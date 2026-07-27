"""Which channels a networked pass may touch, how far each got, and why
one stopped.

Every write here is small and deliberate, because the cursor is the one
piece of state that must never run ahead of the rows it describes: a
cursor past the last stored message is a window of history that no later
run will ever ask for again.

The scope predicates are shared rather than restated. Two passes now
select over the inventory — the history walk and the metadata pass — and
they must agree on what is in scope down to the last condition. A second
copy of "accepted, not a chat, not permanently gone" is how a collector
eventually starts reading something nobody agreed to collect.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    BackfillState,
    BackfillStatus,
    Channel,
    ChannelStatus,
    FailureKind,
    RawChannel,
)

__all__ = [
    "channels_in_scope",
    "channels_needing_metadata",
    "count_deferred_chats",
    "count_stale_metadata",
    "load_state",
    "record_complete",
    "record_failure",
    "record_progress",
    "record_skip",
    "start_channel",
    "was_completed_to",
]


def _in_scope() -> tuple[ColumnElement[bool], ...]:
    """What every networked pass over the inventory is allowed to touch.

    Three predicates, and never a fourth: the channel was reviewed and
    accepted, it is not a discussion chat, and no previous run found it
    permanently gone. Returned rather than written out at each call site
    so the two passes cannot drift apart — the failure mode is silent and
    the thing it lets through is collection nobody consented to.

    The ``BackfillState`` condition assumes the caller has joined that
    table; both callers below do.
    """
    return (
        Channel.status == ChannelStatus.SEED,
        Channel.is_chat.is_(False),
        BackfillState.failure_kind.is_distinct_from(FailureKind.PERMANENT),
    )


def _metadata_is_due(max_age: timedelta) -> ColumnElement[bool]:
    """Extended information that is absent or past its freshness window.

    Absent and stale are the same decision but not the same fact, which
    is why they are spelled out separately rather than collapsed into a
    coalesce: a channel nothing has ever asked about reads differently
    from one whose description has gone quiet for a year.
    """
    return or_(
        RawChannel.fetched_at.is_(None),
        RawChannel.fetched_at < datetime.now(UTC) - max_age,
    )


async def channels_in_scope(session: AsyncSession) -> Sequence[Channel]:
    """The channels a backfill run may touch, and no others.

    Ordered by id so a run interrupted and restarted covers the same
    channels in the same order, which is what makes ``--limit`` mean
    anything across runs.
    """
    statement = (
        select(Channel)
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .where(*_in_scope())
        .order_by(Channel.tg_id)
    )
    return (await session.scalars(statement)).all()


async def channels_needing_metadata(
    session: AsyncSession,
    *,
    max_age: timedelta,
    limit: int | None = None,
    refresh: bool = False,
) -> Sequence[Channel]:
    """In-scope channels whose extended information is absent or stale.

    ``metadata_age`` answers this one channel at a time, which is the
    right shape for a walk deciding about the channel in front of it and
    the wrong shape for a pass that has to know its whole queue before it
    spends the first request.

    ``refresh`` drops the freshness condition and returns everything in
    scope — the operator saying they know the stored payloads are wrong.

    Ordered by id for the same reason as ``channels_in_scope``: a bounded
    run has to mean something across sittings.
    """
    statement = (
        select(Channel)
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .outerjoin(RawChannel, RawChannel.channel_id == Channel.tg_id)
        .where(*_in_scope())
        .order_by(Channel.tg_id)
    )
    if not refresh:
        statement = statement.where(_metadata_is_due(max_age))
    if limit is not None:
        statement = statement.limit(limit)
    return (await session.scalars(statement)).all()


async def count_stale_metadata(
    session: AsyncSession, *, max_age: timedelta
) -> int:
    """How many in-scope channels are waiting on the metadata pass.

    Read by a backfill run, which no longer fetches any of it. Counting
    costs one query and no request, and it is the whole answer to the one
    real objection to a separate command: that an operator will forget it
    exists.
    """
    statement = (
        select(func.count())
        .select_from(Channel)
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .outerjoin(RawChannel, RawChannel.channel_id == Channel.tg_id)
        .where(*_in_scope(), _metadata_is_due(max_age))
    )
    return await session.scalar(statement) or 0


async def count_deferred_chats(session: AsyncSession) -> int:
    """Accepted chats that belong to no channel, and so are left alone.

    ``channels_in_scope`` excludes every chat, which is right for a
    discussion chat — its parent channel is what was reviewed. A chat
    accepted on its own is a different thing: somebody looked at it and
    said yes, and it is not walked only because reading a community chat
    is not built yet. Counting them is what keeps that decision visible
    instead of letting the run look complete while a reviewed chat sits
    untouched.
    """
    statement = (
        select(func.count())
        .select_from(Channel)
        .where(
            Channel.status == ChannelStatus.SEED,
            Channel.is_chat.is_(True),
            Channel.linked_to.is_(None),
        )
    )
    return await session.scalar(statement) or 0


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
