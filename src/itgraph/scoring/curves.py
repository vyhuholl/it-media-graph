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

It is measured **per band**, not once per metric. The first version of
this module assumed it was flat across ages; measured over 20 039 scored
readings it runs 1.18 at fifteen minutes and 0.98 at eight hours. One
figure makes a threshold stricter at some ages than at others without
saying so, which is the one thing a comparable score may not do.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise

__all__ = [
    "AGE_BANDS",
    "BAND_EDGES_MINUTES",
    "REFERENCE_BAND",
    "Curve",
    "Observation",
    "band_of",
    "fit_band_spreads",
    "fit_curve",
    "fit_factor",
    "fit_spread",
    "median",
]

# The edges of the age bands, in minutes: contiguous from ten minutes to
# the end of the alerting window, with no gaps between them.
#
# **They used to be six narrow windows around the collection schedule's
# offsets, and that was measured to be wrong.** Those six covered 273
# minutes out of the window, so 84% of readings were discarded and 840 of
# 3 459 posts were never scored at all — and not at random: 80–88% of
# posts published between 11:00 and 17:00 got scored against 42% of those
# published at 03:00, because sampling is irregular exactly where the
# collector is asleep or busy. A post's chance of being measured must not
# depend on the hour it was published.
#
# Scoring between the offsets was originally refused as interpolation of
# a shape nobody had measured, and against a few hundred observations
# that was right. The gaps now hold 2 000–3 300 readings *per hour*, so a
# band there is measured like any other and `min_band_samples` still
# keeps a thin one out.
#
# The spacing is geometric because growth is: a post changes more between
# ten and twenty minutes than between eight and nine hours, so equal
# bands would be fitted on nothing early and on noise late.
BAND_EDGES_MINUTES = (10, 15, 22, 33, 48, 70, 105, 150, 220, 320, 420, 570)


def _bands() -> tuple[tuple[timedelta, timedelta, str], ...]:
    """Contiguous bands from the edges, each named for its lower edge."""
    return tuple(
        (
            timedelta(minutes=low),
            timedelta(minutes=high),
            f"{low}m",
        )
        for low, high in pairwise(BAND_EDGES_MINUTES)
    )


AGE_BANDS: tuple[tuple[timedelta, timedelta, str], ...] = _bands()

# The band the factor is measured against: a post's value here is what
# the factor relates to its channel's mature median.
#
# Still the band holding the eight-hour mark, so `factor` means what it
# has always meant and the figures stored by earlier runs stay comparable
# with the ones stored by later ones. With the window ending at 9.5h this
# is also the last band, so no fitted fraction exceeds 1 today — that is
# a consequence of where the window ends, not a rule, and a wider window
# would simply fit fractions above 1 for the bands past it.
REFERENCE_BAND = next(
    name for low, high, name in AGE_BANDS if low <= timedelta(hours=8) < high
)


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
    """Which fitted band an age falls in, or ``None`` outside them all.

    The bands are contiguous, so ``None`` now means only what it says:
    the reading is younger than the first edge or older than the alerting
    window. It no longer means "this age happens to sit between two
    sample offsets", which is what it used to mean for 84% of readings.
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


def fit_band_spreads(
    residuals: Mapping[str, Sequence[float]], *, min_samples: int = 20
) -> dict[str, float]:
    """One spread per band, for the bands that have enough to measure.

    A band absent from the result is not an error and not a zero: the
    caller falls back to the metric's pooled spread, because a slightly
    wrong ruler at one age costs far less than no scoring at that age.
    That is the same trade the contiguous bands were introduced to make,
    applied to the dispersion instead of the shape.
    """
    fitted = {}
    for band, values in residuals.items():
        spread = fit_spread(values, min_samples=min_samples)
        if spread is not None:
            fitted[band] = spread
    return fitted
