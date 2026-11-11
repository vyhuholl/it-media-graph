"""YouTube's baselines: the videos and readings they are fitted on, and runs.

Nothing here fits anything — ``scoring/yt_refresh.py`` does. This module
reads what a fit needs and writes what it found down.

**One read serves every use.** The refresh, the scoring pass and the replay
all want the same thing: the videos of the channels in scope, each with its
format, and every reading of them up to a moment. ``load_videos`` reads
that once, between two publication moments; what a given moment could see
is cut from it in Python, so a replay that refits for each day it scores
reads the database once rather than once a day.

**A refresh replaces rather than accumulates**, by a run, as Telegram's
does: readers take the newest *completed* run, and one that dies half way
is never read.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    BaselineReference,
    ChannelStatus,
    YtBaselineRun,
    YtChannel,
    YtChannelBaseline,
    YtChannelKind,
    YtCurvePoint,
    YtLang,
    YtMetric,
    YtMetricBaseline,
    YtVideoFormat,
)
from itgraph.derive.yt_videos import Counters, format_of
from itgraph.scoring.curves import YOUTUBE_BANDS, Curve
from itgraph.scoring.score import Baseline

__all__ = [
    "SCORED_LANGS",
    "MetricFit",
    "YtBaselines",
    "YtVideo",
    "load_languages",
    "load_scope",
    "load_videos",
    "load_yt_baselines",
    "store_run",
]

# The languages whose seeds are scored: those that have a topic. `other`
# gets no alert, so nothing scores it (change 6's decision).
SCORED_LANGS = (YtLang.EN, YtLang.RU)


@dataclass(frozen=True, slots=True)
class YtVideo:
    """A video of a channel in scope, as scoring needs it.

    ``format`` is ``None`` for a video that is not scored — a broadcast, or
    one whose length cannot be read — and ``broadcast`` says which.
    ``readings`` are every reading up to the load's moment, oldest first.
    """

    video_id: str
    channel_id: str
    published: datetime
    format: YtVideoFormat | None
    broadcast: bool
    readings: tuple[tuple[datetime, Counters], ...] = ()


async def load_scope(session: AsyncSession) -> dict[str, YtChannelKind]:
    """Every channel in scope and its kind: seeds labelled `en` or `ru`."""
    rows = await session.execute(
        select(YtChannel.channel_id, YtChannel.kind).where(
            YtChannel.status == ChannelStatus.SEED,
            YtChannel.lang.in_(SCORED_LANGS),
            YtChannel.kind.is_not(None),
        )
    )
    return {channel: kind for channel, kind in rows.tuples() if kind}


async def load_languages(session: AsyncSession) -> dict[str, YtLang]:
    """The language of every channel in scope: what a report splits by."""
    rows = await session.execute(
        select(YtChannel.channel_id, YtChannel.lang).where(
            YtChannel.status == ChannelStatus.SEED,
            YtChannel.lang.in_(SCORED_LANGS),
            YtChannel.kind.is_not(None),
        )
    )
    return {channel: lang for channel, lang in rows.tuples() if lang}


# The publication time and the two sections a format is read from, and no
# more of the payload: a title or a description has no business here.
VIDEOS = """
    SELECT v.video_id,
           v.channel_id,
           (v.payload->'snippet'->>'publishedAt')::timestamptz AS published,
           jsonb_strip_nulls(jsonb_build_object(
             'contentDetails', v.payload->'contentDetails',
             'liveStreamingDetails', v.payload->'liveStreamingDetails'
           )) AS sections
    FROM yt_raw_videos v
    JOIN yt_channels c ON c.channel_id = v.channel_id
    WHERE c.status = 'seed'
      AND c.lang IN ('en', 'ru')
      AND c.kind IS NOT NULL
      AND (v.payload->'snippet'->>'publishedAt')::timestamptz >= :since
      AND (v.payload->'snippet'->>'publishedAt')::timestamptz <= :until
"""

READINGS = """
    SELECT video_id, observed_at, views, likes, comments
    FROM yt_raw_video_stats
    WHERE video_id = ANY(:ids) AND observed_at <= :until
    ORDER BY video_id, observed_at
"""


async def load_videos(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    short_max_seconds: int,
) -> dict[str, YtVideo]:
    """Videos of channels in scope published between two moments, read.

    The channel is the one YouTube attributes the video to now, which is
    the one its alert would be routed by. A reading taken after ``until``
    is not loaded, so a pass reasoning from a past moment cannot see it.
    """
    rows = (
        await session.execute(text(VIDEOS), {"since": since, "until": until})
    ).all()
    videos: dict[str, YtVideo] = {}
    for video_id, channel_id, published, sections in rows:
        videos[video_id] = YtVideo(
            video_id=video_id,
            channel_id=channel_id,
            published=published,
            format=format_of(sections, short_max_seconds=short_max_seconds),
            broadcast="liveStreamingDetails" in sections,
        )
    if not videos:
        return {}

    readings: dict[str, list[tuple[datetime, Counters]]] = {}
    for video_id, observed_at, views, likes, comments in await session.execute(
        text(READINGS), {"ids": list(videos), "until": until}
    ):
        readings.setdefault(video_id, []).append(
            (
                observed_at,
                Counters(views=views, likes=likes, comments=comments),
            )
        )
    return {
        video_id: YtVideo(
            video_id=video.video_id,
            channel_id=video.channel_id,
            published=video.published,
            format=video.format,
            broadcast=video.broadcast,
            readings=tuple(readings.get(video_id, ())),
        )
        for video_id, video in videos.items()
    }


@dataclass(frozen=True, slots=True)
class YtBaselines:
    """Everything a YouTube scoring pass needs, keyed for its lookups.

    Built from a stored run, or straight from a fit by a replay; the two are
    the same structure, so a replay scores exactly as a pass would.
    ``medians`` holds each channel's reference per format and metric, with
    what it is a median of.
    """

    computed_at: datetime
    medians: dict[
        tuple[str, YtVideoFormat, YtMetric], tuple[float, BaselineReference]
    ]
    kinds: dict[str, YtChannelKind]
    factors: dict[tuple[YtChannelKind, YtVideoFormat, YtMetric], float]
    spreads: dict[tuple[YtChannelKind, YtVideoFormat, YtMetric], float]
    curves: dict[tuple[YtChannelKind, YtVideoFormat, YtMetric], Curve]
    band_spreads: dict[
        tuple[YtChannelKind, YtVideoFormat, YtMetric], dict[str, float]
    ]
    dispersions: dict[tuple[YtChannelKind, YtVideoFormat, YtMetric], float] = (
        field(default_factory=dict)
    )
    channels_in_scope: int = 0
    run_id: int | None = None

    def for_video(
        self, channel_id: str, video_format: YtVideoFormat, metric: YtMetric
    ) -> Baseline | None:
        """The baseline for a video of this channel and format, or ``None``.

        ``None`` where any part is missing, as Telegram's: a partial
        baseline would score against a mixture of measured and assumed. A
        reference of the channel's own takes a factor of 1.
        """
        kind = self.kinds.get(channel_id)
        if kind is None:
            return None
        found = self.medians.get((channel_id, video_format, metric))
        if found is None:
            return None
        median, reference = found
        key = (kind, video_format, metric)
        factor = (
            1.0
            if reference is BaselineReference.RECENT
            else self.factors.get(key)
        )
        spread = self.spreads.get(key)
        curve = self.curves.get(key)
        if factor is None or spread is None or curve is None:
            return None
        return Baseline(
            reference=median,
            factor=factor,
            curve=curve,
            spread=spread,
            band_spreads=self.band_spreads.get(key, {}),
            dispersion=self.dispersions.get(key),
        )

    @property
    def scored_channels(self) -> int:
        """Channels with a reference for at least one format and metric."""
        return len({channel for channel, _, _ in self.medians})

    @property
    def fallback_channels(self) -> int:
        """Scored channels with no reference of their own at all."""
        own = {
            channel
            for (channel, _, _), (_, reference) in self.medians.items()
            if reference is BaselineReference.RECENT
        }
        return len({channel for channel, _, _ in self.medians} - own)


@dataclass(frozen=True, slots=True)
class MetricFit:
    """One kind, format and metric's fit, as stored."""

    factor: float
    spread: float
    samples: int
    curve: Curve
    band_spreads: Mapping[str, float]
    dispersion: float | None
    borrowed: bool


async def store_run(
    session: AsyncSession,
    *,
    parameters: Mapping[str, int],
    channels_in_scope: int,
    references: Mapping[
        tuple[str, YtVideoFormat, YtMetric],
        tuple[float, int, BaselineReference],
    ],
    fits: Mapping[tuple[YtChannelKind, YtVideoFormat, YtMetric], MetricFit],
    completed_at: datetime,
) -> YtBaselineRun:
    """Write one complete run: its parameters, references and fits.

    One transaction — the caller's — and ``completed_at`` set last, so a
    reader sees the whole run or none of it.
    """
    channels = {channel for channel, _, _ in references}
    own = {
        channel
        for (channel, _, _), (_, _, reference) in references.items()
        if reference is BaselineReference.RECENT
    }
    run = YtBaselineRun(
        **parameters,
        channels_in_scope=channels_in_scope,
        channels_with_baseline=len(channels),
        channels_on_fallback=len(channels - own),
    )
    session.add(run)
    await session.flush()

    session.add_all(
        YtChannelBaseline(
            run_id=run.id,
            channel_id=channel,
            format=video_format,
            metric=metric,
            median=median,
            samples=samples,
            reference=reference,
        )
        for (channel, video_format, metric), (
            median,
            samples,
            reference,
        ) in references.items()
    )
    for (kind, video_format, metric), found in fits.items():
        session.add(
            YtMetricBaseline(
                run_id=run.id,
                kind=kind,
                format=video_format,
                metric=metric,
                factor=found.factor,
                spread=found.spread,
                samples=found.samples,
                dispersion=found.dispersion,
                borrowed=found.borrowed,
            )
        )
        session.add_all(
            YtCurvePoint(
                run_id=run.id,
                kind=kind,
                format=video_format,
                metric=metric,
                band=band,
                fraction=fraction,
                spread=found.band_spreads.get(band),
                samples=found.curve.samples.get(band, 0),
            )
            for band, fraction in found.curve.fractions.items()
        )
    await session.flush()
    run.completed_at = completed_at
    return run


async def load_yt_baselines(
    session: AsyncSession, *, kinds: Mapping[str, YtChannelKind]
) -> YtBaselines | None:
    """The newest completed run, or ``None`` if there is none.

    ``kinds`` is the scope as it is now, read by the caller: a channel
    relabelled since the run is scored, or not, by its current label.
    """
    run = await session.scalar(
        select(YtBaselineRun)
        .where(YtBaselineRun.completed_at.is_not(None))
        .order_by(YtBaselineRun.completed_at.desc(), YtBaselineRun.id.desc())
        .limit(1)
    )
    if run is None or run.completed_at is None:
        return None

    medians = {
        (row.channel_id, row.format, row.metric): (row.median, row.reference)
        for row in await session.scalars(
            select(YtChannelBaseline).where(YtChannelBaseline.run_id == run.id)
        )
    }
    factors: dict[tuple[YtChannelKind, YtVideoFormat, YtMetric], float] = {}
    spreads: dict[tuple[YtChannelKind, YtVideoFormat, YtMetric], float] = {}
    dispersions: dict[
        tuple[YtChannelKind, YtVideoFormat, YtMetric], float
    ] = {}
    for ruler in await session.scalars(
        select(YtMetricBaseline).where(YtMetricBaseline.run_id == run.id)
    ):
        key = (ruler.kind, ruler.format, ruler.metric)
        factors[key] = ruler.factor
        spreads[key] = ruler.spread
        if ruler.dispersion is not None:
            dispersions[key] = ruler.dispersion

    fractions: dict[
        tuple[YtChannelKind, YtVideoFormat, YtMetric], dict[str, float]
    ] = {}
    counts: dict[
        tuple[YtChannelKind, YtVideoFormat, YtMetric], dict[str, int]
    ] = {}
    band_spreads: dict[
        tuple[YtChannelKind, YtVideoFormat, YtMetric], dict[str, float]
    ] = {}
    for point in await session.scalars(
        select(YtCurvePoint).where(YtCurvePoint.run_id == run.id)
    ):
        key = (point.kind, point.format, point.metric)
        fractions.setdefault(key, {})[point.band] = point.fraction
        counts.setdefault(key, {})[point.band] = point.samples
        if point.spread is not None:
            band_spreads.setdefault(key, {})[point.band] = point.spread

    return YtBaselines(
        run_id=run.id,
        computed_at=run.completed_at,
        medians=medians,
        kinds=dict(kinds),
        factors=factors,
        spreads=spreads,
        curves={
            key: Curve(
                fractions=value,
                samples=counts.get(key, {}),
                bands=YOUTUBE_BANDS,
            )
            for key, value in fractions.items()
        },
        band_spreads=band_spreads,
        dispersions=dispersions,
        channels_in_scope=run.channels_in_scope,
    )
