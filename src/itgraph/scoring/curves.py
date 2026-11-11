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

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

__all__ = [
    "AGE_BANDS",
    "BAND_EDGES_MINUTES",
    "REFERENCE_BAND",
    "REFERENCE_SETTLED",
    "TELEGRAM_BANDS",
    "YOUTUBE_BANDS",
    "YOUTUBE_BAND_EDGES_MINUTES",
    "AgeBands",
    "Curve",
    "Observation",
    "band_of",
    "fit_band_spreads",
    "fit_count_curve",
    "fit_curve",
    "fit_factor",
    "fit_spread",
    "median",
    "recent_mean",
    "recent_reference",
    "reference_at",
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

# YouTube's, measured on its own readings (`add-youtube-virality-scoring`).
# One scheduled reading age inside each — 4, 8, 12, 18, 24 and 36 hours,
# then 2, 3, 4, 5 and 7 days — with room above it for a pass interval and
# for a deferred reading. From three hours, not one: the view counter lags
# in the first hours, zero on a tenth of videos at one hour and staying
# put between readings on a quarter at two, so what an earlier band would
# measure is the counter rather than the audience. To eight and a half
# days, past which nothing raises an alert.
YOUTUBE_BAND_EDGES_MINUTES = (
    180,
    360,
    600,
    900,
    1260,
    1800,
    2520,
    3600,
    5040,
    6480,
    8640,
    12240,
)


@dataclass(frozen=True, slots=True)
class AgeBands:
    """Contiguous age bands, and the one a reference is read in.

    Each band is named for its lower edge in minutes. The reference band is
    the one holding ``reference_age``: what a curve's fractions are of,
    what a channel's own reference is the median of, and what a factor
    relates to a mature median.

    A platform's bands are a value rather than module constants, so that
    one module fits Telegram's eight-hour posts and YouTube's week-long
    videos alike.
    """

    edges_minutes: tuple[int, ...]
    reference_age: timedelta
    bands: tuple[tuple[timedelta, timedelta, str], ...] = field(
        init=False, repr=False, compare=False
    )
    reference: str = field(init=False, compare=False)
    # How old a post must be before its reference-band value is final: the
    # band's upper edge. A post younger than this has a largest reading so
    # far in the band, not a largest reading, and a reference built from it
    # would be biased low by exactly the posts still being read.
    settled: timedelta = field(init=False, compare=False)

    def __post_init__(self) -> None:
        bands = tuple(
            (timedelta(minutes=low), timedelta(minutes=high), f"{low}m")
            for low, high in pairwise(self.edges_minutes)
        )
        held = [
            (name, high)
            for low, high, name in bands
            if low <= self.reference_age < high
        ]
        if not held:
            raise ValueError(
                f"no band holds the reference age {self.reference_age}"
            )
        object.__setattr__(self, "bands", bands)
        object.__setattr__(self, "reference", held[0][0])
        object.__setattr__(self, "settled", held[0][1])

    def band_of(self, age: timedelta) -> str | None:
        """Which band an age falls in, or ``None`` outside them all."""
        for low, high, name in self.bands:
            if low <= age < high:
                return name
        return None

    @property
    def last_edge(self) -> timedelta:
        """The upper edge of the last band: the oldest age scored."""
        return self.bands[-1][1]


# The band the factor is measured against holds the eight-hour mark, so
# `factor` means what it has always meant and the figures stored by earlier
# runs stay comparable with the ones stored by later ones. With the window
# ending at 9.5h this is also the last band, so no fitted fraction exceeds
# 1 — a consequence of where the window ends, not a rule.
TELEGRAM_BANDS = AgeBands(
    edges_minutes=BAND_EDGES_MINUTES, reference_age=timedelta(hours=8)
)

# Twenty-four hours: early enough for a channel's own reference to fill in
# weeks, late enough that the view counter has caught up. Every fraction
# past it is above 1, since YouTube's views still grow at a week.
YOUTUBE_BANDS = AgeBands(
    edges_minutes=YOUTUBE_BAND_EDGES_MINUTES, reference_age=timedelta(hours=24)
)

# Telegram's, under the names every caller before YouTube used.
AGE_BANDS: tuple[tuple[timedelta, timedelta, str], ...] = TELEGRAM_BANDS.bands
REFERENCE_BAND = TELEGRAM_BANDS.reference
REFERENCE_SETTLED = TELEGRAM_BANDS.settled


@dataclass(frozen=True, slots=True)
class Observation[C, P]:
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

    ``published`` is when the post went out, and it is what a residual
    needs to know which reference the post would have been scored
    against — the one built from the posts settled before it. ``None``
    where nobody asked, which takes the channel's fallback reference.
    """

    post_key: tuple[C, P]
    age: timedelta
    value: float
    published: datetime | None = None


@dataclass(frozen=True, slots=True)
class Curve:
    """A fitted shape: fraction of the reference value, per age band.

    ``samples`` travels with it because a band fitted on eleven
    observations and one fitted on two thousand are not the same claim,
    and whoever reads a threshold later needs to be able to tell.
    """

    fractions: dict[str, float]
    samples: dict[str, int]
    # The bands the fractions are named by. Telegram's unless said: every
    # curve stored before YouTube was Telegram's.
    bands: AgeBands = TELEGRAM_BANDS

    def at(self, age: timedelta) -> float | None:
        """The fraction for this age, or ``None`` outside every band."""
        band = self.bands.band_of(age)
        return None if band is None else self.fractions.get(band)


def band_of(age: timedelta, bands: AgeBands = TELEGRAM_BANDS) -> str | None:
    """Which fitted band an age falls in, or ``None`` outside them all.

    The bands are contiguous, so ``None`` now means only what it says:
    the reading is younger than the first edge or older than the alerting
    window. It no longer means "this age happens to sit between two
    sample offsets", which is what it used to mean for 84% of readings.
    """
    return bands.band_of(age)


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


def _reference[C, P](
    observations: Iterable[Observation[C, P]],
    *,
    keep_zeros: bool = False,
    bands: AgeBands = TELEGRAM_BANDS,
) -> dict[tuple[C, P], float]:
    """Each post's value in the reference band, where it has one.

    A post read twice inside the band contributes its largest reading:
    counters only rise, so the later one is the more complete, and taking
    a median of two would understate a post the loop happened to sample
    early in the window.
    """
    reference: dict[tuple[C, P], float] = {}
    for entry in observations:
        if bands.band_of(entry.age) != bands.reference:
            continue
        if entry.value < 0 or (entry.value == 0 and not keep_zeros):
            continue
        current = reference.get(entry.post_key)
        if current is None or entry.value > current:
            reference[entry.post_key] = entry.value
    return reference


def fit_curve[C, P](
    observations: Sequence[Observation[C, P]],
    *,
    min_samples: int = 20,
    bands: AgeBands = TELEGRAM_BANDS,
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
    reference = _reference(observations, bands=bands)

    gathered: dict[str, list[float]] = {}
    for entry in observations:
        base = reference.get(entry.post_key)
        if base is None or base <= 0 or entry.value <= 0:
            continue
        band = bands.band_of(entry.age)
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
        bands=bands,
    )


def fit_count_curve[C, P](
    observations: Sequence[Observation[C, P]],
    *,
    min_samples: int = 20,
    bands: AgeBands = TELEGRAM_BANDS,
) -> Curve:
    """A counted metric's shape: per band, a ratio of sums, zeros kept.

    ``fit_curve`` takes the median of each post's fraction over non-zero
    readings, which for comments discards half the data — the posts
    nobody had commented on yet — and describes the shape of the posts
    that already had some. Here every reading of a post with a reading at
    eight hours counts, and the band's fraction is what its readings sum
    to over what those posts reached at eight hours.
    """
    reference = _reference(observations, keep_zeros=True, bands=bands)
    sums: dict[str, tuple[float, float, int]] = {}
    for entry in observations:
        base = reference.get(entry.post_key)
        if base is None or entry.value < 0:
            continue
        band = bands.band_of(entry.age)
        if band is None:
            continue
        reached, possible, count = sums.get(band, (0.0, 0.0, 0))
        sums[band] = (reached + entry.value, possible + base, count + 1)
    return Curve(
        fractions={
            band: reached / possible
            for band, (reached, possible, count) in sums.items()
            if count >= min_samples and possible > 0
        },
        samples={band: count for band, (_, _, count) in sums.items()},
        bands=bands,
    )


def fit_factor[C, P](
    observations: Sequence[Observation[C, P]],
    mature: Mapping[C, float],
    *,
    min_samples: int = 20,
    bands: AgeBands = TELEGRAM_BANDS,
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
        for (channel, _), value in _reference(
            observations, bands=bands
        ).items()
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
    residuals: Mapping[str, Sequence[float]],
    *,
    min_samples: int = 20,
    posts: Mapping[str, int] | None = None,
    min_posts: int = 1,
) -> dict[str, float]:
    """One spread per band, for the bands that have enough to measure.

    A band absent from the result is not an error and not a zero: the
    caller falls back to the metric's pooled spread, because a slightly
    wrong ruler at one age costs far less than no scoring at that age.
    That is the same trade the contiguous bands were introduced to make,
    applied to the dispersion instead of the shape.

    **Enough is counted in posts as well as readings.** A post is read
    several times inside one band, so twenty readings can be four posts,
    and a spread from few posts is biased *low* — the direction that
    over-alerts. Bootstrapped over well-measured bands, the 5th
    percentile of the estimate from k posts runs 0.52 of the full one at
    20 posts and 0.78 at 100. ``posts`` is each band's count of distinct
    posts; a band under ``min_posts`` is left out like a thin one.
    """
    fitted = {}
    for band, values in residuals.items():
        if posts is not None and posts.get(band, 0) < min_posts:
            continue
        spread = fit_spread(values, min_samples=min_samples)
        if spread is not None:
            fitted[band] = spread
    return fitted


def recent_reference(
    values: Sequence[float], *, newest: int, minimum: int
) -> tuple[float, int] | None:
    """The median of a channel's newest reference-band values, and how
    many it was taken over — or ``None`` under the minimum.

    ``values`` is ordered by publication, oldest first, so "newest" is a
    property of the input rather than of a sort this function would have
    to guess the key of.

    **Newest posts, not a window of days.** A window lets a prolific
    channel fill it with hundreds of posts and lag its own growth by half
    of it, while a slow one barely fills it; counting posts follows both.
    Measured rolling out of sample: a 90-day window left a channel that
    grew ninefold at +3.8 z, its newest twenty at +1.3. Under the minimum
    is refused rather than taken thinly — a median of three is not a
    reference, and the channel still has its fallback.
    """
    taken = [value for value in values[-newest:] if value > 0]
    if len(taken) < minimum:
        return None
    return median(taken), len(taken)


def recent_mean(
    values: Sequence[float], *, newest: int, minimum: int
) -> tuple[float, int] | None:
    """A counted metric's reference: the mean of the newest, zeros kept.

    The mean, because it is the parameter the count is scored with; zeros
    kept, because a channel whose posts mostly had no comments is a
    channel where comments are rare, and a median of the commented ones
    would describe a different channel. Twenty posts of which two had ten
    comments is a reference of one, not ten.
    """
    taken = list(values[-newest:])
    if len(taken) < minimum:
        return None
    return sum(taken) / len(taken), len(taken)


def reference_at(
    history: Sequence[tuple[datetime, float]],
    moment: datetime,
    *,
    newest: int,
    minimum: int,
    max_age: timedelta,
    counted: bool = False,
    bands: AgeBands = TELEGRAM_BANDS,
) -> tuple[float, int] | None:
    """The reference a channel had at ``moment``.

    Built from posts whose reference band had closed by then — published
    at least ``bands.settled`` earlier, so each value is final — and
    no older than ``max_age``. ``history`` is the channel's posts as
    (published, reference-band value), oldest first.

    One function for both uses, which is the point. At a refresh,
    ``moment`` is the refresh and this is the stored reference. For a
    residual, ``moment`` is the post's own publication, and this is the
    reference it would have been scored against: it cannot contain the
    post, nor anything published after it. A reference that contains
    the posts it is measured on fits them, and the ruler comes out too
    narrow — measured, by 5%, in the over-alerting direction.
    """
    upper = bisect_right(history, moment - bands.settled, key=_published)
    lower = bisect_left(history, moment - max_age, key=_published)
    window = [value for _, value in history[lower:upper]]
    if counted:
        return recent_mean(window, newest=newest, minimum=minimum)
    return recent_reference(window, newest=newest, minimum=minimum)


def _published(entry: tuple[datetime, float]) -> datetime:
    return entry[0]
