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
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from itgraph.config import settings
from itgraph.db.baselines import (
    count_in_scope,
    mature_medians,
    observations,
    start_run,
    store_channel_medians,
    store_curves,
)
from itgraph.db.models import ChannelKind, Metric
from itgraph.db.session import Database
from itgraph.scoring.curves import (
    Curve,
    Observation,
    band_of,
    fit_band_spreads,
    fit_curve,
    fit_factor,
    fit_spread,
)

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

    def line(self) -> str:
        line = (
            f"run {self.run_id}: {self.channels_with_baseline} of "
            f"{self.channels_in_scope} channel(s) have a baseline, "
            f"{len(self.fitted)} kind/metric pair(s) fitted"
        )
        if self.borrowed:
            pairs = ", ".join(
                f"{kind}/{metric}" for kind, metric in self.borrowed
            )
            line += f"; borrowed the pooled curve: {pairs}"
        if self.skipped:
            reasons = ", ".join(
                f"{kind or 'every kind'}/{metric} ({why})"
                for kind, metric, why in self.skipped
            )
            line += f"; not fitted: {reasons}"
        return line


def _residuals(
    entries: list[Observation],
    medians: dict[int, tuple[float, int]],
    *,
    curve: Curve,
    factor: float,
) -> dict[str, list[float]]:
    """``log(actual / expected)`` per age band, for those that have both.

    Computed against the curve and factor just fitted, not against a
    previous run's: the spread has to describe *this* estimate's error,
    or a threshold expressed in it would be measuring the difference
    between two vintages of baseline.

    Grouped by band because the dispersion is not constant across a
    post's life — measured, 1.18 at fifteen minutes against 0.98 at eight
    hours. The caller both pools these and fits each band separately, so
    a band too thin to speak for itself still has something to fall back
    on.
    """
    residuals: dict[str, list[float]] = {}
    for entry in entries:
        band = band_of(entry.age)
        if band is None:
            continue
        fraction = curve.fractions.get(band)
        median = medians.get(entry.post_key[0])
        if fraction is None or fraction <= 0 or median is None:
            continue
        expected = median[0] * factor * fraction
        if expected <= 0 or entry.value <= 0:
            continue
        residuals.setdefault(band, []).append(math.log(entry.value / expected))
    return residuals


@dataclass(frozen=True, slots=True)
class Fit:
    """A shape and its two rulers, fitted from one pool of observations."""

    curve: Curve
    factor: float
    spread: float
    band_spreads: dict[str, float]
    samples: int


def _fit(
    entries: list[Observation], medians: dict[int, tuple[float, int]]
) -> Fit | str:
    """Everything one pool of observations supports, or why it does not.

    Returns the reason as a string rather than ``None`` so a caller can
    say which of the three steps failed. "Not fitted" without a reason is
    the kind of silence this whole change exists to remove.
    """
    floor = settings.baseline_min_band_samples
    curve = fit_curve(entries, min_samples=floor)
    if not curve.fractions:
        return "no band met the minimum"

    factor = fit_factor(
        entries,
        {channel: value for channel, (value, _) in medians.items()},
        min_samples=floor,
    )
    if factor is None:
        return "too few posts with a mature median"

    residuals = _residuals(entries, medians, curve=curve, factor=factor)
    pooled = fit_spread(
        [value for values in residuals.values() for value in values],
        min_samples=floor,
    )
    if pooled is None:
        # Without a spread there is no z, and one borrowed from another
        # metric would apply that metric's shape to this one.
        return "no measurable spread"

    return Fit(
        curve=curve,
        factor=factor,
        spread=pooled,
        band_spreads=fit_band_spreads(residuals, min_samples=floor),
        samples=len(entries),
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
            channels_in_scope=await count_in_scope(session),
        )

        scored: set[int] = set()
        fitted: list[tuple[ChannelKind, Metric]] = []
        borrowed_pairs: list[tuple[ChannelKind, Metric]] = []
        skipped: list[tuple[ChannelKind | None, Metric, str]] = []

        for metric in Metric:
            medians = await mature_medians(
                session,
                metric,
                mature_days=settings.baseline_mature_days,
                max_days=settings.baseline_mature_max_days,
                min_posts=settings.baseline_min_channel_posts,
            )
            await store_channel_medians(session, run, metric, medians)
            scored.update(medians)

            by_kind = await observations(session, metric, since=since)
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
            )

            for kind, entries in by_kind.items():
                fit = _fit(entries, medians)
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
        )

    logger.info("%s", summary.line())
    return summary
