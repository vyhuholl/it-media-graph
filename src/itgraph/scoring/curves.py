"""How a metric accrues with a post's age, fitted from observed snapshots.

Pure functions over plain rows — no database, no clock of its own, every
input passed in. The same shape ``affiliation/signals.py`` and
``alerts/cascade.py`` have, and for the same reason: what a curve is will
be re-fitted over data already collected, so it must be re-computable and
testable without any of the machinery that gathered it.

Two quantities are fitted here and they answer different questions.

**The curve** is the shape: what fraction of its eight-hour value a post
has reached at age *t*. Fitted per metric and per channel kind, because
the shapes genuinely differ — at one hour a post holds a third of its
views and over half its forwards, and a vacancy feed accrues visibly
slower than an aggregator.

**The factor** is the join to history: how much of its channel's *mature*
median a post has reached by eight hours. Without it a curve normalised
to the eight-hour value cannot be compared to a baseline computed over
thirty days, and the two halves of the estimate do not meet. It is fitted
per metric and not shared, because the four differ by more than a factor
of two — views keep trickling for weeks while a comment happens when
somebody reads the post.

**The spread** is what makes the whole thing a z-score rather than a
ratio: the dispersion of ``log(actual / expected)`` once the curve and
the factor have done their work. Measured rather than assumed, and stored
alongside, because it is the number that decides what a threshold means.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

__all__ = [
    "AGE_BANDS",
    "Curve",
    "Observation",
    "band_of",
    "fit_curve",
    "fit_factor",
    "fit_spread",
    "median",
]

# The ages a curve is fitted at, each a window around one of the
# collection schedule's offsets. Windows rather than points because
# samples are irregular by design — quiet hours, an outage, a rate limit
# — so a reading lands *near* its offset rather than on it.
#
# The upper edge is eight hours because that is where the schedule's
# dense half ends and where views are 96% settled. Beyond it the curve is
# flat enough that a band would be fitted on noise.
AGE_BANDS: tuple[tuple[timedelta, timedelta, str], ...] = (
    (timedelta(minutes=12), timedelta(minutes=21), "15m"),
    (timedelta(minutes=24), timedelta(minutes=36), "30m"),
    (timedelta(minutes=51), timedelta(minutes=75), "1h"),
    (timedelta(minutes=108), timedelta(minutes=144), "2h"),
    (timedelta(hours=3, minutes=30), timedelta(hours=4, minutes=42), "4h"),
    (timedelta(hours=7), timedelta(hours=9), "8h"),
)

# The band the factor is measured against: a post's value here is what
# the factor relates to its channel's mature median.
REFERENCE_BAND = "8h"


@dataclass(frozen=True, slots=True)
class Observation:
    """One reading of one post: its age, and what the metric was.

    A narrow projection of a snapshot rather than the row itself. What a
    curve is made of is *when* and *how much*, and passing the whole row
    would let this module start depending on columns it has no business
    reading.

    ``age`` is the interval between the reading and the post's
    publication — computed by the caller from ``observed_at`` and the
    stored date, never from which sample in the schedule this was
    supposed to be. Samples are irregular by design, and a fit that
    assumed the schedule was met would mis-age exactly the posts whose
    sampling was unusual.
    """

    post_key: tuple[int, int]
    age: timedelta
    value: float


@dataclass(frozen=True, slots=True)
class Curve:
    """A fitted shape: fraction of the reference value, per age band.

    ``samples`` travels with it because a band fitted on eleven
    observations and one fitted on two thousand are not the same claim,
    and whoever reads a threshold later needs to be able to tell.
    """

    fractions: dict[str, float]
    samples: dict[str, int]

    def at(self, age: timedelta) -> float | None:
        """The fraction for this age, or ``None`` outside every band."""
        band = band_of(age)
        return None if band is None else self.fractions.get(band)


def band_of(age: timedelta) -> str | None:
    """Which fitted band an age falls in, or ``None`` between them.

    ``None`` rather than the nearest band: a reading at three hours sits
    between the two-hour and four-hour fits, and inventing a value for it
    would be interpolating a shape this module has not measured. Not
    scoring a reading costs nothing — the post has other readings.
    """
    for low, high, name in AGE_BANDS:
        if low <= age < high:
            return name
    return None


def median(values: Sequence[float]) -> float:
    """The middle value. Median throughout, never mean.

    One viral post moves a mean, and the viral post is the thing being
    measured — a baseline that the outlier drags upward hides the next
    outlier. The same argument ``notebooks/anomalous_posts.py`` makes for
    its own baselines.
    """
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _reference(
    observations: Iterable[Observation],
) -> dict[tuple[int, int], float]:
    """Each post's value in the reference band, where it has one.

    A post read twice inside the band contributes its largest reading:
    counters only rise, so the later one is the more complete, and taking
    a median of two would understate a post the loop happened to sample
    early in the window.
    """
    reference: dict[tuple[int, int], float] = {}
    for entry in observations:
        if band_of(entry.age) != REFERENCE_BAND:
            continue
        if entry.value <= 0:
            continue
        current = reference.get(entry.post_key)
        if current is None or entry.value > current:
            reference[entry.post_key] = entry.value
    return reference


def fit_curve(
    observations: Sequence[Observation], *, min_samples: int = 20
) -> Curve:
    """The shape, as a fraction of each post's own reference value.

    Normalising per post before aggregating is what removes channel size
    from the shape: a channel reaching two hundred readers and one
    reaching two hundred thousand contribute equally to what "half way by
    two hours" means.

    Only posts with a reading in the reference band take part. A post the
    loop stopped watching early has no denominator, and guessing one from
    its last reading would bias the shape toward whatever ages happened to
    be sampled.

    A band under ``min_samples`` is left out rather than fitted thinly.
    An absent band means nothing is scored at that age, which is a gap;
    a band fitted on four observations is a wrong number, which is worse.
    """
    reference = _reference(observations)

    gathered: dict[str, list[float]] = {}
    for entry in observations:
        base = reference.get(entry.post_key)
        if base is None or base <= 0 or entry.value <= 0:
            continue
        band = band_of(entry.age)
        if band is None:
            continue
        gathered.setdefault(band, []).append(entry.value / base)

    return Curve(
        fractions={
            band: median(values)
            for band, values in gathered.items()
            if len(values) >= min_samples
        },
        samples={band: len(values) for band, values in gathered.items()},
    )


def fit_factor(
    observations: Sequence[Observation],
    mature: Mapping[int, float],
    *,
    min_samples: int = 20,
) -> float | None:
    """Reference value over the channel's mature median, across posts.

    The join between a curve normalised to eight hours and a baseline
    computed over thirty days. Without it the two halves of the estimate
    are in different units and the product means nothing.

    ``None`` when too few posts have both halves — which is the honest
    answer on a metric most channels do not publish, and better than a
    factor fitted on six posts that then scales every score on it.
    """
    ratios = [
        value / mature[channel]
        for (channel, _), value in _reference(observations).items()
        if mature.get(channel, 0) > 0
    ]
    if len(ratios) < min_samples:
        return None
    return median(ratios)


def fit_spread(
    residuals: Sequence[float], *, min_samples: int = 20
) -> float | None:
    """The dispersion of ``log(actual / expected)``, robustly.

    Median absolute deviation scaled to a standard deviation, never the
    standard deviation itself: the distribution's tail is the thing being
    detected, and an estimator the tail can move would widen with every
    spike until nothing scored above it.

    ``None`` on too few residuals, and on a spread of zero — a
    denominator of zero would make every deviation infinite, which is not
    a very confident measurement but an undefined one.
    """
    if len(residuals) < min_samples:
        return None
    centre = median(residuals)
    deviation = median([abs(value - centre) for value in residuals]) * 1.4826
    return deviation if deviation > 0 else None
