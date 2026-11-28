"""The record of rate limits: writes, and the questions asked of it.

Kept apart from the code that catches a rate limit, because that code
lives in ``tg/`` and knows about Telethon, while this knows about a
table. What arrives here is already a method name and a duration.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Interval, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import CollectionCommand, FloodEvent

__all__ = [
    "held_until",
    "latest_flood_for_method",
    "store_flood_event",
]


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


async def held_until(
    session: AsyncSession, *, methods: Sequence[str], now: datetime
) -> datetime | None:
    """When the last recorded wait on any of these methods ends, if later
    than ``now``.

    Whichever command recorded it: a limit is the account's, so a wait met
    by the operator's run holds the loop too. Read from the record rather
    than remembered, so a restart inside the wait asks nothing either.
    """
    ends = FloodEvent.occurred_at + func.make_interval(
        0, 0, 0, 0, 0, 0, FloodEvent.seconds, type_=Interval
    )
    latest: datetime | None = await session.scalar(
        select(func.max(ends)).where(
            FloodEvent.method.in_(methods), ends > now
        )
    )
    return latest
