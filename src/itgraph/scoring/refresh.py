"""The baseline refresh: measure what normal is, and write it down.

Fits nothing itself — the arithmetic is in ``scoring/curves.py`` and the
storage in ``db/baselines.py``. What lives here is the order the two are
put in, and the one rule that order exists to enforce: **a refresh
replaces rather than accumulates.** Everything is written against one
run, and the run is marked complete last, so a refresh that dies half way
is invisible instead of leaving medians for some channels and curves
fitted without them.

Baselines are computed for **all four metrics** regardless of which ones
may raise an alert. Enabling comments later is then a setting rather than
a re-fit, and — more usefully — the measurement that would justify
enabling them keeps accumulating in the meantime.

**Each channel gets one reference per metric**: its own newest posts at
eight hours where it has enough of them, its mature median otherwise. The
mature medians are still read for every channel, because the kind's
factor is fitted from them and the fallback channels need it.

**Comments are counted, and fitted differently** (``scoring/counts.py``):
their reference is the mean of the newest posts with the zeros kept, with
no mature fallback; their curve is a ratio of sums; and beside the log
spread, which is still stored and scores nothing, each kind gets a
dispersion fitted by maximum likelihood against the reference each post
would have been scored against.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from itgraph.config import settings
from itgraph.db.baselines import (
    count_in_scope,
    mature_medians,
    observations,
    recent_values,
    start_run,
    store_channel_medians,
    store_curves,
)
from itgraph.db.models import BaselineReference, ChannelKind, Metric
from itgraph.db.session import Database
from itgraph.scoring.curves import (
    TELEGRAM_BANDS,
    Observation,
    fit_count_curve,
    fit_curve,
)
from itgraph.scoring.fit import Fit, Floors, RecentRule, fit, rolling
from itgraph.scoring.score import COUNTED

# What a residual is measured from: Telegram's posts' own reference.
type Reference = Callable[[Observation[int, int]], float | None]

__all__ = ["BaselineSummary", "refresh_baselines"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BaselineSummary:
    """What a refresh measured, and what it could not.

    ``skipped`` is as much of the answer as ``fitted``. A refresh that
    fits views for every kind and nothing else has not half worked — it
    has reported that three metrics are too thinly published to be
    measured, which is a finding rather than a fault.
    """

    run_id: int
    channels_in_scope: int
    channels_with_baseline: int
    fitted: list[tuple[ChannelKind, Metric]] = field(default_factory=list)
    # Fitted from the pool across all kinds because this kind could not
    # support its own. Reported separately from ``fitted`` and from
    # ``skipped`` because it is neither: the channels are scored, and the
    # number they are scored against is not their kind's own. A growing
    # list here means the pooling is covering for something.
    borrowed: list[tuple[ChannelKind, Metric]] = field(default_factory=list)
    # ``None`` in the kind position means the metric was not fitted for
    # any kind, which is a different statement from one kind failing and
    # has to read differently — a metric absent from all three lists
    # would otherwise be unexplained.
    skipped: list[tuple[ChannelKind | None, Metric, str]] = field(
        default_factory=list
    )
    # Per metric, how many channels are measured against their own newest
    # posts and how many against the mature fallback. The second number
    # is how much of "scored" still means "scored against July".
    references: dict[Metric, tuple[int, int]] = field(default_factory=dict)

    def line(self) -> str:
        line = (
            f"run {self.run_id}: {self.channels_with_baseline} of "
            f"{self.channels_in_scope} channel(s) have a baseline, "
            f"{len(self.fitted)} kind/metric pair(s) fitted"
        )
        if self.references:
            counts = ", ".join(
                f"{metric} {recent}/{mature}"
                for metric, (recent, mature) in self.references.items()
            )
            line += f"; recent/mature references: {counts}"
        if self.borrowed:
            pairs = ", ".join(
                f"{kind}/{metric}" for kind, metric in self.borrowed
            )
            line += f"; borrowed the pooled fit: {pairs}"
        if self.skipped:
            reasons = ", ".join(
                f"{kind or 'every kind'}/{metric} ({why})"
                for kind, metric, why in self.skipped
            )
            line += f"; not fitted: {reasons}"
        return line


def _floors() -> Floors:
    """The band floors, as configured."""
    return Floors(
        band_samples=settings.baseline_min_band_samples,
        band_spread_posts=settings.baseline_min_band_spread_posts,
    )


def _rule() -> RecentRule:
    """How a channel's own reference is taken, as configured."""
    return RecentRule(
        newest=settings.baseline_recent_posts,
        minimum=settings.baseline_recent_min_posts,
        max_age=timedelta(days=settings.baseline_recent_max_days),
    )


def _fit(
    entries: list[Observation[int, int]],
    medians: dict[int, tuple[float, int]],
    *,
    recent: Reference | None = None,
    counted: bool = False,
) -> Fit | str:
    """Everything one pool of observations supports, or why it does not.

    ``scoring/fit.py`` under Telegram's bands and settings. A counted
    metric's curve is a ratio of sums; every other one's a median of each
    post's fraction.
    """
    return fit(
        entries,
        medians,
        shape=fit_count_curve if counted else fit_curve,
        floors=_floors(),
        bands=TELEGRAM_BANDS,
        recent=recent,
        counted=counted,
    )


def _recent_reference(
    history: Sequence[tuple[datetime, float]],
    moment: datetime,
    *,
    counted: bool = False,
) -> tuple[float, int] | None:
    """A channel's recent reference at a moment, under the settings."""
    return _rule().at(history, moment, bands=TELEGRAM_BANDS, counted=counted)


def _rolling(
    history: Mapping[int, Sequence[tuple[datetime, float]]],
    *,
    counted: bool = False,
) -> Reference:
    """Each observation's recent reference as of its own post's publication."""
    return rolling(
        history, rule=_rule(), bands=TELEGRAM_BANDS, counted=counted
    )


async def refresh_baselines(
    database: Database,
    *,
    now: datetime | None = None,
    window: timedelta | None = None,
) -> BaselineSummary:
    """Recompute every baseline and publish them as one run.

    One transaction from ``start_run`` to ``completed_at``. The
    alternative — committing each metric as it finishes — would leave a
    window in which a scoring pass could read a run that is complete for
    views and empty for forwards, and score against a mixture of a
    measurement and an absence without either side being able to tell.
    """
    moment = now or datetime.now(UTC)
    span = window or timedelta(days=settings.baseline_window_days)
    since = moment - span

    async with database.session() as session:
        run = await start_run(
            session,
            mature_days=settings.baseline_mature_days,
            min_channel_posts=settings.baseline_min_channel_posts,
            min_band_samples=settings.baseline_min_band_samples,
            min_band_spread_posts=settings.baseline_min_band_spread_posts,
            recent_posts=settings.baseline_recent_posts,
            recent_min_posts=settings.baseline_recent_min_posts,
            recent_max_days=settings.baseline_recent_max_days,
            channels_in_scope=await count_in_scope(session),
        )

        scored: set[int] = set()
        fitted: list[tuple[ChannelKind, Metric]] = []
        borrowed_pairs: list[tuple[ChannelKind, Metric]] = []
        skipped: list[tuple[ChannelKind | None, Metric, str]] = []
        references: dict[Metric, tuple[int, int]] = {}

        for metric in Metric:
            counted = metric in COUNTED
            # A counted metric has no mature fallback: the settled value is
            # a final count, not an eight-hour one, and its factor would be
            # fitted on the commented posts alone.
            medians = (
                {}
                if counted
                else await mature_medians(
                    session,
                    metric,
                    mature_days=settings.baseline_mature_days,
                    max_days=settings.baseline_mature_max_days,
                    min_posts=settings.baseline_min_channel_posts,
                )
            )
            # Far enough back that the oldest post in the curve window
            # still has its whole reference behind it.
            history = await recent_values(
                session,
                metric,
                since=since
                - timedelta(days=settings.baseline_recent_max_days),
                until=moment,
                burst_gap=timedelta(seconds=settings.scoring_burst_seconds),
            )
            recent = {
                channel: found
                for channel, entries in history.items()
                if (
                    found := _recent_reference(
                        entries, moment, counted=counted
                    )
                )
                is not None
            }
            fallback = {
                channel: median
                for channel, median in medians.items()
                if channel not in recent
            }
            await store_channel_medians(
                session,
                run,
                metric,
                recent,
                reference=BaselineReference.RECENT,
            )
            await store_channel_medians(
                session,
                run,
                metric,
                fallback,
                reference=BaselineReference.MATURE,
            )
            scored.update(recent)
            scored.update(fallback)
            references[metric] = (len(recent), len(fallback))
            rolling = _rolling(history, counted=counted)

            by_kind = await observations(
                session,
                metric,
                since=since,
                burst_gap=timedelta(seconds=settings.scoring_burst_seconds),
            )
            if not by_kind:
                # No snapshot in the window carried this metric at all —
                # a channel that publishes no reactions is the ordinary
                # case. Recorded rather than passed over, so a metric
                # missing from the fitted list has a stated reason.
                skipped.append((None, metric, "nothing published it"))
                continue

            # The pooled fit, across every kind at once. A kind too thin
            # to have a shape of its own takes this rather than going
            # unscoreable — `event` is 18 seed channels that could never
            # be scored however much history they accumulated.
            pooled = _fit(
                [entry for entries in by_kind.values() for entry in entries],
                medians,
                recent=rolling,
                counted=counted,
            )

            for kind, entries in by_kind.items():
                fit = _fit(entries, medians, recent=rolling, counted=counted)
                borrowed = False
                if isinstance(fit, str):
                    if isinstance(pooled, str):
                        skipped.append((kind, metric, fit))
                        continue
                    # The whole fit is borrowed, not the curve alone. A
                    # kind that cannot support a shape cannot support a
                    # factor or a spread either, and assembling one from
                    # two sources is the partial baseline the rules
                    # forbid — the flag is what makes this different.
                    fit, borrowed = pooled, True

                await store_curves(
                    session,
                    run,
                    kind,
                    metric,
                    curve=fit.curve,
                    factor=fit.factor,
                    spread=fit.spread,
                    band_spreads=fit.band_spreads,
                    samples=fit.samples,
                    borrowed=borrowed,
                    dispersion=fit.dispersion,
                )
                (borrowed_pairs if borrowed else fitted).append((kind, metric))

        run.channels_with_baseline = len(scored)
        run.completed_at = moment
        summary = BaselineSummary(
            run_id=run.id,
            channels_in_scope=run.channels_in_scope,
            channels_with_baseline=len(scored),
            fitted=fitted,
            borrowed=borrowed_pairs,
            skipped=skipped,
            references=references,
        )

    logger.info("%s", summary.line())
    return summary
