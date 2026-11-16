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

**The second wind** (`add-second-wind`) is stored with its list: its
entries in ``weekly_revivals``, whether it was switched on and the
thresholds on the list. The posts it judges come with their three
readings around the week, each the last at or before a moment, read by
probes on the snapshots' primary key.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import exists, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    Metric,
    WeeklyEntry,
    WeeklyList,
    WeeklyRevival,
)

__all__ = [
    "NewEntry",
    "NewList",
    "NewRevival",
    "PendingEntry",
    "PendingList",
    "PendingRevival",
    "PostMarks",
    "claim_lists",
    "mark_entries_delivered",
    "mark_list_delivered",
    "mark_list_failed",
    "mark_revivals_delivered",
    "posts_with_marks",
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
class NewRevival:
    """One channel in the second wind section, as the pass computed it."""

    rank: int
    channel_id: int
    msg_id: int
    before_at: datetime
    start_at: datetime
    end_at: datetime
    previous_pace: float | None
    pace: float
    norm: float
    others: int


@dataclass(frozen=True, slots=True)
class NewList:
    """A week's list, as the pass computed it, with its second wind."""

    week_start: date
    computed_at: datetime
    threshold: float
    size: int
    comments_min: int
    posts_scored: int
    channels_above: int
    entries: tuple[NewEntry, ...]
    revivals: tuple[NewRevival, ...] = ()
    second_wind_shown: bool = False
    second_wind_acceleration: float | None = None
    second_wind_norm_share: float | None = None
    second_wind_size: int | None = None


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
                second_wind_shown=found.second_wind_shown,
                second_wind_acceleration=found.second_wind_acceleration,
                second_wind_norm_share=found.second_wind_norm_share,
                second_wind_size=found.second_wind_size,
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
            session.add_all(
                WeeklyRevival(
                    list_id=row.id,
                    rank=revival.rank,
                    channel_id=revival.channel_id,
                    msg_id=revival.msg_id,
                    before_at=revival.before_at,
                    start_at=revival.start_at,
                    end_at=revival.end_at,
                    previous_pace=revival.previous_pace,
                    pace=revival.pace,
                    norm=revival.norm,
                    others=revival.others,
                )
                for revival in found.revivals
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
class PendingRevival:
    """A second wind entry of a claimed list, not yet sent."""

    rank: int
    channel_id: int
    msg_id: int
    previous_pace: float | None
    pace: float
    norm: float
    others: int


@dataclass(frozen=True, slots=True)
class PendingList:
    """A list the bot has claimed, with the entries still to send.

    ``sent`` is how many of its entries already went, in an earlier part,
    and ``revivals_sent`` how many of its second wind entries. The second
    wind is sent only when ``second_wind_shown``: a list computed while
    the section was switched on.
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
    second_wind_shown: bool = False
    revivals: tuple[PendingRevival, ...] = ()
    revivals_sent: int = 0


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
        revivals = (
            await session.scalars(
                select(WeeklyRevival)
                .where(WeeklyRevival.list_id == found.id)
                .order_by(WeeklyRevival.rank)
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
                second_wind_shown=found.second_wind_shown,
                revivals=tuple(
                    PendingRevival(
                        rank=revival.rank,
                        channel_id=revival.channel_id,
                        msg_id=revival.msg_id,
                        previous_pace=revival.previous_pace,
                        pace=revival.pace,
                        norm=revival.norm,
                        others=revival.others,
                    )
                    for revival in revivals
                    if revival.delivered_at is None
                ),
                revivals_sent=sum(
                    revival.delivered_at is not None for revival in revivals
                ),
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


async def mark_revivals_delivered(
    session: AsyncSession, list_id: int, ranks: Sequence[int], *, at: datetime
) -> None:
    """Record that these second wind entries went out."""
    if not ranks:
        return
    await session.execute(
        update(WeeklyRevival)
        .where(WeeklyRevival.list_id == list_id, WeeklyRevival.rank.in_(ranks))
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


type Mark = tuple[datetime, float]


@dataclass(frozen=True, slots=True)
class PostMarks:
    """A post the second wind judges, with its three readings' views.

    Each reading is the last at or before its moment, or ``None``.
    """

    channel_id: int
    msg_id: int
    album: int | None
    published: datetime
    before: Mark | None
    start: Mark | None
    end: Mark | None


# Three probes per post on `message_metrics`' primary key, each the last
# reading with views at or before a moment.
_PROBE = """
    LEFT JOIN LATERAL (
        SELECT m.observed_at, m.views FROM message_metrics m
        WHERE m.channel_id = r.channel_id AND m.msg_id = r.msg_id
          AND m.observed_at <= :{moment} AND m.views IS NOT NULL
        ORDER BY m.observed_at DESC LIMIT 1
    ) {name} ON true
"""

POSTS_WITH_MARKS = f"""
    SELECT r.channel_id, r.msg_id,
           (r.payload->>'grouped_id')::bigint AS album,
           (r.payload->>'date')::timestamptz AS published,
           p.observed_at, p.views, a.observed_at, a.views,
           z.observed_at, z.views
    FROM raw_messages r
    JOIN channels c ON c.tg_id = r.channel_id
    {_PROBE.format(moment="before", name="p")}
    {_PROBE.format(moment="start", name="a")}
    {_PROBE.format(moment="end", name="z")}
    WHERE r.payload->>'_' = 'Message'
      AND c.status = 'seed'
      AND c.is_chat = false
      AND (r.payload->>'date')::timestamptz >= :since
      AND (r.payload->>'date')::timestamptz < :until
"""


async def posts_with_marks(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    before: datetime,
    start: datetime,
    end: datetime,
) -> list[PostMarks]:
    """Seeds' posts published between two moments, with three readings.

    Every message, an album's parts and a burst's included: which of them
    stands for the post is the scoring's rule to apply, not this query's.
    """
    rows = await session.execute(
        text(POSTS_WITH_MARKS),
        {
            "since": since,
            "until": until,
            "before": before,
            "start": start,
            "end": end,
        },
    )

    def mark(at: datetime | None, views: int | None) -> Mark | None:
        return None if at is None or views is None else (at, float(views))

    return [
        PostMarks(
            channel_id=channel_id,
            msg_id=msg_id,
            album=album,
            published=published,
            before=mark(p_at, p_views),
            start=mark(a_at, a_views),
            end=mark(z_at, z_views),
        )
        for (
            channel_id,
            msg_id,
            album,
            published,
            p_at,
            p_views,
            a_at,
            a_views,
            z_at,
            z_views,
        ) in rows.tuples()
    ]
