"""Which channels to walk, how far each got, and why one stopped.

Every write here is small and deliberate, because the cursor is the one
piece of state that must never run ahead of the rows it describes: a
cursor past the last stored message is a window of history that no later
run will ever ask for again.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    BackfillState,
    BackfillStatus,
    Channel,
    ChannelStatus,
    FailureKind,
)

__all__ = [
    "channels_in_scope",
    "load_state",
    "record_complete",
    "record_failure",
    "record_progress",
    "record_skip",
    "start_channel",
    "was_completed_to",
]


async def channels_in_scope(session: AsyncSession) -> Sequence[Channel]:
    """The channels a backfill run may touch, and no others.

    Three predicates, and never a fourth: the channel was reviewed and
    accepted, it is not a discussion chat, and no previous run found it
    permanently gone. Widening this is how a collector starts reading
    something nobody agreed to collect.

    Ordered by id so a run interrupted and restarted covers the same
    channels in the same order, which is what makes ``--limit`` mean
    anything across runs.
    """
    statement = (
        select(Channel)
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .where(
            Channel.status == ChannelStatus.SEED,
            Channel.is_chat.is_(False),
            BackfillState.failure_kind.is_distinct_from(FailureKind.PERMANENT),
        )
        .order_by(Channel.tg_id)
    )
    return (await session.scalars(statement)).all()


async def load_state(
    session: AsyncSession, channel_id: int
) -> BackfillState | None:
    return await session.get(BackfillState, channel_id)


def was_completed_to(state: BackfillState | None, cutoff: datetime) -> bool:
    """Whether a previous run already reached this far back.

    Re-running with the same cutoff skips; re-running with an earlier one
    resumes. Without the stored cutoff, deepening the window would either
    silently do nothing or re-fetch everything.
    """
    if state is None or state.status is not BackfillStatus.COMPLETE:
        return False
    return state.cutoff_at is not None and state.cutoff_at <= cutoff


async def _upsert_state(
    session: AsyncSession, channel_id: int, **values: object
) -> None:
    statement = insert(BackfillState).values(channel_id=channel_id, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[BackfillState.channel_id],
            set_={key: statement.excluded[key] for key in values}
            | {"updated_at": datetime.now(UTC)},
        )
    )


async def start_channel(session: AsyncSession, channel_id: int) -> None:
    """Mark a channel as being walked right now.

    A ``running`` row left behind by a killed process needs no repair:
    resumption reads the cursor, not the status. It is what tells the
    operator afterwards which channel was interrupted.
    """
    await _upsert_state(
        session,
        channel_id,
        status=BackfillStatus.RUNNING,
        failure_kind=None,
        failure_detail=None,
    )


async def record_progress(
    session: AsyncSession,
    channel_id: int,
    *,
    oldest_fetched_id: int,
    newest_fetched_id: int | None = None,
) -> None:
    """Advance the cursor.

    The caller commits this together with the batch it describes, in one
    transaction. Split them and a process killed in between leaves a
    cursor past rows that were never stored.
    """
    values: dict[str, object] = {
        "oldest_fetched_id": oldest_fetched_id,
        "status": BackfillStatus.RUNNING,
    }
    if newest_fetched_id is not None:
        values["newest_fetched_id"] = newest_fetched_id
    await _upsert_state(session, channel_id, **values)


async def record_complete(
    session: AsyncSession, channel_id: int, cutoff: datetime
) -> None:
    """Record how far back this channel is now known to be complete."""
    await _upsert_state(
        session,
        channel_id,
        status=BackfillStatus.COMPLETE,
        cutoff_at=cutoff,
        failure_kind=None,
        failure_detail=None,
    )


async def record_skip(
    session: AsyncSession, channel_id: int, detail: str
) -> None:
    """A channel that was not walked, and not because anything failed."""
    await _upsert_state(
        session,
        channel_id,
        status=BackfillStatus.SKIPPED,
        failure_detail=detail,
    )


async def record_failure(
    session: AsyncSession,
    channel_id: int,
    kind: FailureKind,
    detail: str,
) -> None:
    """Record why a channel stopped, and whether to try it again.

    Everything else about the channel is retained either way: one that
    went private is still a node in the graph, and still has whatever
    history was collected before it did.
    """
    await _upsert_state(
        session,
        channel_id,
        status=BackfillStatus.FAILED,
        failure_kind=kind,
        # Truncated: a driver traceback in a status column helps nobody,
        # and the log has the whole thing.
        failure_detail=detail[:500],
    )
