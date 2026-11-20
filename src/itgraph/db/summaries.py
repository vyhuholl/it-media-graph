"""The daily summaries' markers: which day a destination has been sent.

The bot's own table, written by the bot alone. A row is opened when a
day's summary begins going out and closed once every part has; an open
row is a summary interrupted part-way, and the delivery pass resends the
whole day rather than finish half of it — at-least-once, the guarantee
every send here has. See ``bot/summary.py`` for the pass and the day it
closes.
"""

from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import BotDailySummary

__all__ = ["close_summary", "is_sent", "open_summary"]


async def is_sent(session: AsyncSession, destination: str, day: date) -> bool:
    """Whether the destination's summary of this day has gone out whole.

    An open row — a summary interrupted part-way — is not sent, and the
    next pass resends the day.
    """
    sent_at = await session.scalar(
        select(BotDailySummary.sent_at).where(
            BotDailySummary.destination == destination,
            BotDailySummary.day == day,
        )
    )
    return sent_at is not None


async def open_summary(
    session: AsyncSession, destination: str, day: date
) -> None:
    """Record that this day's summary is going out now.

    Absorbed silently if a row is already there — the interrupted
    previous attempt's — so a resend takes the same row rather than a
    second one the unique constraint would refuse.
    """
    await session.execute(
        pg_insert(BotDailySummary)
        .values(destination=destination, day=day)
        .on_conflict_do_nothing()
    )


async def close_summary(
    session: AsyncSession,
    destination: str,
    day: date,
    *,
    at: datetime,
) -> None:
    """Record that every part of this day's summary has gone.

    The write that makes the day done: until it, a restart resends the
    whole summary, duplicates and all, which is the accepted cost of not
    keeping per-part state for a recap.
    """
    await session.execute(
        update(BotDailySummary)
        .where(
            BotDailySummary.destination == destination,
            BotDailySummary.day == day,
        )
        .values(sent_at=at)
    )
