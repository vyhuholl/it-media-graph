"""The weekly list: what took off on YouTube in a week, per language.

A week is Monday to Monday in the readers' zone, and a video belongs to the
week of its publication. The pass lists the last week that has ended, or
the one named, once: a week that has its lists is not computed again.

**Scored as the live pass saw it.** Each reading is scored against the
baseline run that was newest when it was taken — the run the live pass,
fifteen minutes behind every reading, scored it against — through that
pass's own preparing and best-per-video. The week's readings are cut at
each run's completion and each cut scored against its run; a video's
score is its best across the cuts. No run is refitted, and today's run,
which holds the week's own videos, is not used for readings it did not
see. A reading taken before any run is not scored, and is counted.

**One entry per channel.** A video is above the floor when its best score
reaches ``youtube_weekly_list_z``; a channel is listed by its best such
video, and the others are counted. Channels are ranked by that score, a
tie going to the earlier crossing and then to the video id, and the list
holds ``youtube_weekly_list_size`` of them, or fewer — never padded from
below the floor. Shorts and long-form compete.

**The language is the label when the list is computed.** The list is that
language's, and does not move between topics once it exists.

It makes no request and books no quota; it writes nothing but the list
tables, and with a preview nothing at all.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from itgraph.config import settings
from itgraph.db.models import YtAlertRoute, YtLang, YtMetric, YtVideoFormat
from itgraph.db.session import Database
from itgraph.db.yt_baselines import (
    RunInForce,
    YtBaselines,
    YtVideo,
    load_languages,
    load_scope,
    load_videos,
    load_yt_baselines,
    runs_in_force,
)
from itgraph.db.yt_weekly import NewEntry, NewList, store_week, week_has_lists
from itgraph.scoring.score import Best
from itgraph.scoring.yt_run import best_per_video, prepare_readings

__all__ = [
    "WeekRefused",
    "WeeklyRoute",
    "WeeklySummary",
    "check_week",
    "last_ended_week",
    "rank_week",
    "run_weekly",
    "score_week",
    "week_bounds",
]

logger = logging.getLogger(__name__)

WEEK = timedelta(days=7)

# The routes a list is computed for, and the label each stands for.
ROUTES = {YtLang.EN: YtAlertRoute.EN, YtLang.RU: YtAlertRoute.RU}


class WeekRefused(ValueError):
    """A week that cannot be listed: not a Monday, or not yet ended."""


def week_bounds(week_start: date, *, zone: str) -> tuple[datetime, datetime]:
    """The week's first moment and the next week's, in UTC."""
    tz = ZoneInfo(zone)
    start = datetime.combine(week_start, time(), tzinfo=tz)
    end = datetime.combine(week_start + WEEK, time(), tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def last_ended_week(moment: datetime, *, zone: str) -> date:
    """The Monday of the last week that had ended by ``moment``."""
    local = moment.astimezone(ZoneInfo(zone)).date()
    this_monday = local - timedelta(days=local.weekday())
    return this_monday - WEEK


def check_week(week_start: date, *, moment: datetime, zone: str) -> None:
    """Refuse a week that is not a Monday's, or has not ended by ``moment``."""
    if week_start.weekday() != 0:
        raise WeekRefused(f"{week_start} is not a Monday")
    _, end = week_bounds(week_start, zone=zone)
    if end > moment:
        raise WeekRefused(f"the week of {week_start} has not ended")


@dataclass(frozen=True, slots=True)
class Scored:
    """A week's videos scored: each one's best, and what was left out."""

    best: dict[tuple[str, str], Best]
    formats: dict[str, YtVideoFormat]
    videos: int
    broadcasts: int
    unreadable: int
    unscored_readings: int


def _merge(found: Best, current: Best | None) -> Best:
    """The better of two bests of one video, crossing at the earlier."""
    if current is None:
        return found
    crossings = [
        moment
        for moment in (found.crossed_at, current.crossed_at)
        if moment is not None
    ]
    better = found if found.score.z > current.score.z else current
    return Best(
        score=better.score,
        crossed_at=min(crossings) if crossings else None,
        scored_at=better.scored_at,
    )


def score_week(
    videos: Iterable[YtVideo],
    runs: Sequence[tuple[RunInForce, YtBaselines]],
    *,
    until: datetime,
    floor: float,
    comments_min: int,
) -> Scored:
    """Each video's best, every reading against the run in force when taken.

    ``runs`` oldest first. A run is in force from its completion to the
    next one's, the last up to ``until``; readings before the first are
    not scored, and are counted.
    """
    videos = list(videos)
    everything = prepare_readings(videos, until=until)
    first = runs[0][0].completed_at if runs else until
    unscored = len(prepare_readings(videos, until=min(first, until)).readings)

    best: dict[tuple[str, str], Best] = {}
    for index, (run, baselines) in enumerate(runs):
        ends = (
            runs[index + 1][0].completed_at if index + 1 < len(runs) else until
        )
        cut = prepare_readings(
            videos, since=run.completed_at, until=min(ends, until)
        )
        if not cut.readings:
            continue
        for key, found in best_per_video(
            cut, baselines, threshold=floor, comments_min=comments_min
        ).items():
            best[key] = _merge(found, best.get(key))

    return Scored(
        best=best,
        formats=everything.formats,
        videos=everything.videos,
        broadcasts=everything.broadcasts,
        unreadable=everything.unreadable,
        unscored_readings=unscored,
    )


@dataclass(frozen=True, slots=True)
class Standing:
    """A channel's best video of the week, and how many more reached the floor."""

    channel_id: str
    video_id: str
    best: Best
    others: int


def _order(video_id: str, best: Best) -> tuple[float, datetime, str]:
    crossed = best.crossed_at or datetime.max.replace(tzinfo=UTC)
    return (-best.score.z, crossed, video_id)


def rank_week(
    best: Mapping[tuple[str, str], Best], *, floor: float
) -> list[Standing]:
    """Every channel with a video at or above the floor, best first."""
    above: dict[str, list[tuple[str, Best]]] = {}
    for (channel, video), found in best.items():
        if found.score.z >= floor:
            above.setdefault(channel, []).append((video, found))
    standings = []
    for channel, videos in above.items():
        video, top = min(videos, key=lambda pair: _order(*pair))
        standings.append(
            Standing(
                channel_id=channel,
                video_id=video,
                best=top,
                others=len(videos) - 1,
            )
        )
    standings.sort(
        key=lambda standing: _order(standing.video_id, standing.best)
    )
    return standings


@dataclass(frozen=True, slots=True)
class WeeklyRoute:
    """One language's list, as computed."""

    route: YtAlertRoute
    videos_scored: int
    videos_above: int
    standings: list[Standing]
    entries: list[NewEntry]

    @property
    def channels_above(self) -> int:
        return len(self.standings)


@dataclass(frozen=True, slots=True)
class WeeklySummary:
    """What a run of the pass computed, and whether it wrote it."""

    week_start: date
    now: datetime
    floor: float
    size: int
    videos: int = 0
    broadcasts: int = 0
    unreadable: int = 0
    unscored_readings: int = 0
    runs: int = 0
    routes: list[WeeklyRoute] = field(default_factory=list)
    formats: dict[str, YtVideoFormat] = field(default_factory=dict)
    # Whether the lists were stored: False for a preview, and for a week
    # that already had its lists.
    stored: bool = False
    already: bool = False

    def line(self) -> str:
        if self.already:
            return (
                f"the week of {self.week_start} already has its lists; "
                "nothing written"
            )
        parts = [
            f"{route.route.value}: {len(route.entries)} listed of "
            f"{route.channels_above} channel(s) and {route.videos_above} "
            f"video(s) at z {self.floor:g} or more, "
            f"{route.videos_scored} video(s) scored"
            for route in self.routes
        ]
        line = (
            f"week of {self.week_start}: " + "; ".join(parts) + f"; "
            f"{self.videos} video(s) published, against {self.runs} "
            "baseline run(s)"
        )
        if self.broadcasts or self.unreadable:
            line += (
                f"; not scored: {self.broadcasts} broadcast(s), "
                f"{self.unreadable} without a readable length"
            )
        if self.unscored_readings:
            line += (
                f"; {self.unscored_readings} reading(s) taken before any "
                "baseline run, not scored"
            )
        if not self.stored:
            line += "; preview, nothing written"
        return line

    def preview(self) -> list[str]:
        """Each list by ids, one line an entry."""
        lines: list[str] = []
        for route in self.routes:
            lines.append(f"{route.route.value}:")
            if not route.entries:
                lines.append("  nothing at or above the floor")
            for entry in route.entries:
                kind = self.formats.get(entry.video_id)
                lines.append(
                    f"  {entry.rank:>2}. z {entry.value:5.2f} "
                    f"{entry.metric.value:<8} "
                    f"{kind.value if kind else '?':<5} "
                    f"{entry.video_id} {entry.channel_id}"
                    + (f" +{entry.others}" if entry.others else "")
                )
        return lines


def _entries(standings: Sequence[Standing], *, size: int) -> list[NewEntry]:
    return [
        NewEntry(
            rank=rank,
            channel_id=standing.channel_id,
            video_id=standing.video_id,
            scored_at=standing.best.scored_at,
            metric=YtMetric(standing.best.score.metric),
            value=standing.best.score.z,
            expected=standing.best.score.expected,
            others=standing.others,
        )
        for rank, standing in enumerate(standings[:size], start=1)
    ]


async def run_weekly(
    database: Database,
    *,
    week: date | None = None,
    now: datetime | None = None,
    preview: bool = False,
) -> WeeklySummary:
    """Compute a week's lists, store them unless previewing, and report.

    Raises ``WeekRefused`` for a week that cannot be listed, before
    anything is read.
    """
    moment = now or datetime.now(UTC)
    zone = settings.youtube_alert_timezone
    week_start = (
        week if week is not None else last_ended_week(moment, zone=zone)
    )
    check_week(week_start, moment=moment, zone=zone)
    floor = settings.youtube_weekly_list_z
    size = settings.youtube_weekly_list_size
    comments_min = settings.youtube_alert_spike_comments_min
    start, end = week_bounds(week_start, zone=zone)

    async with database.session() as session:
        if not preview and await week_has_lists(session, week_start):
            summary = WeeklySummary(
                week_start=week_start,
                now=moment,
                floor=floor,
                size=size,
                already=True,
            )
            logger.info("%s", summary.line())
            return summary
        kinds = await load_scope(session)
        languages = await load_languages(session)
        loaded = await load_videos(
            session,
            since=start,
            until=moment,
            short_max_seconds=settings.youtube_scoring_short_max_seconds,
        )
        runs: list[tuple[RunInForce, YtBaselines]] = []
        for run in await runs_in_force(session, since=start, until=moment):
            baselines = await load_yt_baselines(
                session, kinds=kinds, run_id=run.run_id
            )
            if baselines is not None:
                runs.append((run, baselines))

    videos = [video for video in loaded.values() if video.published < end]
    scored = score_week(
        videos, runs, until=moment, floor=floor, comments_min=comments_min
    )

    routes: list[WeeklyRoute] = []
    for lang, route in ROUTES.items():
        best = {
            key: found
            for key, found in scored.best.items()
            if languages.get(key[0]) is lang
        }
        standings = rank_week(best, floor=floor)
        routes.append(
            WeeklyRoute(
                route=route,
                videos_scored=len(best),
                videos_above=sum(
                    found.score.z >= floor for found in best.values()
                ),
                standings=standings,
                entries=_entries(standings, size=size),
            )
        )

    stored = False
    if not preview:
        async with database.session() as session:
            stored = await store_week(
                session,
                [
                    NewList(
                        week_start=week_start,
                        route=route.route,
                        computed_at=moment,
                        threshold=floor,
                        size=size,
                        comments_min=comments_min,
                        videos_scored=route.videos_scored,
                        channels_above=route.channels_above,
                        entries=tuple(route.entries),
                    )
                    for route in routes
                ],
            )

    summary = WeeklySummary(
        week_start=week_start,
        now=moment,
        floor=floor,
        size=size,
        videos=scored.videos,
        broadcasts=scored.broadcasts,
        unreadable=scored.unreadable,
        unscored_readings=scored.unscored_readings,
        runs=len(runs),
        routes=routes,
        formats=scored.formats,
        stored=stored,
        already=not preview and not stored,
    )
    logger.info("%s", summary.line())
    return summary
