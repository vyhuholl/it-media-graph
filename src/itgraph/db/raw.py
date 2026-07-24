"""Writes into the raw layer.

Nothing here reads a payload, and nothing here may start. Every field the
collector stores is interpreted later, from these tables, by code that
must stay re-runnable — that is the whole reason the raw layer exists.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import RawChannel, RawMessage

__all__ = ["count_messages", "store_channel_payload", "store_messages"]


async def count_messages(session: AsyncSession, channel_id: int) -> int:
    """How many messages the raw layer already holds for one channel.

    Read before a walk, not accumulated across runs: the rows are the
    only honest answer to "how much of this channel do we have", and a
    counter kept anywhere else would drift from them.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(RawMessage)
        .where(RawMessage.channel_id == channel_id)
    )
    return total or 0


async def store_messages(
    session: AsyncSession,
    *,
    channel_id: int,
    payloads: dict[int, dict[str, Any]],
) -> int:
    """Insert a batch of messages. Returns how many were new.

    The first fetch of a message id wins and later ones are dropped:
    edits and changing view counts are a time series, which is a
    different table on a different cadence, and pretending otherwise
    would let a re-run quietly rewrite collected history.

    Keyed by message id because Postgres refuses to let one ``ON
    CONFLICT`` statement touch the same row twice, and a batch can carry
    the same id if a walk overlaps itself.
    """
    if not payloads:
        return 0

    fetched_at = datetime.now(UTC)
    statement = insert(RawMessage).values(
        [
            {
                "channel_id": channel_id,
                "msg_id": msg_id,
                "payload": payload,
                "fetched_at": fetched_at,
            }
            for msg_id, payload in payloads.items()
        ]
    )
    result = await session.execute(
        statement.on_conflict_do_nothing(
            index_elements=[RawMessage.channel_id, RawMessage.msg_id]
        ).returning(RawMessage.msg_id)
    )
    return len(result.all())


async def store_channel_payload(
    session: AsyncSession, *, channel_id: int, payload: dict[str, Any]
) -> None:
    """Record the latest extended information for one channel.

    The freshest payload wins, unlike a message: a description and a
    linked discussion chat change over time, and what a reader wants is
    the current state. Keeping every version would be a time series, and
    that is a different table on a different cadence.
    """
    statement = insert(RawChannel).values(
        channel_id=channel_id,
        payload=payload,
        fetched_at=datetime.now(UTC),
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[RawChannel.channel_id],
            set_={
                "payload": statement.excluded.payload,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
    )
