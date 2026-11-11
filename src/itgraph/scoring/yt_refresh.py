"""YouTube's baseline refresh: fit what normal is, and write it down.

Telegram's refresh (``scoring/refresh.py``) under YouTube's bands and
settings, with one more dimension: the format. A channel's Shorts and its
talks are two references, and a kind's short-form and long-form videos two
curves, because they are watched differently.

``fit_youtube`` is pure — loaded videos and a moment in, references and
fits out — and is the refresh's whole arithmetic. The refresh runs it at
the present and stores the result; the replay runs it in memory as of each
day it scores, so it scores against what the refresh of that day would have
stored.

Per format and metric:

- **a channel's own reference** is the median day-old value of its newest
  videos of that format (the mean, zeros kept, for comments);
- **its settled reference**, where it has too few of its own, is the median
  of its settled videos — one reading each, the latest inside the settled
  window — scaled by its kind's factor; comments take none, as on Telegram;
- **the curve, factor and spreads** are fitted per kind, borrowed from the
  format's pool where a kind is too thin.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from itgraph.config import settings
from itgraph.db.models import (
    BaselineReference,
    YtChannelKind,
    YtMetric,
    YtVideoFormat,
)
from itgraph.db.session import Database
from itgraph.db.yt_baselines import (
    MetricFit,
    YtBaselines,
    YtVideo,
    load_scope,
    load_videos,
    store_run,
)
from itgraph.derive.yt_videos import Counters
from itgraph.scoring.curves import (
    YOUTUBE_BANDS,
    Curve,
    Observation,
    fit_count_curve,
    fit_curve,
    median,
)
from itgraph.scoring.fit import (
    Fit,
    Floors,
    RecentRule,
    Reference,
    fit,
    rolling,
)
from itgraph.scoring.score import COUNTED

__all__ = [
    "YtFit",
    "YtParams",
    "YtRefreshSummary",
    "fit_youtube",
    "refresh_yt_baselines",
]

logger = logging.getLogger(__name__)

type Key = tuple[YtChannelKind, YtVideoFormat, YtMetric]


@dataclass(frozen=True, slots=True)
class YtParams:
    """Everything a fit is taken under, read from the settings once."""

    window: timedelta
    mature_days: int
    mature_max_days: int
    min_channel_videos: int
    rule: RecentRule
    floors: Floors
    short_max_seconds: int

    @classmethod
    def from_settings(cls) -> YtParams:
        return cls(
            window=timedelta(days=settings.youtube_baseline_window_days),
            mature_days=settings.youtube_baseline_mature_days,
            mature_max_days=settings.youtube_baseline_mature_max_days,
            min_channel_videos=settings.youtube_baseline_min_channel_videos,
            rule=RecentRule(
                newest=settings.youtube_baseline_recent_videos,
                minimum=settings.youtube_baseline_recent_min_videos,
                max_age=timedelta(
                    days=settings.youtube_baseline_recent_max_days
                ),
            ),
            floors=Floors(
                band_samples=settings.youtube_baseline_min_band_samples,
                band_spread_posts=(
                    settings.youtube_baseline_min_band_spread_posts
                ),
            ),
            short_max_seconds=settings.youtube_scoring_short_max_seconds,
        )

    @property
    def lookback(self) -> timedelta:
        """How far back a fit reads publications: the oldest it may use."""
        return max(
            self.window + self.rule.max_age,
            timedelta(days=self.mature_max_days),
        )

    def columns(self) -> dict[str, int]:
        """The parameters as the run row records them."""
        return {
            "window_days": self.window.days,
            "mature_days": self.mature_days,
            "mature_max_days": self.mature_max_days,
            "min_channel_videos": self.min_channel_videos,
            "recent_videos": self.rule.newest,
            "recent_min_videos": self.rule.minimum,
            "recent_max_days": self.rule.max_age.days,
            "min_band_samples": self.floors.band_samples,
            "min_band_spread_posts": self.floors.band_spread_posts,
        }


@dataclass(frozen=True, slots=True)
class YtFit:
    """What one fit found: references, fits, and what it could not fit."""

    moment: datetime
    channels_in_scope: int
    references: dict[
        tuple[str, YtVideoFormat, YtMetric],
        tuple[float, int, BaselineReference],
    ]
    fits: dict[Key, MetricFit]
    fitted: list[Key] = field(default_factory=list)
    borrowed: list[Key] = field(default_factory=list)
    skipped: list[
        tuple[YtChannelKind | None, YtVideoFormat, YtMetric, str]
    ] = field(default_factory=list)

    def baselines(self, kinds: Mapping[str, YtChannelKind]) -> YtBaselines:
        """This fit as a scoring pass reads a stored run."""
        return YtBaselines(
            computed_at=self.moment,
            medians={
                key: (value, reference)
                for key, (value, _, reference) in self.references.items()
            },
            kinds=dict(kinds),
            factors={key: found.factor for key, found in self.fits.items()},
            spreads={key: found.spread for key, found in self.fits.items()},
            curves={key: found.curve for key, found in self.fits.items()},
            band_spreads={
                key: dict(found.band_spreads)
                for key, found in self.fits.items()
            },
            dispersions={
                key: found.dispersion
                for key, found in self.fits.items()
                if found.dispersion is not None
            },
            channels_in_scope=self.channels_in_scope,
        )


def _value(counters: Counters, metric: YtMetric) -> int | None:
    value: int | None = getattr(counters, metric.value)
    return value


def _readings(
    video: YtVideo, moment: datetime
) -> list[tuple[datetime, Counters]]:
    return [entry for entry in video.readings if entry[0] <= moment]


def _history(
    videos: Sequence[YtVideo],
    metric: YtMetric,
    moment: datetime,
    *,
    since: datetime,
    counted: bool,
) -> dict[str, list[tuple[datetime, float]]]:
    """Each channel's videos and the value each reached in the day band.

    Per video the largest reading in the band: counters only rise, so the
    later one is the more complete. Positive values only, but for a
    counted metric zeros too. Oldest first.
    """
    history: dict[str, list[tuple[datetime, float]]] = {}
    for video in videos:
        if video.published < since:
            continue
        in_band = [
            value
            for observed, counters in _readings(video, moment)
            if YOUTUBE_BANDS.band_of(observed - video.published)
            == YOUTUBE_BANDS.reference
            and (value := _value(counters, metric)) is not None
        ]
        if not in_band:
            continue
        value = max(in_band)
        if value < 0 or (value == 0 and not counted):
            continue
        history.setdefault(video.channel_id, []).append(
            (video.published, float(value))
        )
    for entries in history.values():
        entries.sort()
    return history


def _settled(
    videos: Sequence[YtVideo],
    metric: YtMetric,
    moment: datetime,
    params: YtParams,
) -> dict[str, tuple[float, int]]:
    # ``moment`` bounds the readings taken, which is the fit's own moment
    # unless a replay lets settled readings come later (``fit_youtube``).
    """Each channel's settled median, over at least the minimum videos.

    One reading per video — the latest whose age is inside the window —
    because the refresh reads a past upload every 25 days, and a video read
    four times would otherwise count four times.
    """
    low = timedelta(days=params.mature_days)
    high = timedelta(days=params.mature_max_days)
    values: dict[str, list[float]] = {}
    for video in videos:
        settled = [
            (observed, counters)
            for observed, counters in _readings(video, moment)
            if low <= observed - video.published < high
        ]
        if not settled:
            continue
        value = _value(max(settled, key=lambda entry: entry[0])[1], metric)
        if value is None or value <= 0:
            continue
        values.setdefault(video.channel_id, []).append(float(value))
    return {
        channel: (median(found), len(found))
        for channel, found in values.items()
        if len(found) >= params.min_channel_videos
    }


def _observations(
    videos: Sequence[YtVideo],
    metric: YtMetric,
    moment: datetime,
    kinds: Mapping[str, YtChannelKind],
    *,
    since: datetime,
    counted: bool,
) -> dict[YtChannelKind, list[Observation[str, str]]]:
    """Readings of videos published since a moment, grouped by kind."""
    grouped: dict[YtChannelKind, list[Observation[str, str]]] = {}
    for video in videos:
        if video.published < since:
            continue
        kind = kinds[video.channel_id]
        for observed, counters in _readings(video, moment):
            value = _value(counters, metric)
            if value is None or value < 0 or (value == 0 and not counted):
                continue
            age = observed - video.published
            if age < timedelta(0):
                continue
            grouped.setdefault(kind, []).append(
                Observation(
                    post_key=(video.channel_id, video.video_id),
                    age=age,
                    value=float(value),
                    published=video.published,
                )
            )
    return grouped


def _fit(
    entries: list[Observation[str, str]],
    settled: Mapping[str, tuple[float, int]],
    *,
    own: Reference[str, str],
    counted: bool,
    params: YtParams,
) -> Fit | str:
    """One pool's fit under YouTube's bands: a ratio of sums for a count."""
    return fit(
        entries,
        settled,
        shape=fit_count_curve if counted else fit_curve,
        floors=params.floors,
        bands=YOUTUBE_BANDS,
        recent=own,
        counted=counted,
    )


def _stored(found: Fit, *, borrowed: bool) -> MetricFit:
    return MetricFit(
        factor=found.factor,
        spread=found.spread,
        samples=found.samples,
        curve=Curve(
            fractions=found.curve.fractions,
            samples=found.curve.samples,
            bands=YOUTUBE_BANDS,
        ),
        band_spreads=found.band_spreads,
        dispersion=found.dispersion,
        borrowed=borrowed,
    )


def fit_youtube(
    videos: Mapping[str, YtVideo],
    kinds: Mapping[str, YtChannelKind],
    moment: datetime,
    params: YtParams,
    *,
    settled_until: datetime | None = None,
) -> YtFit:
    """Every reference and fit the videos support, as of ``moment``.

    Sees nothing published or read after ``moment``, so a replay refitting
    as of a past day finds what the refresh of that day would have found.

    ``settled_until`` is the one exception, and a replay's alone: settled
    readings — of videos a month old or more, never the ones being scored —
    taken up to that later moment count as if taken by ``moment``. It lets a
    replay score days before the past uploads were first read; the cost is a
    settled video read some days older than it would have been, which a
    video that old barely notices and the factor, fitted on the same
    medians, absorbs.
    """
    found = YtFit(
        moment=moment, channels_in_scope=len(kinds), references={}, fits={}
    )
    for video_format in YtVideoFormat:
        pool = [
            video
            for video in videos.values()
            if video.format is video_format
            and video.channel_id in kinds
            and video.published <= moment
        ]
        for metric in YtMetric:
            counted = metric in COUNTED
            history = _history(
                pool,
                metric,
                moment,
                since=moment - params.window - params.rule.max_age,
                counted=counted,
            )
            recent = {
                channel: reference
                for channel, entries in history.items()
                if (
                    reference := params.rule.at(
                        entries, moment, bands=YOUTUBE_BANDS, counted=counted
                    )
                )
                is not None
            }
            settled = (
                {}
                if counted
                else _settled(pool, metric, settled_until or moment, params)
            )
            for channel, (value, samples) in recent.items():
                found.references[(channel, video_format, metric)] = (
                    value,
                    samples,
                    BaselineReference.RECENT,
                )
            for channel, (value, samples) in settled.items():
                if channel not in recent:
                    found.references[(channel, video_format, metric)] = (
                        value,
                        samples,
                        BaselineReference.MATURE,
                    )

            by_kind = _observations(
                pool,
                metric,
                moment,
                kinds,
                since=moment - params.window,
                counted=counted,
            )
            if not by_kind:
                found.skipped.append(
                    (None, video_format, metric, "nothing published it")
                )
                continue

            own = rolling(
                history, rule=params.rule, bands=YOUTUBE_BANDS, counted=counted
            )
            pooled = _fit(
                [entry for entries in by_kind.values() for entry in entries],
                settled,
                own=own,
                counted=counted,
                params=params,
            )
            for kind, entries in by_kind.items():
                key = (kind, video_format, metric)
                own_fit = _fit(
                    entries, settled, own=own, counted=counted, params=params
                )
                if not isinstance(own_fit, str):
                    found.fits[key] = _stored(own_fit, borrowed=False)
                    found.fitted.append(key)
                elif not isinstance(pooled, str):
                    found.fits[key] = _stored(pooled, borrowed=True)
                    found.borrowed.append(key)
                else:
                    found.skipped.append((kind, video_format, metric, own_fit))
    return found


@dataclass(frozen=True, slots=True)
class YtRefreshSummary:
    """What a refresh measured, and what it could not."""

    run_id: int
    found: YtFit

    def line(self) -> str:
        found = self.found
        channels = {channel for channel, _, _ in found.references}
        own = {
            channel
            for (channel, _, _), (_, _, reference) in found.references.items()
            if reference is BaselineReference.RECENT
        }
        line = (
            f"run {self.run_id}: {len(channels)} of {found.channels_in_scope} "
            f"channel(s) have a baseline, {len(channels - own)} of them on "
            f"settled videos alone; {len(found.fitted)} kind/format/metric "
            "fit(s)"
        )
        if found.borrowed:
            line += "; borrowed the format's pool: " + ", ".join(
                f"{kind}/{fmt}/{metric}"
                for kind, fmt, metric in found.borrowed
            )
        if found.skipped:
            line += "; not fitted: " + ", ".join(
                f"{kind or 'every kind'}/{fmt}/{metric} ({why})"
                for kind, fmt, metric, why in found.skipped
            )
        return line


async def refresh_yt_baselines(
    database: Database, *, now: datetime | None = None
) -> YtRefreshSummary:
    """Fit every YouTube baseline as of now and store it as one run.

    One transaction from the read to ``completed_at``: a refresh that dies
    half way leaves nothing a scoring pass would read.
    """
    moment = now or datetime.now(UTC)
    params = YtParams.from_settings()
    async with database.session() as session:
        kinds = await load_scope(session)
        videos = await load_videos(
            session,
            since=moment - params.lookback,
            until=moment,
            short_max_seconds=params.short_max_seconds,
        )
        found = fit_youtube(videos, kinds, moment, params)
        run = await store_run(
            session,
            parameters=params.columns(),
            channels_in_scope=len(kinds),
            references=found.references,
            fits=found.fits,
            completed_at=moment,
        )
        summary = YtRefreshSummary(run_id=run.id, found=found)
    logger.info("%s", summary.line())
    return summary
