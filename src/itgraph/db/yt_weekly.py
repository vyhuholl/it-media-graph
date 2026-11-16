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

**The second wind** (`add-second-wind`) is stored with its list: its
entries in ``yt_weekly_revivals`` — the moments of their readings and
itgraph's multiples, no count — and whether it was switched on and the
thresholds on the list. The videos it judges come with their three
readings around the week, by probes on the readings' primary key.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import exists, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    YtAlertRoute,
    YtMetric,
    YtRawVideoStat,
    YtVideoFormat,
    YtWeeklyEntry,
    YtWeeklyList,
    YtWeeklyRevival,
)
from itgraph.db.yt_alerts import YtEvidence, video_evidence
from itgraph.derive.yt_videos import format_of

__all__ = [
    "NewEntry",
    "NewList",
    "NewRevival",
    "PendingEntry",
    "PendingList",
    "PendingRevival",
    "RevivalEvidence",
    "VideoMarks",
    "claim_lists",
    "entry_evidence",
    "mark_entries_delivered",
    "mark_list_delivered",
    "mark_list_failed",
    "mark_revivals_delivered",
    "revival_evidence",
    "store_week",
    "videos_with_marks",
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
class NewRevival:
    """One channel in a list's second wind section, as computed.

    The moments of its three readings and itgraph's two multiples; no
    count YouTube reported.
    """

    rank: int
    channel_id: str
    video_id: str
    before_at: datetime
    start_at: datetime
    end_at: datetime
    acceleration: float | None
    share: float
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
    revivals: tuple[NewRevival, ...] = ()
    second_wind_shown: bool = False
    second_wind_acceleration: float | None = None
    second_wind_norm_share: float | None = None
    second_wind_size: int | None = None


async def week_has_lists(session: AsyncSession, week_start: date) -> bool:
    """Whether any list of this week is stored."""
    return bool(
        await session.scalar(
            select(exists().where(YtWeeklyList.week_start == week_start))
        )
    )


# The unique key on a list's week and language, as the revision names it.
WEEK_KEY = "uq_yt_weekly_lists_week"


async def store_week(session: AsyncSession, lists: Sequence[NewList]) -> bool:
    """Write a week's lists whole, in the caller's transaction.

    ``False``, writing nothing, when the week already has a list: a second
    run of the pass for a week is an answer, not an error. That holds for
    two runs at once too — one by hand beside the scheduled one: the
    second meets the unique key inside a savepoint, which is rolled back,
    and is told the same as if it had come later. Any other refusal is
    raised.
    """
    weeks = {found.week_start for found in lists}
    for week in weeks:
        if await week_has_lists(session, week):
            return False
    try:
        async with session.begin_nested():
            for found in lists:
                await _store_list(session, found)
    except IntegrityError as exc:
        if WEEK_KEY not in str(exc.orig):
            raise
        return False
    return True


async def _store_list(session: AsyncSession, found: NewList) -> None:
    row = YtWeeklyList(
        week_start=found.week_start,
        route=found.route,
        computed_at=found.computed_at,
        threshold=found.threshold,
        size=found.size,
        comments_min=found.comments_min,
        videos_scored=found.videos_scored,
        channels_above=found.channels_above,
        second_wind_shown=found.second_wind_shown,
        second_wind_acceleration=found.second_wind_acceleration,
        second_wind_norm_share=found.second_wind_norm_share,
        second_wind_size=found.second_wind_size,
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
    session.add_all(
        YtWeeklyRevival(
            list_id=row.id,
            rank=revival.rank,
            channel_id=revival.channel_id,
            video_id=revival.video_id,
            before_at=revival.before_at,
            start_at=revival.start_at,
            end_at=revival.end_at,
            acceleration=revival.acceleration,
            share=revival.share,
            others=revival.others,
        )
        for revival in found.revivals
    )
    await session.flush()


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
class PendingRevival:
    """A second wind entry of a claimed list, not yet sent."""

    rank: int
    channel_id: str
    video_id: str
    start_at: datetime
    end_at: datetime
    acceleration: float | None
    share: float
    others: int


@dataclass(frozen=True, slots=True)
class PendingList:
    """A list the bot has claimed, with the entries still to send.

    ``sent`` is how many of its entries already went, in an earlier part:
    a list whose first part was sent and whose second failed resumes with
    the second, and says it is a continuation. ``revivals_sent`` counts
    its second wind entries alike; the second wind is sent only when
    ``second_wind_shown``.
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
    second_wind_shown: bool = False
    revivals: tuple[PendingRevival, ...] = ()
    revivals_sent: int = 0


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
        revivals = (
            await session.scalars(
                select(YtWeeklyRevival)
                .where(YtWeeklyRevival.list_id == found.id)
                .order_by(YtWeeklyRevival.rank)
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
                second_wind_shown=found.second_wind_shown,
                revivals=tuple(
                    PendingRevival(
                        rank=revival.rank,
                        channel_id=revival.channel_id,
                        video_id=revival.video_id,
                        start_at=revival.start_at,
                        end_at=revival.end_at,
                        acceleration=revival.acceleration,
                        share=revival.share,
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
        update(YtWeeklyEntry)
        .where(
            YtWeeklyEntry.list_id == list_id,
            YtWeeklyEntry.rank.in_(ranks),
        )
        .values(delivered_at=at)
    )


async def mark_revivals_delivered(
    session: AsyncSession, list_id: int, ranks: Sequence[int], *, at: datetime
) -> None:
    """Record that these second wind entries went out."""
    if not ranks:
        return
    await session.execute(
        update(YtWeeklyRevival)
        .where(
            YtWeeklyRevival.list_id == list_id,
            YtWeeklyRevival.rank.in_(ranks),
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


@dataclass(frozen=True, slots=True)
class RevivalEvidence:
    """What a second wind entry shows of its video, read at sending.

    ``evidence`` is the video as an entry shows it, scored at the end of
    its week; ``start_views`` and ``end_views`` YouTube's views at the two
    readings, ``None`` where it reported none.
    """

    evidence: YtEvidence
    start_views: int | None
    end_views: int | None


async def revival_evidence(
    session: AsyncSession, revival: PendingRevival
) -> RevivalEvidence | None:
    """A second wind entry's video and its two readings, or ``None``."""
    evidence = await video_evidence(
        session, video_id=revival.video_id, scored_at=revival.end_at
    )
    if evidence is None:
        return None
    start_views = await session.scalar(
        select(YtRawVideoStat.views).where(
            YtRawVideoStat.video_id == revival.video_id,
            YtRawVideoStat.observed_at == revival.start_at,
        )
    )
    return RevivalEvidence(
        evidence=evidence,
        start_views=start_views,
        end_views=evidence.scored.views,
    )


type Mark = tuple[datetime, float]


@dataclass(frozen=True, slots=True)
class VideoMarks:
    """A video the second wind judges, with its three readings' views."""

    channel_id: str
    video_id: str
    format: YtVideoFormat | None
    published: datetime
    before: Mark | None
    start: Mark | None
    end: Mark | None


_PROBE = """
    LEFT JOIN LATERAL (
        SELECT s.observed_at, s.views FROM yt_raw_video_stats s
        WHERE s.video_id = v.video_id
          AND s.observed_at <= :{moment} AND s.views IS NOT NULL
        ORDER BY s.observed_at DESC LIMIT 1
    ) {name} ON true
"""

# The videos of channels in scope — seeds labelled `en` or `ru` with a
# kind, as scoring's scope is — with what their format is read from, and
# no more of their payload.
VIDEOS_WITH_MARKS = f"""
    SELECT v.channel_id, v.video_id,
           (v.payload#>>'{{snippet,publishedAt}}')::timestamptz AS published,
           jsonb_strip_nulls(jsonb_build_object(
               'contentDetails', v.payload->'contentDetails',
               'liveStreamingDetails', v.payload->'liveStreamingDetails'
           )) AS shape,
           p.observed_at, p.views, a.observed_at, a.views,
           z.observed_at, z.views
    FROM yt_raw_videos v
    JOIN yt_channels c ON c.channel_id = v.channel_id
    {_PROBE.format(moment="before", name="p")}
    {_PROBE.format(moment="start", name="a")}
    {_PROBE.format(moment="end", name="z")}
    WHERE c.status = 'seed'
      AND c.lang IN ('en', 'ru')
      AND c.kind IS NOT NULL
      AND (v.payload#>>'{{snippet,publishedAt}}')::timestamptz >= :since
      AND (v.payload#>>'{{snippet,publishedAt}}')::timestamptz < :until
"""


async def videos_with_marks(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    before: datetime,
    start: datetime,
    end: datetime,
    short_max_seconds: int,
) -> list[VideoMarks]:
    """Videos in scope published between two moments, with three readings.

    The format is worked out on reading, as scoring's is, and never
    stored; which format is judged is the caller's to say.
    """
    rows = await session.execute(
        text(VIDEOS_WITH_MARKS),
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

    found: list[VideoMarks] = []
    for row in rows.tuples():
        channel_id, video_id, published, shape = row[:4]
        p_at, p_views, a_at, a_views, z_at, z_views = row[4:]
        payload: dict[str, Any] = shape or {}
        found.append(
            VideoMarks(
                channel_id=channel_id,
                video_id=video_id,
                format=format_of(payload, short_max_seconds=short_max_seconds),
                published=published,
                before=mark(p_at, p_views),
                start=mark(a_at, a_views),
                end=mark(z_at, z_views),
            )
        )
    return found
