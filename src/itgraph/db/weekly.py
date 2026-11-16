"""Telegram's weekly lists: storing a week's, and what the bot reads and marks.

A week's list is written whole, or not at all when the week already has
one: the unique key on the week says so, and a second run of the pass is
told rather than refused.

The bot's side is YouTube's lists' discipline: claim the undelivered
lists, oldest week first, with their undelivered entries in rank order;
send; mark what was sent. An entry whose reading is gone went with it,
and is simply not among them.

A list holds ids, moments and itgraph's numbers, and the count each score
came from; what a message shows of a post — its text, its channel's name
— is read at sending, by ``db.alerts.alert_evidence``, as an alert's is.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import Metric, WeeklyEntry, WeeklyList

__all__ = [
    "NewEntry",
    "NewList",
    "PendingEntry",
    "PendingList",
    "claim_lists",
    "mark_entries_delivered",
    "mark_list_delivered",
    "mark_list_failed",
    "store_week",
    "week_has_list",
]


@dataclass(frozen=True, slots=True)
class NewEntry:
    """One channel on the list, as the pass computed it."""

    rank: int
    channel_id: int
    msg_id: int
    scored_at: datetime
    metric: Metric
    value: float
    observed: float
    expected: float
    others: int


@dataclass(frozen=True, slots=True)
class NewList:
    """A week's list, as the pass computed it."""

    week_start: date
    computed_at: datetime
    threshold: float
    size: int
    comments_min: int
    posts_scored: int
    channels_above: int
    entries: tuple[NewEntry, ...]


async def week_has_list(session: AsyncSession, week_start: date) -> bool:
    """Whether this week's list is stored."""
    return bool(
        await session.scalar(
            select(exists().where(WeeklyList.week_start == week_start))
        )
    )


# The unique key on a list's week, as the revision names it.
WEEK_KEY = "uq_weekly_lists_week"


async def store_week(session: AsyncSession, found: NewList) -> bool:
    """Write a week's list whole, in the caller's transaction.

    ``False``, writing nothing, when the week already has its list. Two
    runs at once — one by hand beside the scheduled one — end the same
    way: the second meets the unique key inside a savepoint, which is
    rolled back. Any other refusal is raised.
    """
    if await week_has_list(session, found.week_start):
        return False
    try:
        async with session.begin_nested():
            row = WeeklyList(
                week_start=found.week_start,
                computed_at=found.computed_at,
                threshold=found.threshold,
                size=found.size,
                comments_min=found.comments_min,
                posts_scored=found.posts_scored,
                channels_above=found.channels_above,
            )
            session.add(row)
            await session.flush()
            session.add_all(
                WeeklyEntry(
                    list_id=row.id,
                    rank=entry.rank,
                    channel_id=entry.channel_id,
                    msg_id=entry.msg_id,
                    scored_at=entry.scored_at,
                    metric=entry.metric,
                    value=entry.value,
                    observed=entry.observed,
                    expected=entry.expected,
                    others=entry.others,
                )
                for entry in found.entries
            )
            await session.flush()
    except IntegrityError as exc:
        if WEEK_KEY not in str(exc.orig):
            raise
        return False
    return True


@dataclass(frozen=True, slots=True)
class PendingEntry:
    """An entry of a list the bot has claimed, not yet sent."""

    rank: int
    channel_id: int
    msg_id: int
    metric: Metric
    value: float
    observed: float
    expected: float
    others: int


@dataclass(frozen=True, slots=True)
class PendingList:
    """A list the bot has claimed, with the entries still to send.

    ``sent`` is how many of its entries already went, in an earlier part.
    """

    id: int
    week_start: date
    computed_at: datetime
    threshold: float
    posts_scored: int
    channels_above: int
    attempts: int
    entries: tuple[PendingEntry, ...]
    sent: int


async def claim_lists(session: AsyncSession) -> list[PendingList]:
    """The undelivered lists, oldest week first, locked as alerts are."""
    lists = (
        await session.scalars(
            select(WeeklyList)
            .where(WeeklyList.delivered_at.is_(None))
            .order_by(WeeklyList.week_start, WeeklyList.id)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claimed: list[PendingList] = []
    for found in lists:
        entries = (
            await session.scalars(
                select(WeeklyEntry)
                .where(WeeklyEntry.list_id == found.id)
                .order_by(WeeklyEntry.rank)
            )
        ).all()
        claimed.append(
            PendingList(
                id=found.id,
                week_start=found.week_start,
                computed_at=found.computed_at,
                threshold=found.threshold,
                posts_scored=found.posts_scored,
                channels_above=found.channels_above,
                attempts=found.attempts,
                entries=tuple(
                    PendingEntry(
                        rank=entry.rank,
                        channel_id=entry.channel_id,
                        msg_id=entry.msg_id,
                        metric=entry.metric,
                        value=entry.value,
                        observed=entry.observed,
                        expected=entry.expected,
                        others=entry.others,
                    )
                    for entry in entries
                    if entry.delivered_at is None
                ),
                sent=sum(entry.delivered_at is not None for entry in entries),
            )
        )
    return claimed


async def mark_entries_delivered(
    session: AsyncSession, list_id: int, ranks: Sequence[int], *, at: datetime
) -> None:
    """Record that these entries went out, in a part of their list."""
    if not ranks:
        return
    await session.execute(
        update(WeeklyEntry)
        .where(WeeklyEntry.list_id == list_id, WeeklyEntry.rank.in_(ranks))
        .values(delivered_at=at)
    )


async def mark_list_delivered(
    session: AsyncSession, list_id: int, *, at: datetime
) -> None:
    """Record that the whole list went out."""
    await session.execute(
        update(WeeklyList)
        .where(WeeklyList.id == list_id)
        .values(delivered_at=at, last_error=None)
    )


async def mark_list_failed(
    session: AsyncSession, list_id: int, *, error: str
) -> None:
    """Record a failed send, leaving the list undelivered."""
    await session.execute(
        update(WeeklyList)
        .where(WeeklyList.id == list_id)
        .values(attempts=WeeklyList.attempts + 1, last_error=error[:500])
    )
