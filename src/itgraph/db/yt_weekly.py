"""The weekly lists: storing a week's, and what the bot reads and marks.

A week's lists — one per language — are written together, or not at all
when the week already has them: the unique key on week and language says
so, and a second run of the pass is told rather than refused.

The bot's side is the alert queue's discipline at the size of a list:
claim the undelivered lists of a route, oldest week first, with their
undelivered entries in rank order; send; mark what was sent. An entry
whose reading is gone went with it, and is simply not among them.

Nothing here copies what YouTube said: a list holds ids, moments and
itgraph's numbers, and what a message shows of a video is read at
sending, through ``video_evidence``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    YtAlertRoute,
    YtMetric,
    YtWeeklyEntry,
    YtWeeklyList,
)
from itgraph.db.yt_alerts import YtEvidence, video_evidence

__all__ = [
    "NewEntry",
    "NewList",
    "PendingEntry",
    "PendingList",
    "claim_lists",
    "entry_evidence",
    "mark_entries_delivered",
    "mark_list_delivered",
    "mark_list_failed",
    "store_week",
    "week_has_lists",
]


@dataclass(frozen=True, slots=True)
class NewEntry:
    """One channel on a list, as the pass computed it."""

    rank: int
    channel_id: str
    video_id: str
    scored_at: datetime
    metric: YtMetric
    value: float
    expected: float
    others: int


@dataclass(frozen=True, slots=True)
class NewList:
    """One language's list of a week, as the pass computed it."""

    week_start: date
    route: YtAlertRoute
    computed_at: datetime
    threshold: float
    size: int
    comments_min: int
    videos_scored: int
    channels_above: int
    entries: tuple[NewEntry, ...]


async def week_has_lists(session: AsyncSession, week_start: date) -> bool:
    """Whether any list of this week is stored."""
    return bool(
        await session.scalar(
            select(exists().where(YtWeeklyList.week_start == week_start))
        )
    )


async def store_week(session: AsyncSession, lists: Sequence[NewList]) -> bool:
    """Write a week's lists whole, in the caller's transaction.

    ``False``, writing nothing, when the week already has a list: a second
    run of the pass for a week is an answer, not an error. Two runs at
    once would meet the unique key, and the second fail.
    """
    weeks = {found.week_start for found in lists}
    for week in weeks:
        if await week_has_lists(session, week):
            return False
    for found in lists:
        row = YtWeeklyList(
            week_start=found.week_start,
            route=found.route,
            computed_at=found.computed_at,
            threshold=found.threshold,
            size=found.size,
            comments_min=found.comments_min,
            videos_scored=found.videos_scored,
            channels_above=found.channels_above,
        )
        session.add(row)
        await session.flush()
        session.add_all(
            YtWeeklyEntry(
                list_id=row.id,
                rank=entry.rank,
                channel_id=entry.channel_id,
                video_id=entry.video_id,
                scored_at=entry.scored_at,
                metric=entry.metric,
                value=entry.value,
                expected=entry.expected,
                others=entry.others,
            )
            for entry in found.entries
        )
    await session.flush()
    return True


@dataclass(frozen=True, slots=True)
class PendingEntry:
    """An entry of a list the bot has claimed, not yet sent."""

    rank: int
    channel_id: str
    video_id: str
    scored_at: datetime
    metric: YtMetric
    value: float
    expected: float
    others: int


@dataclass(frozen=True, slots=True)
class PendingList:
    """A list the bot has claimed, with the entries still to send.

    ``sent`` is how many of its entries already went, in an earlier part:
    a list whose first part was sent and whose second failed resumes with
    the second, and says it is a continuation.
    """

    id: int
    week_start: date
    route: YtAlertRoute
    computed_at: datetime
    threshold: float
    videos_scored: int
    channels_above: int
    attempts: int
    entries: tuple[PendingEntry, ...]
    sent: int


async def claim_lists(
    session: AsyncSession, route: YtAlertRoute
) -> list[PendingList]:
    """The undelivered lists of one route, oldest week first.

    Locked as the alerts are, on the lists alone; the entries are read
    under them.
    """
    lists = (
        await session.scalars(
            select(YtWeeklyList)
            .where(
                YtWeeklyList.delivered_at.is_(None),
                YtWeeklyList.route == route,
            )
            .order_by(YtWeeklyList.week_start, YtWeeklyList.id)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claimed: list[PendingList] = []
    for found in lists:
        entries = (
            await session.scalars(
                select(YtWeeklyEntry)
                .where(YtWeeklyEntry.list_id == found.id)
                .order_by(YtWeeklyEntry.rank)
            )
        ).all()
        claimed.append(
            PendingList(
                id=found.id,
                week_start=found.week_start,
                route=found.route,
                computed_at=found.computed_at,
                threshold=found.threshold,
                videos_scored=found.videos_scored,
                channels_above=found.channels_above,
                attempts=found.attempts,
                entries=tuple(
                    PendingEntry(
                        rank=entry.rank,
                        channel_id=entry.channel_id,
                        video_id=entry.video_id,
                        scored_at=entry.scored_at,
                        metric=entry.metric,
                        value=entry.value,
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
        update(YtWeeklyEntry)
        .where(
            YtWeeklyEntry.list_id == list_id,
            YtWeeklyEntry.rank.in_(ranks),
        )
        .values(delivered_at=at)
    )


async def mark_list_delivered(
    session: AsyncSession, list_id: int, *, at: datetime
) -> None:
    """Record that the whole list went out."""
    await session.execute(
        update(YtWeeklyList)
        .where(YtWeeklyList.id == list_id)
        .values(delivered_at=at, last_error=None)
    )


async def mark_list_failed(
    session: AsyncSession, list_id: int, *, error: str
) -> None:
    """Record a failed send, leaving the list undelivered."""
    await session.execute(
        update(YtWeeklyList)
        .where(YtWeeklyList.id == list_id)
        .values(attempts=YtWeeklyList.attempts + 1, last_error=error[:500])
    )


async def entry_evidence(
    session: AsyncSession, entry: PendingEntry, *, short_max_seconds: int
) -> YtEvidence | None:
    """An entry's video as its message shows it, read now, or ``None``."""
    return await video_evidence(
        session,
        video_id=entry.video_id,
        scored_at=entry.scored_at,
        short_max_seconds=short_max_seconds,
    )
