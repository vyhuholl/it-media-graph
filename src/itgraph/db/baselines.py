"""Reading and writing what a post of a given age is expected to reach.

Nothing here fits anything: the arithmetic lives in ``scoring/curves.py``,
which is pure and testable without a database. This module knows how to
ask Postgres for the inputs and how to write the answer down.

**A refresh replaces rather than accumulates**, and the mechanism is a
run: everything written points at one, and reading baselines means
reading the newest *completed* run. Older rows stay, which is what lets a
threshold argued about next month be compared against the baselines it
was actually arguing with rather than against whatever has been
recomputed since. A run that dies half way leaves ``completed_at`` null
and is therefore invisible to readers, rather than leaving medians for
some channels and curves fitted without them.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.backfill import in_scope
from itgraph.db.models import (
    BackfillState,
    BaselineReference,
    BaselineRun,
    Channel,
    ChannelBaseline,
    ChannelKind,
    CurvePoint,
    Metric,
    MetricBaseline,
)
from itgraph.scoring.curves import (
    AGE_BANDS,
    REFERENCE_BAND,
    Curve,
    Observation,
)
from itgraph.scoring.fold import burst_heads
from itgraph.scoring.score import Baseline

__all__ = [
    "Baselines",
    "count_in_scope",
    "current_run",
    "load_baselines",
    "mature_medians",
    "observations",
    "recent_values",
    "start_run",
    "store_channel_medians",
    "store_curves",
]

logger = logging.getLogger(__name__)

# How each metric is read out of a stored payload. The reactions
# expression is not defensive noise: the payload stores an absent field
# as JSON `null`, and `jsonb_array_elements` raises on a scalar rather
# than returning nothing — the same trap `derive/metrics.py` documents on
# the Python side.
MATURE_VALUE = {
    Metric.VIEWS: "(r.payload->>'views')::float8",
    Metric.FORWARDS: "(r.payload->>'forwards')::float8",
    Metric.COMMENTS: "(r.payload->'replies'->>'replies')::float8",
    Metric.REACTIONS: (
        "CASE WHEN jsonb_typeof(r.payload->'reactions'->'results') = 'array'"
        " THEN (SELECT sum((one->>'count')::bigint)"
        "       FROM jsonb_array_elements(r.payload->'reactions'->'results')"
        "       AS one)::float8 END"
    ),
}

# ...and out of a snapshot row.
SNAPSHOT_VALUE = {
    Metric.VIEWS: "m.views::float8",
    Metric.FORWARDS: "m.forwards::float8",
    Metric.COMMENTS: "m.comments::float8",
    Metric.REACTIONS: (
        "CASE WHEN jsonb_typeof(m.reactions) = 'object'"
        " THEN (SELECT sum(entry.value::bigint)"
        "       FROM jsonb_each_text(m.reactions) AS entry)::float8 END"
    ),
}


@dataclass(frozen=True, slots=True)
class Baselines:
    """Everything a scoring pass needs, read once.

    Keyed for the lookup the pass actually makes — by channel and metric
    for the median, by kind and metric for the ruler — rather than
    mirroring the tables. A pass scoring thousands of readings should not
    be joining anything.

    ``references`` says which of the medians are a channel's own
    eight-hour value; a pair missing from it is a mature median, which is
    what every run before recent references stored.
    """

    run_id: int
    computed_at: datetime
    medians: dict[tuple[int, Metric], float]
    kinds: dict[int, ChannelKind]
    factors: dict[tuple[ChannelKind, Metric], float]
    spreads: dict[tuple[ChannelKind, Metric], float]
    curves: dict[tuple[ChannelKind, Metric], Curve]
    band_spreads: dict[tuple[ChannelKind, Metric], dict[str, float]]
    channels_in_scope: int
    references: dict[tuple[int, Metric], BaselineReference] = field(
        default_factory=dict
    )

    def for_channel(self, channel_id: int, metric: Metric) -> Baseline | None:
        """The baseline for one channel and metric, or ``None``.

        ``None`` where any part is missing — no median because the
        channel is too thin, no curve because that kind and metric were
        never fitted. A partial baseline would score against a mixture of
        measured and assumed, which is the one thing a z may not be.

        A recent reference takes a factor of 1: it is already the value
        at the age the curve is normalised to, and the kind's factor is
        the bridge from a *mature* median to that age, not a property of
        the channel.
        """
        kind = self.kinds.get(channel_id)
        if kind is None:
            return None
        median = self.medians.get((channel_id, metric))
        recent = (
            self.references.get((channel_id, metric))
            is BaselineReference.RECENT
        )
        factor = 1.0 if recent else self.factors.get((kind, metric))
        spread = self.spreads.get((kind, metric))
        curve = self.curves.get((kind, metric))
        if median is None or factor is None or spread is None or curve is None:
            return None
        return Baseline(
            reference=median,
            factor=factor,
            curve=curve,
            spread=spread,
            band_spreads=self.band_spreads.get((kind, metric), {}),
        )

    @property
    def scored_channels(self) -> int:
        """How many channels have a median for at least one metric."""
        return len({channel for channel, _ in self.medians})

    @property
    def fallback_channels(self) -> int:
        """How many scored channels have no recent reference at all.

        Scored against what they were when their history was collected,
        rather than against what they are — counted, so that "scored"
        does not quietly mean both.
        """
        recent = {
            channel
            for (channel, _), reference in self.references.items()
            if reference is BaselineReference.RECENT
        }
        return len({channel for channel, _ in self.medians} - recent)


async def mature_medians(
    session: AsyncSession,
    metric: Metric,
    *,
    mature_days: int,
    max_days: int,
    min_posts: int,
) -> dict[int, tuple[float, int]]:
    """Each in-scope channel's median for one metric, and its sample count.

    **The window has two ends, and the upper one is load-bearing.** With
    only a lower bound the median covered a channel's entire history: the
    contributing post was 183 days old at the median, 332 at the 90th
    percentile and eight years at the oldest. A channel that has grown
    was then measured against every version of itself it had ever been,
    so its ordinary posts read as remarkable — measured, per-channel bias
    ran from +1.87 to −4.48 z, with 5% of channels biased by more than
    the alert threshold itself.

    Both ends are measured per row as ``fetched_at - date``, not against
    cutoff dates, because the backfill ran over a week and one cutoff
    would mean four weeks of maturity for one channel and five for
    another — the same correction ``notebooks/anomalous_posts.py`` makes.

    A channel under ``min_posts`` is absent rather than present with a
    thin median. A median over a handful of posts is not a baseline, and
    a baseline that is wrong is worse than one that is missing: the
    missing one gets reported.
    """
    result = await session.execute(
        text(f"""
            SELECT channel_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS median,
                   count(*) AS samples
            FROM (
              SELECT r.channel_id, {MATURE_VALUE[metric]} AS v
              FROM raw_messages r
              JOIN channels c ON c.tg_id = r.channel_id
              LEFT JOIN backfill_state b ON b.channel_id = c.tg_id
              WHERE r.payload->>'_' = 'Message'
                AND c.status = 'seed'
                AND c.is_chat = false
                AND b.failure_kind IS DISTINCT FROM 'permanent'
                AND r.fetched_at - (r.payload->>'date')::timestamptz
                    >= make_interval(days => :mature_days)
                AND r.fetched_at - (r.payload->>'date')::timestamptz
                    < make_interval(days => :max_days)
            ) t
            WHERE v IS NOT NULL AND v > 0
            GROUP BY channel_id
            HAVING count(*) >= :min_posts
        """),
        {
            "mature_days": mature_days,
            "max_days": max_days,
            "min_posts": min_posts,
        },
    )
    return {
        channel: (float(median), int(samples))
        for channel, median, samples in result.all()
    }


# The reference band, as the interval a reading's age has to fall in.
_REFERENCE_LOW, _REFERENCE_HIGH = next(
    (low, high) for low, high, name in AGE_BANDS if name == REFERENCE_BAND
)


async def recent_values(
    session: AsyncSession,
    metric: Metric,
    *,
    since: datetime,
    until: datetime,
    burst_gap: timedelta,
) -> dict[int, list[tuple[datetime, float]]]:
    """Each in-scope channel's posts and the value each reached at 8h.

    Per post, the largest reading in the reference band — the same value
    ``scoring/curves.py`` normalises a curve to, so a reference built
    from these and a curve fraction multiply into the same quantity.
    Oldest first, positive values only, which is what a median over them
    is taken over; a post never read in the band (the collector sleeps
    through the eighth hour of anything published around 19:00) is left
    out rather than guessed.

    **Posts are counted the way the scorer counts them**: an album as its
    first part, a burst as its first message. Otherwise an album of ten
    parts is ten votes for one post's reach.

    ``until`` bounds both the posts and the readings, so a refresh
    reasoning from a past moment sees what it would have seen then.
    """
    result = await session.execute(
        text(f"""
            WITH posts AS (
              SELECT channel_id, msg_id, published FROM (
                SELECT r.channel_id, r.msg_id,
                       (r.payload->>'date')::timestamptz AS published,
                       row_number() OVER (
                         PARTITION BY r.channel_id,
                           coalesce(r.payload->>'grouped_id', r.msg_id::text)
                         ORDER BY r.msg_id
                       ) AS part
                FROM raw_messages r
                JOIN channels c ON c.tg_id = r.channel_id
                LEFT JOIN backfill_state b ON b.channel_id = c.tg_id
                WHERE r.payload->>'_' = 'Message'
                  AND c.status = 'seed'
                  AND c.is_chat = false
                  AND b.failure_kind IS DISTINCT FROM 'permanent'
                  AND (r.payload->>'date')::timestamptz >= :since
                  AND (r.payload->>'date')::timestamptz <= :until
              ) t
              WHERE part = 1
            )
            SELECT p.channel_id, p.msg_id, p.published,
                   max({SNAPSHOT_VALUE[metric]}) FILTER (
                     WHERE m.observed_at - p.published >= :low
                       AND m.observed_at - p.published < :high
                       AND m.observed_at <= :until
                   ) AS v
            FROM posts p
            LEFT JOIN message_metrics m
              ON m.channel_id = p.channel_id AND m.msg_id = p.msg_id
            GROUP BY p.channel_id, p.msg_id, p.published
        """),
        {
            "since": since,
            "until": until,
            "low": _REFERENCE_LOW,
            "high": _REFERENCE_HIGH,
        },
    )
    rows = result.all()

    # Every post takes part in chaining a burst, read or not; only a
    # burst's first message then contributes its value.
    heads = burst_heads(
        (((channel, msg), published) for channel, msg, published, _ in rows),
        gap=burst_gap,
    )
    history: dict[int, list[tuple[datetime, float]]] = {}
    for channel, msg, published, value in rows:
        if heads[(channel, msg)] != (channel, msg):
            continue
        if value is None or value <= 0:
            continue
        history.setdefault(channel, []).append((published, float(value)))
    for entries in history.values():
        entries.sort()
    return history


async def observations(
    session: AsyncSession, metric: Metric, *, since: datetime
) -> dict[ChannelKind, list[Observation]]:
    """Snapshots of posts published since a moment, grouped by channel kind.

    The age carried on each observation is ``observed_at`` minus the
    stored publication date. Never which sample in the schedule this was
    meant to be — samples are irregular by design, and a curve fitted on
    intended ages would describe a schedule rather than a metric.
    """
    result = await session.execute(
        text(f"""
            SELECT c.kind::text AS kind, m.channel_id, m.msg_id,
                   EXTRACT(EPOCH FROM (
                     m.observed_at - (r.payload->>'date')::timestamptz
                   )) AS age_seconds,
                   {SNAPSHOT_VALUE[metric]} AS v,
                   (r.payload->>'date')::timestamptz AS published
            FROM message_metrics m
            JOIN raw_messages r
              ON r.channel_id = m.channel_id AND r.msg_id = m.msg_id
            JOIN channels c ON c.tg_id = m.channel_id
            WHERE (r.payload->>'date')::timestamptz >= :since
              AND c.kind IS NOT NULL
        """),
        {"since": since},
    )

    grouped: dict[ChannelKind, list[Observation]] = {}
    for kind, channel, msg, age_seconds, value, published in result.all():
        # A reading predating its post is clock skew or a bad stored
        # date; a negative age passes every band test silently, which is
        # the worst way for bad data to behave.
        if value is None or value <= 0 or age_seconds is None:
            continue
        if age_seconds < 0:
            continue
        grouped.setdefault(ChannelKind(kind), []).append(
            Observation(
                post_key=(channel, msg),
                age=timedelta(seconds=float(age_seconds)),
                value=float(value),
                published=published,
            )
        )
    return grouped


async def count_in_scope(session: AsyncSession) -> int:
    """How many channels a refresh could in principle have covered.

    The denominator that makes "465 channels have baselines" mean
    something. Without it, a refresh that covered a tenth of the
    inventory and one that covered all of it read the same.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(Channel)
            .outerjoin(
                BackfillState, BackfillState.channel_id == Channel.tg_id
            )
            .where(*in_scope())
        )
        or 0
    )


async def start_run(
    session: AsyncSession,
    *,
    mature_days: int,
    min_channel_posts: int,
    min_band_samples: int,
    channels_in_scope: int,
    min_band_spread_posts: int | None = None,
    recent_posts: int | None = None,
    recent_min_posts: int | None = None,
    recent_max_days: int | None = None,
) -> BaselineRun:
    """Open a refresh. Nothing reads it until it is completed."""
    run = BaselineRun(
        mature_days=mature_days,
        min_channel_posts=min_channel_posts,
        min_band_samples=min_band_samples,
        min_band_spread_posts=min_band_spread_posts,
        recent_posts=recent_posts,
        recent_min_posts=recent_min_posts,
        recent_max_days=recent_max_days,
        channels_in_scope=channels_in_scope,
        channels_with_baseline=0,
    )
    session.add(run)
    await session.flush()
    return run


async def store_channel_medians(
    session: AsyncSession,
    run: BaselineRun,
    metric: Metric,
    medians: dict[int, tuple[float, int]],
    *,
    reference: BaselineReference = BaselineReference.MATURE,
) -> None:
    """Write one metric's channel medians for this run, of one reference.

    ``reference`` defaults to mature because that is what every median
    stored before recent references was, and a caller writing a recent
    one has to say so.
    """
    session.add_all(
        ChannelBaseline(
            run_id=run.id,
            channel_id=channel,
            metric=metric,
            median=median,
            samples=samples,
            reference=reference,
        )
        for channel, (median, samples) in medians.items()
    )


async def store_curves(
    session: AsyncSession,
    run: BaselineRun,
    kind: ChannelKind,
    metric: Metric,
    *,
    curve: Curve,
    factor: float,
    spread: float,
    samples: int,
    band_spreads: dict[str, float] | None = None,
    borrowed: bool = False,
) -> None:
    """Write one kind's fitted shape and rulers for one metric.

    ``band_spreads`` carries the dispersion measured for each band; a
    band absent from it stores ``NULL`` and falls back to the pooled
    ``spread`` at scoring time. Absent rather than a copy of the pooled
    figure, so "this band was not measured" stays legible afterwards
    instead of looking like a measurement that happened to agree.
    """
    session.add(
        MetricBaseline(
            run_id=run.id,
            kind=kind,
            metric=metric,
            factor=factor,
            spread=spread,
            samples=samples,
            borrowed=borrowed,
        )
    )
    spreads = band_spreads or {}
    session.add_all(
        CurvePoint(
            run_id=run.id,
            kind=kind,
            metric=metric,
            band=band,
            fraction=fraction,
            spread=spreads.get(band),
            samples=curve.samples.get(band, 0),
        )
        for band, fraction in curve.fractions.items()
    )


async def current_run(session: AsyncSession) -> BaselineRun | None:
    """The newest completed refresh, or ``None`` if there is none.

    Completed, not merely newest: a refresh that died half way must be
    invisible rather than half-read.
    """
    run: BaselineRun | None = await session.scalar(
        select(BaselineRun)
        .where(BaselineRun.completed_at.is_not(None))
        .order_by(BaselineRun.completed_at.desc())
        .limit(1)
    )
    return run


async def load_baselines(session: AsyncSession) -> Baselines | None:
    """Everything the scoring pass needs, from the newest completed run."""
    run = await current_run(session)
    if run is None or run.completed_at is None:
        return None

    medians: dict[tuple[int, Metric], float] = {}
    references: dict[tuple[int, Metric], BaselineReference] = {}
    for median_row in await session.scalars(
        select(ChannelBaseline).where(ChannelBaseline.run_id == run.id)
    ):
        pair = (median_row.channel_id, median_row.metric)
        medians[pair] = median_row.median
        references[pair] = median_row.reference

    factors: dict[tuple[ChannelKind, Metric], float] = {}
    spreads: dict[tuple[ChannelKind, Metric], float] = {}
    for ruler in await session.scalars(
        select(MetricBaseline).where(MetricBaseline.run_id == run.id)
    ):
        factors[(ruler.kind, ruler.metric)] = ruler.factor
        spreads[(ruler.kind, ruler.metric)] = ruler.spread

    fractions: dict[tuple[ChannelKind, Metric], dict[str, float]] = {}
    counts: dict[tuple[ChannelKind, Metric], dict[str, int]] = {}
    band_spreads: dict[tuple[ChannelKind, Metric], dict[str, float]] = {}
    for point in await session.scalars(
        select(CurvePoint).where(CurvePoint.run_id == run.id)
    ):
        key = (point.kind, point.metric)
        fractions.setdefault(key, {})[point.band] = point.fraction
        counts.setdefault(key, {})[point.band] = point.samples
        if point.spread is not None:
            band_spreads.setdefault(key, {})[point.band] = point.spread

    kinds = {
        channel_id: kind
        for channel_id, kind in await session.execute(
            select(Channel.tg_id, Channel.kind).where(
                Channel.kind.is_not(None)
            )
        )
    }

    return Baselines(
        run_id=run.id,
        computed_at=run.completed_at,
        medians=medians,
        kinds=kinds,
        factors=factors,
        spreads=spreads,
        curves={
            key: Curve(fractions=value, samples=counts.get(key, {}))
            for key, value in fractions.items()
        },
        band_spreads=band_spreads,
        channels_in_scope=run.channels_in_scope,
        references=references,
    )
