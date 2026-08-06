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
    # ``None`` in the kind position means the metric was not fitted for
    # any kind, which is a different statement from one kind failing and
    # has to read differently — a metric absent from both lists would
    # otherwise be unexplained.
    skipped: list[tuple[ChannelKind | None, Metric, str]] = field(
        default_factory=list
    )

    def line(self) -> str:
        line = (
            f"run {self.run_id}: {self.channels_with_baseline} of "
            f"{self.channels_in_scope} channel(s) have a baseline, "
            f"{len(self.fitted)} kind/metric pair(s) fitted"
        )
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
) -> list[float]:
    """``log(actual / expected)`` for every observation that has both.

    Computed against the curve and factor just fitted, not against a
    previous run's: the spread has to describe *this* estimate's error,
    or a threshold expressed in it would be measuring the difference
    between two vintages of baseline.
    """
    residuals: list[float] = []
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
        residuals.append(math.log(entry.value / expected))
    return residuals


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
        skipped: list[tuple[ChannelKind | None, Metric, str]] = []

        for metric in Metric:
            medians = await mature_medians(
                session,
                metric,
                mature_days=settings.baseline_mature_days,
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
            for kind, entries in by_kind.items():
                curve = fit_curve(
                    entries,
                    min_samples=settings.baseline_min_band_samples,
                )
                if not curve.fractions:
                    skipped.append((kind, metric, "no band met the minimum"))
                    continue

                factor = fit_factor(
                    entries,
                    {
                        channel: value
                        for channel, (value, _) in medians.items()
                    },
                    min_samples=settings.baseline_min_band_samples,
                )
                if factor is None:
                    skipped.append(
                        (kind, metric, "too few posts with a mature median")
                    )
                    continue

                spread = fit_spread(
                    _residuals(entries, medians, curve=curve, factor=factor),
                    min_samples=settings.baseline_min_band_samples,
                )
                if spread is None:
                    # Without a spread there is no z, and a borrowed one
                    # would apply another metric's shape to this one.
                    skipped.append((kind, metric, "no measurable spread"))
                    continue

                await store_curves(
                    session,
                    run,
                    kind,
                    metric,
                    curve=curve,
                    factor=factor,
                    spread=spread,
                    samples=len(entries),
                )
                fitted.append((kind, metric))

        run.channels_with_baseline = len(scored)
        run.completed_at = moment
        summary = BaselineSummary(
            run_id=run.id,
            channels_in_scope=run.channels_in_scope,
            channels_with_baseline=len(scored),
            fitted=fitted,
            skipped=skipped,
        )

    logger.info("%s", summary.line())
    return summary
