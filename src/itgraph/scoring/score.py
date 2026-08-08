"""What a post should have reached by now, and how far past it went.

Pure arithmetic over a baseline and a reading. No database, no clock, no
configuration read from anywhere — the threshold arrives as an argument,
so a replay can try a different one without touching a setting.

The estimate, and every part of it measured rather than assumed:

    expected(age) = channel's mature median      ← 30 days of its own posts
                  × factor                       ← mature → 8h, per metric
                  × curve(age, kind, metric)     ← the shape, per kind

    z = log(actual / expected) / spread          ← spread per metric and kind

**Levels are scored, never ratios**, and that is a correction to what
``docs/PLAN.md`` originally specified. A ratio like ``forwards / views``
looks age-free and is not: forwards front-load relative to views, so the
ratio runs about twice its settled value at fifteen minutes and scoring
it against a mature baseline over-alerts young posts. Over-alerting is
the direction that costs trust. A level against its own curve corrects
for age exactly once.

The four metrics stay apart. Views are reach, reactions approval,
forwards an endorsement strong enough to republish, comments as often an
argument as an interest; a combined score would average away the only
distinction worth having.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from itgraph.scoring.curves import Curve, band_of

__all__ = [
    "Baseline",
    "Score",
    "Spike",
    "expected",
    "score_metric",
    "score_post",
]


@dataclass(frozen=True, slots=True)
class Baseline:
    """Everything needed to say what one metric should be at one age.

    ``spread`` lives here rather than in the code because it is measured
    and it differs — 0.38 for views against 1.01 for comments, and again
    by channel kind. A constant in the source would quietly outlive the
    measurement it came from and silently apply one metric's shape to
    another.

    ``band_spreads`` is the same quantity measured per age band, and it
    is what a reading is actually divided by. The pooled ``spread`` is
    the fallback for a band with too few residuals to measure its own —
    a slightly wrong ruler at one age costs far less than no scoring at
    that age. Measured, the dispersion runs 1.18 at fifteen minutes
    against 0.98 at eight hours, so one figure for all ages would make
    the threshold mean different things at different points of a post's
    life without saying so.
    """

    mature_median: float
    factor: float
    curve: Curve
    spread: float
    band_spreads: dict[str, float] = field(default_factory=dict)

    def spread_at(self, age: timedelta) -> float | None:
        """The dispersion to divide this reading by, or ``None``.

        ``None`` only where the age is outside every band, which is the
        same condition that leaves it without an expectation — so a
        caller that has an expectation always has a ruler for it.
        """
        band = band_of(age)
        if band is None:
            return None
        return self.band_spreads.get(band, self.spread)


@dataclass(frozen=True, slots=True)
class Score:
    """One metric of one post, measured against what was expected."""

    metric: str
    age: timedelta
    observed: float
    expected: float
    z: float

    @property
    def times_expected(self) -> float:
        """How many times the expectation, for a person to read.

        A z is comparable across metrics and means nothing to anyone; a
        multiple is the reverse. Both belong in a message.
        """
        return self.observed / self.expected if self.expected > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Spike:
    """One post that scored past the threshold, and on what.

    The pair a raise needs: which post, and the single score it is being
    raised under. Kept here beside ``Score`` rather than in the pass, for
    the reason ``Cascade`` lives in ``alerts/cascade.py`` — the queue
    writer takes this type, and a type defined in the pass would make the
    two import each other.
    """

    post_key: tuple[int, int]
    score: Score


def expected(baseline: Baseline, age: timedelta) -> float | None:
    """What this metric should be on this channel at this age.

    ``None`` when the age falls outside every fitted band, or the curve
    has no fit there. Not scoring a reading costs nothing — the post has
    others — while inventing a fraction between two measured bands would
    be interpolating a shape nobody measured.
    """
    fraction = baseline.curve.at(age)
    if fraction is None or fraction <= 0:
        return None
    value = baseline.mature_median * baseline.factor * fraction
    return value if value > 0 else None


def score_metric(
    metric: str,
    observed: float | None,
    age: timedelta,
    baseline: Baseline | None,
) -> Score | None:
    """One metric's score, or ``None`` if it cannot honestly be given.

    Every ``None`` here is a refusal rather than a zero, and the
    distinction is the same one ``derive/metrics.py`` preserves at the
    other end of the pipeline: a channel that publishes no reactions has
    not scored zero on reactions, it has not been measured.

    A reading of zero *is* scored — a post nobody reacted to on a channel
    where they usually do is a real observation, and one the log handles
    by being undefined at zero, so it is floored rather than dropped.
    """
    if observed is None or baseline is None:
        return None
    reference = expected(baseline, age)
    dispersion = baseline.spread_at(age)
    if reference is None or dispersion is None or dispersion <= 0:
        return None

    # A zero reading has no logarithm. Floored at half a unit rather than
    # discarded: "fewer than one" is a real and scoreable state, and
    # dropping it would quietly exclude the quietest posts from ever
    # being measured at all.
    value = max(observed, 0.5)
    return Score(
        metric=metric,
        age=age,
        observed=observed,
        expected=reference,
        z=math.log(value / reference) / dispersion,
    )


def score_post(
    readings: Mapping[str, float | None],
    age: timedelta,
    baselines: Mapping[str, Baseline | None],
    *,
    metrics: tuple[str, ...],
) -> list[Score]:
    """Every metric of one reading that can be scored, highest first.

    Sorted so the caller can take the head and be done: one post raises
    one alert, under the metric that scored highest, because four
    independent alerts would put four messages about the most interesting
    post of the day into the chat inside an hour.

    ``metrics`` is passed rather than taken from the mapping's keys so
    that a metric can be measured and stored while being excluded from
    alerting — which is how comments are handled until they can be
    measured well enough to trust.
    """
    scores = [
        score
        for metric in metrics
        if (
            score := score_metric(
                metric, readings.get(metric), age, baselines.get(metric)
            )
        )
        is not None
    ]
    return sorted(scores, key=lambda entry: entry.z, reverse=True)
