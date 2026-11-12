"""The YouTube scoring pass: load, score, raise, report — and its replay.

Telegram's pass (``scoring/run.py``) for videos. It reads the newest
complete baseline run and the readings of every scored video published in
the last eight and a half days, scores each reading in a band on views,
likes and comments, takes each video's best, folds a channel's spikes into
one event per day, and writes alerts. It makes no request to YouTube,
books no quota and needs no key; it changes nothing but ``yt_alerts``.

**The replay is the same scoring at earlier moments.** Unlike Telegram's,
which scores a period against the current run, it refits the baselines in
memory as of the start of each day it scores, with the refresh's own code,
and scores that day's readings against them: with weeks of data, a curve
fitted on the days it scores would be most of the fit. It writes nothing.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from itgraph.config import settings
from itgraph.db.models import YtChannelKind, YtLang, YtMetric, YtVideoFormat
from itgraph.db.session import Database
from itgraph.db.yt_alerts import (
    open_yt_heads,
    raise_yt_spikes,
    raised_yt_spikes,
)
from itgraph.db.yt_baselines import (
    YtBaselines,
    YtVideo,
    load_languages,
    load_scope,
    load_videos,
    load_yt_baselines,
)
from itgraph.scoring.curves import YOUTUBE_BANDS
from itgraph.scoring.fold import Placed, fold
from itgraph.scoring.score import (
    Baseline,
    Best,
    Reading,
    Spike,
    best_per_post,
    score_metric,
)
from itgraph.scoring.yt_refresh import YtParams, fit_youtube

__all__ = [
    "HELD_AGE",
    "HELD_MULTIPLE",
    "Outcome",
    "Prepared",
    "YtReplay",
    "YtSpikeSummary",
    "best_per_video",
    "prepare_readings",
    "replay_yt_scoring",
    "run_yt_scoring",
    "sweep_yt_scoring",
]

logger = logging.getLogger(__name__)

# How far back a pass looks for videos: the last band's upper edge, so every
# band a video can be scored in is reachable.
WINDOW = YOUTUBE_BANDS.last_edge


@dataclass(frozen=True, slots=True)
class Prepared:
    """A pass's readings, and what it left out and why."""

    readings: list[Reading[str, str]]
    formats: dict[str, YtVideoFormat]
    videos: int
    broadcasts: int
    unreadable: int


def prepare_readings(
    videos: Iterable[YtVideo],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Prepared:
    """Every reading of a scored video, between two moments if given.

    A broadcast and a video whose length cannot be read are counted and
    left out. Age is the reading's moment minus the publication, never
    which sample of the schedule it was meant to be.
    """
    readings: list[Reading[str, str]] = []
    formats: dict[str, YtVideoFormat] = {}
    total = broadcasts = unreadable = 0
    for video in videos:
        total += 1
        if video.format is None:
            if video.broadcast:
                broadcasts += 1
            else:
                unreadable += 1
            continue
        formats[video.video_id] = video.format
        for observed, counters in video.readings:
            if since is not None and observed < since:
                continue
            if until is not None and observed >= until:
                continue
            age = observed - video.published
            if age < timedelta(0):
                continue
            readings.append(
                Reading(
                    post_key=(video.channel_id, video.video_id),
                    age=age,
                    values={
                        YtMetric.VIEWS: counters.views,
                        YtMetric.LIKES: counters.likes,
                        YtMetric.COMMENTS: counters.comments,
                    },
                    observed_at=observed,
                )
            )
    return Prepared(
        readings=readings,
        formats=formats,
        videos=total,
        broadcasts=broadcasts,
        unreadable=unreadable,
    )


def best_per_video(
    prepared: Prepared,
    baselines: YtBaselines,
    *,
    threshold: float,
    comments_min: int | None = None,
) -> dict[tuple[str, str], Best]:
    """Each video's best score against one run, and its first crossing.

    The live pass, the replay and the weekly list all score through this,
    on the alerts' signals and with the comment minimum set aside first,
    so the three agree on any reading scored against the same run.
    """

    def baseline_for(
        reading: Reading[str, str], metric: str
    ) -> Baseline | None:
        channel, video = reading.post_key
        return baselines.for_video(
            channel, prepared.formats[video], YtMetric(metric)
        )

    return best_per_post(
        prepared.readings,
        baseline_for,
        metrics=tuple(settings.youtube_alert_spike_metrics),
        threshold=threshold,
        minimum={
            YtMetric.COMMENTS: (
                comments_min
                if comments_min is not None
                else settings.youtube_alert_spike_comments_min
            )
        },
    )


def _spikes(best: Mapping[tuple[str, str], Best]) -> list[Spike[str, str]]:
    spikes = [
        Spike(
            post_key=key,
            score=entry.score,
            crossed_at=entry.crossed_at,
            scored_at=entry.scored_at,
        )
        for key, entry in best.items()
        if entry.crossed_at is not None
    ]
    spikes.sort(key=lambda spike: spike.score.z, reverse=True)
    return spikes


@dataclass(frozen=True, slots=True)
class YtSpikeSummary:
    """What a pass found, and what it was able to look at."""

    now: datetime
    threshold: float
    videos: int = 0
    broadcasts: int = 0
    unreadable: int = 0
    scored: int = 0
    crossed: int = 0
    raised: int = 0
    folded: int = 0
    channels_in_scope: int = 0
    channels_with_baseline: int = 0
    channels_on_fallback: int = 0
    baseline_age: timedelta | None = None
    spikes: list[Spike[str, str]] = field(default_factory=list)
    placed: list[Placed[str, str]] = field(default_factory=list)

    def line(self) -> str:
        if self.baseline_age is None:
            return (
                "no YouTube baselines: nothing was scored — run "
                "`itgraph yt baselines`"
            )
        line = (
            f"{self.videos} video(s) in the window, {self.scored} scored, "
            f"{self.crossed} past z {self.threshold:g}, raised {self.raised}"
        )
        if self.folded:
            line += f" ({self.folded} folded into a head of the same channel)"
        if self.broadcasts or self.unreadable:
            line += (
                f"; not scored: {self.broadcasts} broadcast(s), "
                f"{self.unreadable} without a readable length"
            )
        unscored = self.channels_in_scope - self.channels_with_baseline
        if unscored > 0:
            line += (
                f"; {unscored} of {self.channels_in_scope} channel(s) have "
                "too little history to be scored"
            )
        if self.channels_on_fallback:
            line += (
                f"; {self.channels_on_fallback} scored against their settled "
                "videos, too quiet for a reference of their own"
            )
        if self.baseline_age > timedelta(
            days=settings.youtube_baseline_refresh_days
        ):
            line += (
                f"; baselines are {self.baseline_age.days}d old — run "
                "`itgraph yt baselines`"
            )
        return line


async def run_yt_scoring(
    database: Database,
    *,
    now: datetime | None = None,
    threshold: float | None = None,
) -> YtSpikeSummary:
    """Score the window's videos, raise what crossed, and report.

    With no baselines it raises nothing and says so, rather than scoring
    against defaults nobody chose.
    """
    moment = now or datetime.now(UTC)
    limit = (
        threshold if threshold is not None else settings.youtube_alert_spike_z
    )
    fold_window = timedelta(hours=settings.youtube_alert_spike_fold_hours)

    async with database.session() as session:
        kinds = await load_scope(session)
        baselines = await load_yt_baselines(session, kinds=kinds)
        if baselines is None:
            summary = YtSpikeSummary(now=moment, threshold=limit)
            logger.warning("%s", summary.line())
            return summary
        videos = await load_videos(
            session,
            since=moment - WINDOW,
            until=moment,
            short_max_seconds=settings.youtube_scoring_short_max_seconds,
        )

    prepared = prepare_readings(videos.values())
    best = best_per_video(prepared, baselines, threshold=limit)
    spikes = _spikes(best)

    written = folded = 0
    placed: list[Placed[str, str]] = []
    if spikes:
        # Reading the queue and writing to it in one transaction, so the
        # heads folded against are the heads that exist when the folds are
        # written.
        async with database.session() as session:
            raised = await raised_yt_spikes(
                session, (spike.post_key[1] for spike in spikes)
            )
            heads = await open_yt_heads(session, since=moment - fold_window)
            fresh = [
                spike
                for spike in spikes
                if (
                    YtMetric(spike.score.metric).alert_kind(),
                    spike.post_key[1],
                )
                not in raised
            ]
            placed = fold(fresh, heads=heads, window=fold_window)
            written, folded = await raise_yt_spikes(session, placed)

    summary = YtSpikeSummary(
        now=moment,
        threshold=limit,
        videos=prepared.videos,
        broadcasts=prepared.broadcasts,
        unreadable=prepared.unreadable,
        scored=len(best),
        crossed=len(spikes),
        raised=written,
        folded=folded,
        channels_in_scope=len(kinds),
        channels_with_baseline=baselines.scored_channels,
        channels_on_fallback=baselines.fallback_channels,
        baseline_age=moment - baselines.computed_at,
        spikes=spikes,
        placed=placed,
    )
    logger.info("%s", summary.line())
    return summary


# What a crossing is held to a few days on: its newest reading at least three
# days old still at least twice what its channel's videos of that age and
# format reach. Whether a video "took off" is otherwise a matter of opinion;
# this is a measurement — a crossing that held is one the channel's audience
# kept up, and one that faded was the counter catching up, a burst that died,
# or a band whose spread was too narrow. A reading aid for pricing the
# threshold in a replay, and not a setting: nothing live reads it.
HELD_AGE = timedelta(days=3)
HELD_MULTIPLE = 2.0


class Outcome(StrEnum):
    """What became of a crossing, by its newest reading in the replay."""

    HELD = "held"
    FADED = "faded"
    EARLY = "too early"


@dataclass(frozen=True, slots=True)
class YtReplayDay:
    """One replayed day, per language: crossings, heads and folds."""

    day: date
    lang: YtLang
    crossed: int = 0
    heads: int = 0
    folded: int = 0


@dataclass(frozen=True, slots=True)
class YtReplay:
    """What a replay would have raised, and how it would have folded."""

    since: datetime
    now: datetime
    threshold: float
    comments_min: int
    # Whether each day's fit took settled readings taken after it.
    late_settled: bool
    days: list[YtReplayDay]
    spikes: list[Spike[str, str]]
    placed: list[Placed[str, str]]
    languages: dict[str, YtLang]
    formats: dict[str, YtVideoFormat]
    # Each crossing's fate, by (metric, video).
    outcomes: dict[tuple[str, str], Outcome]
    # Hours between one channel's consecutive crossings: what the fold
    # window is measured on.
    gaps: list[float]
    channels_with_baseline: int
    channels_on_fallback: int
    channels_in_scope: int

    def outcome(self, spike: Spike[str, str]) -> Outcome:
        return self.outcomes[(spike.score.metric, spike.post_key[1])]

    def per_language(self) -> dict[YtLang, tuple[int, int, int]]:
        """(crossed, heads, folded) over the whole period, per language."""
        totals: dict[YtLang, tuple[int, int, int]] = {}
        for entry in self.days:
            crossed, heads, folded = totals.get(entry.lang, (0, 0, 0))
            totals[entry.lang] = (
                crossed + entry.crossed,
                heads + entry.heads,
                folded + entry.folded,
            )
        return totals

    def held_per_language(self) -> dict[YtLang, dict[Outcome, int]]:
        """How many crossings held, faded, or are too young to say."""
        totals: dict[YtLang, dict[Outcome, int]] = {}
        for spike in self.spikes:
            lang = self.languages.get(spike.post_key[0], YtLang.OTHER)
            counts = totals.setdefault(lang, dict.fromkeys(Outcome, 0))
            counts[self.outcome(spike)] += 1
        return totals

    def per_signal(self) -> dict[tuple[str, YtVideoFormat], int]:
        """Crossings per signal and format."""
        counts: dict[tuple[str, YtVideoFormat], int] = {}
        for spike in self.spikes:
            key = (spike.score.metric, self.formats[spike.post_key[1]])
            counts[key] = counts.get(key, 0) + 1
        return counts

    def summary_lines(self) -> list[str]:
        """The period at this threshold: volume, and how much of it held."""
        span = max((self.now - self.since) / timedelta(days=1), 1e-9)
        lines = [
            (
                f"replay of {span:g} day(s) at z {self.threshold:g}, "
                f"comments from {self.comments_min}"
                f"{', settled readings taken later' if self.late_settled else ''}: "
                f"{len(self.spikes)} crossing(s); "
                f"{self.channels_with_baseline} of {self.channels_in_scope} "
                "channel(s) scored on the last day, "
                f"{self.channels_on_fallback} of them on settled videos alone"
            )
        ]
        held = self.held_per_language()
        for lang, (crossed, heads, folded) in sorted(
            self.per_language().items()
        ):
            fate = held.get(lang, dict.fromkeys(Outcome, 0))
            lines.append(
                f"  {lang}: {heads} message(s), {heads / span:.2f} a day; "
                f"{crossed} crossing(s), {folded} folded; "
                f"held {fate[Outcome.HELD]}, faded {fate[Outcome.FADED]}, "
                f"too early {fate[Outcome.EARLY]}"
            )
        for (metric, video_format), count in sorted(self.per_signal().items()):
            lines.append(f"  {metric} {video_format}: {count}")
        buckets = ((6, "<6h"), (12, "6–12h"), (24, "12–24h"), (48, "24–48h"))
        if self.gaps:
            counts = []
            low = 0.0
            for high, label in buckets:
                counts.append(
                    f"{label} {sum(1 for gap in self.gaps if low <= gap < high)}"
                )
                low = high
            counts.append(f">48h {sum(1 for gap in self.gaps if gap >= 48)}")
            lines.append(
                "  gaps between one channel's crossings: " + ", ".join(counts)
            )
        return lines

    def lines(self) -> list[str]:
        """The summary, and each day per language."""
        return self.summary_lines() + [
            f"  {entry.day} {entry.lang}: {entry.heads} message(s), "
            f"{entry.crossed} crossing(s), {entry.folded} folded"
            for entry in self.days
        ]


def _day_starts(
    since: datetime, until: datetime, zone: ZoneInfo
) -> list[datetime]:
    """The starts of the local days that overlap [since, until)."""
    local = since.astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    starts: list[datetime] = []
    while start < until:
        starts.append(start)
        # Wall-clock arithmetic in the zone: the next local midnight.
        start = start + timedelta(days=1)
    return starts


@dataclass(frozen=True, slots=True)
class _Day:
    """One replayed day: its start, the fit as of then, and its readings."""

    start: datetime
    baselines: YtBaselines
    prepared: Prepared


@dataclass(frozen=True, slots=True)
class _Period:
    """Everything a replay at any threshold needs, fitted once."""

    since: datetime
    moment: datetime
    late_settled: bool
    days: list[_Day]
    kinds: dict[str, YtChannelKind]
    languages: dict[str, YtLang]
    # Each video's newest scored reading per signal, as (age, multiple of
    # its expectation), each scored against the fit of its own day.
    latest: dict[tuple[str, str], tuple[timedelta, float]]


async def _period(
    database: Database, *, days: float, now: datetime, late_settled: bool
) -> _Period:
    since = now - timedelta(days=days)
    params = YtParams.from_settings()
    zone = ZoneInfo(settings.youtube_alert_timezone)
    starts = _day_starts(since, now, zone)

    async with database.session() as session:
        kinds = await load_scope(session)
        languages = await load_languages(session)
        videos = await load_videos(
            session,
            since=(starts[0] if starts else since)
            - max(params.lookback, WINDOW),
            until=now,
            short_max_seconds=params.short_max_seconds,
        )

    replayed: list[_Day] = []
    latest: dict[tuple[str, str], tuple[timedelta, float]] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else now
        day = _Day(
            start=start,
            baselines=fit_youtube(
                videos,
                kinds,
                start,
                params,
                settled_until=now if late_settled else None,
            ).baselines(kinds),
            prepared=prepare_readings(
                videos.values(), since=max(start, since), until=end
            ),
        )
        replayed.append(day)
        for reading in sorted(
            day.prepared.readings, key=lambda entry: entry.observed_at
        ):
            channel, video = reading.post_key
            for metric in settings.youtube_alert_spike_metrics:
                score = score_metric(
                    metric,
                    reading.values.get(metric),
                    reading.age,
                    day.baselines.for_video(
                        channel, day.prepared.formats[video], metric
                    ),
                )
                if score is not None:
                    latest[(metric, video)] = (
                        reading.age,
                        score.times_expected,
                    )
    return _Period(
        since=since,
        moment=now,
        late_settled=late_settled,
        days=replayed,
        kinds=kinds,
        languages=languages,
        latest=latest,
    )


def _outcome(found: tuple[timedelta, float] | None) -> Outcome:
    if found is None or found[0] < HELD_AGE:
        return Outcome.EARLY
    return Outcome.HELD if found[1] >= HELD_MULTIPLE else Outcome.FADED


def _replay(
    period: _Period, *, threshold: float, comments_min: int
) -> YtReplay:
    """One threshold's replay of a fitted period."""
    spikes: dict[tuple[str, str], Spike[str, str]] = {}
    formats: dict[str, YtVideoFormat] = {}
    per_day: dict[tuple[date, YtLang], list[Spike[str, str]]] = {}
    for day in period.days:
        formats.update(day.prepared.formats)
        best = best_per_video(
            day.prepared,
            day.baselines,
            threshold=threshold,
            comments_min=comments_min,
        )
        for spike in _spikes(best):
            key = (spike.score.metric, spike.post_key[1])
            if key in spikes:
                continue
            spikes[key] = spike
            lang = period.languages.get(spike.post_key[0], YtLang.OTHER)
            per_day.setdefault((day.start.date(), lang), []).append(spike)

    ordered = sorted(
        spikes.values(), key=lambda spike: spike.score.z, reverse=True
    )
    window = timedelta(hours=settings.youtube_alert_spike_fold_hours)
    placed = fold(ordered, heads={}, window=window)
    into = {
        (entry.spike.score.metric, entry.spike.post_key[1]): entry.into
        for entry in placed
    }

    days_report = [
        YtReplayDay(
            day=day,
            lang=lang,
            crossed=len(found),
            heads=sum(
                1
                for spike in found
                if into[(spike.score.metric, spike.post_key[1])] is None
            ),
            folded=sum(
                1
                for spike in found
                if into[(spike.score.metric, spike.post_key[1])] is not None
            ),
        )
        for (day, lang), found in sorted(per_day.items())
    ]

    crossings: dict[str, list[datetime]] = {}
    for spike in ordered:
        crossings.setdefault(spike.post_key[0], []).append(spike.crossed_at)
    gaps = [
        (later - earlier) / timedelta(hours=1)
        for moments in crossings.values()
        for earlier, later in zip(
            sorted(moments), sorted(moments)[1:], strict=False
        )
    ]

    last = period.days[-1].baselines if period.days else None
    return YtReplay(
        since=period.since,
        now=period.moment,
        threshold=threshold,
        comments_min=comments_min,
        late_settled=period.late_settled,
        days=days_report,
        spikes=ordered,
        placed=placed,
        languages=period.languages,
        formats=formats,
        outcomes={key: _outcome(period.latest.get(key)) for key in spikes},
        gaps=gaps,
        channels_with_baseline=last.scored_channels if last else 0,
        channels_on_fallback=last.fallback_channels if last else 0,
        channels_in_scope=len(period.kinds),
    )


async def sweep_yt_scoring(
    database: Database,
    *,
    days: float,
    thresholds: Sequence[float],
    now: datetime | None = None,
    comments_min: int | None = None,
    late_settled: bool = False,
) -> list[YtReplay]:
    """What the passes of the last ``days`` would have raised, at each of
    several thresholds; writes nothing.

    Each day's readings are scored against the baselines the refresh would
    have fitted at that day's start, in memory — fitted once, whatever the
    number of thresholds. A video is raised once per signal, at its first
    crossing, as the live pass raises it; the whole period is folded in the
    order things crossed, from no queue. Each crossing is then followed to
    its newest reading, to say whether it held.

    ``late_settled`` lets each day's fit take settled readings taken after
    it, up to the end of the period (``fit_youtube``'s ``settled_until``):
    how days before the past uploads were first read are scored at all. The
    report says when it was used.
    """
    period = await _period(
        database,
        days=days,
        now=now or datetime.now(UTC),
        late_settled=late_settled,
    )
    minimum = (
        comments_min
        if comments_min is not None
        else settings.youtube_alert_spike_comments_min
    )
    return [
        _replay(period, threshold=threshold, comments_min=minimum)
        for threshold in thresholds
    ]


async def replay_yt_scoring(
    database: Database,
    *,
    days: float,
    now: datetime | None = None,
    threshold: float | None = None,
    comments_min: int | None = None,
    late_settled: bool = False,
) -> YtReplay:
    """The replay at one threshold, the configured one unless given."""
    [found] = await sweep_yt_scoring(
        database,
        days=days,
        now=now,
        thresholds=[
            threshold
            if threshold is not None
            else settings.youtube_alert_spike_z
        ],
        comments_min=comments_min,
        late_settled=late_settled,
    )
    return found
