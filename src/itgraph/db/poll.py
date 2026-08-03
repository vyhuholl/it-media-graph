"""The poll queue: what is due, and when it is due again.

Timing only. Where collection has got to in a channel stays on
``BackfillState.newest_fetched_id``, which has been recorded since the
backfill change as the mark that incremental collection would read — this
is that reader, and it reads it in place rather than keeping a copy.

The scheduling arithmetic lives in ``itgraph.schedule`` and none of it is
repeated here. This module knows how to ask the database questions and
how to write the answer down; it does not know what a good interval is.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import DateTime, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.backfill import in_scope
from itgraph.db.models import (
    BackfillState,
    Channel,
    PollState,
    RawMessage,
)

__all__ = [
    "DueChannel",
    "QueueLag",
    "count_overdue",
    "due_channels",
    "live_post_dates",
    "measure_posts_per_day",
    "postpone_all",
    "queue_lag",
    "record_poll",
]

logger = logging.getLogger(__name__)

# The message's publication date, out of the stored payload. Written once
# here rather than at each call site: it is the one expression in this
# module that depends on payload shape, and `derive/` owning payload
# shape is a rule worth not eroding by copy-paste.
PUBLISHED_AT = RawMessage.payload["date"].astext.cast(DateTime(timezone=True))
IS_MESSAGE = RawMessage.payload["_"].astext == "Message"

# How far back to measure a channel's rhythm. Long enough that a quiet
# fortnight does not read as a dead channel, short enough that a channel
# which has changed its habits is described by what it does now.
RATE_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class DueChannel:
    """One channel the loop should poll, and what it needs to poll it.

    Plain values rather than a mapped ``Channel``: the loop commits and
    rolls back between channels, both of which expire mapped instances,
    and reading an attribute off an expired one is a lazy load an async
    session cannot perform. The history walk learned this the same way.
    """

    tg_id: int
    username: str | None
    posts_per_day: float | None
    posts_per_day_at: datetime | None
    last_polled_at: datetime | None
    consecutive_empty: int
    consecutive_failures: int
    last_error: str | None
    newest_fetched_id: int | None


@dataclass(frozen=True, slots=True)
class QueueLag:
    """How far behind its own schedule the loop is running.

    Reported rather than logged, because the question is always asked
    about a process nobody was watching. ``oldest_due_at`` is ``None``
    when nothing is overdue, which reads as "on time" rather than as a
    lag of zero.
    """

    overdue: int
    oldest_due_at: datetime | None
    tracked: int


async def due_channels(
    session: AsyncSession, *, now: datetime, limit: int | None = None
) -> Sequence[DueChannel]:
    """Channels that are due, most overdue first.

    A channel with no ``poll_state`` row is due: that is what makes the
    table seed itself, so the first pass over the inventory is the
    seeding pass and nothing has to backfill it.

    Ordered by ``due_at`` with the missing rows first, so a loop that
    cannot get through everything works on what has waited longest rather
    than on whatever the inventory's id order happens to be.
    """
    statement = (
        select(
            Channel.tg_id,
            Channel.username,
            PollState.posts_per_day,
            PollState.posts_per_day_at,
            PollState.last_polled_at,
            PollState.consecutive_empty,
            PollState.consecutive_failures,
            PollState.last_error,
            BackfillState.newest_fetched_id,
        )
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .outerjoin(PollState, PollState.channel_id == Channel.tg_id)
        .where(*in_scope())
        .where(or_(PollState.due_at.is_(None), PollState.due_at <= now))
        .order_by(PollState.due_at.asc().nullsfirst(), Channel.tg_id)
    )
    if limit is not None:
        statement = statement.limit(limit)

    return [
        DueChannel(
            tg_id=row.tg_id,
            username=row.username,
            posts_per_day=row.posts_per_day,
            posts_per_day_at=row.posts_per_day_at,
            last_polled_at=row.last_polled_at,
            consecutive_empty=row.consecutive_empty or 0,
            consecutive_failures=row.consecutive_failures or 0,
            last_error=row.last_error,
            newest_fetched_id=row.newest_fetched_id,
        )
        for row in (await session.execute(statement)).all()
    ]


async def live_post_dates(
    session: AsyncSession, *, channel_id: int, now: datetime
) -> list[datetime]:
    """When each of this channel's still-watched posts was published.

    "Still watched" is the horizon and nothing else — not how many
    snapshots a post already has. The schedule decides what to do with
    these dates; this only reports which posts are inside the window.
    """
    horizon = now - timedelta(hours=settings.watch_horizon_hours)
    statement = (
        select(PUBLISHED_AT)
        .where(
            RawMessage.channel_id == channel_id,
            IS_MESSAGE,
            PUBLISHED_AT >= horizon,
        )
        .order_by(PUBLISHED_AT.desc())
    )
    return list((await session.scalars(statement)).all())


async def measure_posts_per_day(
    session: AsyncSession, *, channel_id: int, now: datetime
) -> float:
    """How often this channel publishes, over the recent window.

    Counted from the raw layer rather than accumulated, for the same
    reason ``count_messages`` is: the rows are the only honest answer,
    and a counter kept anywhere else would drift from them.

    Costs a query and no request, which is why it can be re-measured
    freely — but it is cached on ``poll_state`` all the same, because the
    loop would otherwise ask this of every channel on every tick.
    """
    since = now - timedelta(days=RATE_WINDOW_DAYS)
    total = await session.scalar(
        select(func.count())
        .select_from(RawMessage)
        .where(
            RawMessage.channel_id == channel_id,
            IS_MESSAGE,
            PUBLISHED_AT >= since,
        )
    )
    return (total or 0) / RATE_WINDOW_DAYS


async def record_poll(
    session: AsyncSession,
    channel_id: int,
    *,
    due_at: datetime,
    polled_at: datetime,
    posts_per_day: float | None = None,
    posts_per_day_at: datetime | None = None,
    found_nothing: bool = False,
    error: str | None = None,
) -> None:
    """Write down when this channel was polled and when it is due again.

    The two counters move in opposite directions on purpose. A poll that
    succeeded clears the failure count whether or not it found anything —
    the channel is reachable, which is what that counter is about — while
    the empty count tracks something else entirely and is only cleared by
    actually finding a post.
    """
    values: dict[str, object] = {
        "due_at": due_at,
        "last_polled_at": polled_at,
        "last_error": error,
    }
    if posts_per_day is not None:
        values["posts_per_day"] = posts_per_day
        values["posts_per_day_at"] = posts_per_day_at or polled_at

    if error is not None:
        values["consecutive_failures"] = PollState.consecutive_failures + 1
    else:
        values["consecutive_failures"] = 0
        values["consecutive_empty"] = (
            PollState.consecutive_empty + 1 if found_nothing else 0
        )

    statement = insert(PollState).values(
        channel_id=channel_id,
        due_at=due_at,
        last_polled_at=polled_at,
        posts_per_day=posts_per_day,
        posts_per_day_at=posts_per_day_at or polled_at,
        consecutive_empty=1 if found_nothing and error is None else 0,
        consecutive_failures=1 if error is not None else 0,
        last_error=error,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[PollState.channel_id], set_=values
        )
    )


async def postpone_all(session: AsyncSession, *, until: datetime) -> int:
    """Push every channel's due moment past a moment. Returns how many moved.

    What a rate limit too long to sit through does to the loop. A batch
    job halts on one, because work with no deadline gains nothing by
    holding a connection open; a loop cannot, because running is the
    product. So the schedule slides and the loop keeps going.

    Only moves forward. A channel already due later than ``until`` is
    left where it is: the wait is a floor on when work may resume, not an
    instruction to bring anything nearer.
    """
    moved = await session.scalars(
        update(PollState)
        .where(PollState.due_at < until)
        .values(due_at=until)
        .returning(PollState.channel_id)
    )
    return len(moved.all())


async def count_overdue(session: AsyncSession, *, now: datetime) -> int:
    """How many in-scope channels are past their due moment."""
    statement = (
        select(func.count())
        .select_from(Channel)
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .outerjoin(PollState, PollState.channel_id == Channel.tg_id)
        .where(*in_scope())
        .where(or_(PollState.due_at.is_(None), PollState.due_at <= now))
    )
    return await session.scalar(statement) or 0


async def queue_lag(session: AsyncSession, *, now: datetime) -> QueueLag:
    """The loop's state, as the status command reports it.

    A channel that has never been polled counts as overdue but supplies
    no ``oldest_due_at``: it has waited since the loop first ran, which is
    a different quantity from being late for a scheduled reading, and
    reporting the two as one number would make a fresh install look
    catastrophically behind.
    """
    overdue = await count_overdue(session, now=now)
    oldest = await session.scalar(
        select(func.min(PollState.due_at))
        .select_from(Channel)
        .outerjoin(BackfillState, BackfillState.channel_id == Channel.tg_id)
        .outerjoin(PollState, PollState.channel_id == Channel.tg_id)
        .where(*in_scope())
        .where(PollState.due_at <= now)
    )
    tracked = await session.scalar(select(func.count()).select_from(PollState))
    return QueueLag(
        overdue=overdue, oldest_due_at=oldest, tracked=tracked or 0
    )
