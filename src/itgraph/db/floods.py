"""The record of rate limits: writes, and the two questions asked of it.

Kept apart from the code that catches a rate limit, because that code
lives in ``tg/`` and knows about Telethon, while this knows about a
table. What arrives here is already a method name and a duration.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import CollectionCommand, FloodEvent

__all__ = [
    "MethodTally",
    "flood_summary",
    "latest_flood_for_method",
    "recent_floods",
    "store_flood_event",
]


@dataclass(frozen=True, slots=True)
class MethodTally:
    """How often one method was limited, and how badly."""

    method: str
    times: int
    longest: int
    latest: datetime


async def store_flood_event(
    session: AsyncSession,
    *,
    method: str,
    seconds: int,
    command: CollectionCommand,
    channel_id: int | None,
    halted: bool,
) -> None:
    """Insert one event. The caller decides which session this runs on."""
    session.add(
        FloodEvent(
            method=method,
            seconds=seconds,
            command=command,
            channel_id=channel_id,
            halted=halted,
        )
    )
    await session.flush()


async def recent_floods(
    session: AsyncSession, *, since: datetime | None = None, limit: int = 50
) -> list[FloodEvent]:
    """The events themselves, newest first."""
    statement = select(FloodEvent).order_by(FloodEvent.occurred_at.desc())
    if since is not None:
        statement = statement.where(FloodEvent.occurred_at >= since)
    return list(await session.scalars(statement.limit(limit)))


async def latest_flood_for_method(
    session: AsyncSession, *, method: str, since: datetime
) -> FloodEvent | None:
    """The newest rate limit recorded for one method since a cutoff.

    For a command that is about to spend that method and wants to say so
    before it starts. Deliberately returns the event rather than a
    verdict: the table records which *run* was limited and not which
    account was behind it, so nothing here can decide whether a past
    limit still applies to the caller. Reporting the fact and leaving the
    judgement is the most this can honestly do.
    """
    statement = (
        select(FloodEvent)
        .where(FloodEvent.method == method, FloodEvent.occurred_at >= since)
        .order_by(FloodEvent.occurred_at.desc())
        .limit(1)
    )
    return (await session.scalars(statement)).first()


async def flood_summary(
    session: AsyncSession, *, since: datetime | None = None
) -> list[MethodTally]:
    """Per-method counts — the shape that answers "again, or something new".

    Ordered by how often a method was limited, because that is the column
    a reader is scanning for.
    """
    statement = select(
        FloodEvent.method,
        func.count().label("times"),
        func.max(FloodEvent.seconds).label("longest"),
        func.max(FloodEvent.occurred_at).label("latest"),
    ).group_by(FloodEvent.method)
    if since is not None:
        statement = statement.where(FloodEvent.occurred_at >= since)

    rows: Any = await session.execute(
        statement.order_by(func.count().desc(), FloodEvent.method)
    )
    return [
        MethodTally(
            method=row.method,
            times=row.times,
            longest=row.longest,
            latest=row.latest,
        )
        for row in rows
    ]
