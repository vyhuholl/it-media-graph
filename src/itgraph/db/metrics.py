"""Writes into the metric snapshot layer.

Nothing here reads a payload and nothing here decides anything, the same
rule ``db/raw.py`` follows. A snapshot is an observation; what it means
is a question for a later pass that must stay re-runnable over these
rows.

The ordering constraint this module participates in is worth stating
where it is easy to break: **payloads first, snapshots second, one
transaction.** The foreign key onto ``raw_messages`` enforces it, so
getting it wrong is a database error rather than a silent inconsistency —
but the error arrives at the write, and the reason for it lives here.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import MessageMetric
from itgraph.derive.metrics import Counters

__all__ = ["count_snapshots", "latest_observation", "store_metrics"]


async def store_metrics(
    session: AsyncSession,
    *,
    channel_id: int,
    observed_at: datetime,
    counters: dict[int, Counters],
) -> int:
    """Record one reading of several messages. Returns how many were new.

    One ``observed_at`` for the whole batch, passed in rather than taken
    per row. That is not a convenience: the rows came from a single
    history response, so they *were* all read at one moment, and stamping
    them individually would invent a spread that the measurement does not
    have.

    ``ON CONFLICT DO NOTHING`` on the primary key. Two readings inside
    one clock tick are one row rather than a crash — the loop has no
    reason to poll a channel twice that fast, but a retry after a
    partially-committed transaction could, and losing a duplicate
    observation costs nothing while raising would cost the batch.
    """
    if not counters:
        return 0

    rows: list[dict[str, Any]] = [
        {
            "channel_id": channel_id,
            "msg_id": msg_id,
            "observed_at": observed_at,
            "views": reading.views,
            "forwards": reading.forwards,
            "reactions": reading.reactions,
            "comments": reading.comments,
        }
        for msg_id, reading in counters.items()
    ]

    statement = insert(MessageMetric).values(rows)
    result = await session.execute(
        statement.on_conflict_do_nothing(
            index_elements=[
                MessageMetric.channel_id,
                MessageMetric.msg_id,
                MessageMetric.observed_at,
            ]
        ).returning(MessageMetric.msg_id)
    )
    return len(result.all())


async def count_snapshots(
    session: AsyncSession, *, since: datetime | None = None
) -> int:
    """How many snapshots exist, optionally only the recent ones.

    Read by the status command. A count rather than a rate, because the
    window it is taken over belongs to whoever is asking.
    """
    statement = select(func.count()).select_from(MessageMetric)
    if since is not None:
        statement = statement.where(MessageMetric.observed_at >= since)
    return await session.scalar(statement) or 0


async def latest_observation(
    session: AsyncSession, *, channel_id: int, msg_id: int
) -> datetime | None:
    """When one message was last read, or ``None`` if it never was."""
    statement = select(func.max(MessageMetric.observed_at)).where(
        MessageMetric.channel_id == channel_id,
        MessageMetric.msg_id == msg_id,
    )
    observed: datetime | None = await session.scalar(statement)
    return observed
