"""Fitting curves and scoring against them.

Pure arithmetic, so every test states its inputs. The cases that matter
are the three the whole feature exists for — that channel size drops out,
that age drops out, and that an ordinary post scores near nothing — plus
the refusals, because every ``None`` here is a decision not to answer
rather than an answer of zero.
"""

import math
from datetime import timedelta
from itertools import pairwise

import pytest

from itgraph.scoring.curves import (
    AGE_BANDS,
    Curve,
    Observation,
    band_of,
    fit_band_spreads,
    fit_curve,
    fit_factor,
    fit_spread,
    median,
)
from itgraph.scoring.score import Baseline, expected, score_metric, score_post

# The measured shape of views, from `docs/PLAN.md`: a third of the
# eight-hour value by an hour, half by two hours. Named by the band each
# age now falls in — the bands are contiguous, so an hour is "48m" (the
# band 48–70 minutes) rather than a window centred on the hour.
VIEWS_CURVE = Curve(
    fractions={
        "15m": 0.17,
        "22m": 0.22,
        "48m": 0.33,
        "105m": 0.50,
        "220m": 0.76,
        "420m": 0.96,
    },
    samples=dict.fromkeys(("15m", "22m", "48m", "105m", "220m", "420m"), 500),
)


def baseline(median_views: float = 1000.0, spread: float = 0.38) -> Baseline:
    """A channel whose typical mature post reaches `median_views`."""
    return Baseline(
        mature_median=median_views,
        factor=0.44,
        curve=VIEWS_CURVE,
        spread=spread,
    )


def readings(*pairs: tuple[float, float]) -> list[Observation]:
    """Observations of one post, as (hours, value)."""
    return [
        Observation(post_key=(1, 1), age=timedelta(hours=h), value=v)
        for h, v in pairs
    ]


# --- bands ----------------------------------------------------------


def test_a_reading_lands_in_a_band_wherever_it_falls() -> None:
    """Contiguous, so no age inside the window is homeless.

    The six offset windows this replaced covered 273 minutes out of the
    whole window, which discarded 84% of readings.
    """
    assert band_of(timedelta(minutes=16)) == "15m"
    assert band_of(timedelta(minutes=62)) == "48m"
    # Three hours used to sit between the two-hour and four-hour fits.
    assert band_of(timedelta(hours=3)) == "150m"


def test_the_bands_leave_no_gap() -> None:
    """The property the whole change rests on, asserted directly."""
    for (_, high, _), (low, _, _) in pairwise(AGE_BANDS):
        assert high == low


def test_an_age_outside_the_window_belongs_to_no_band() -> None:
    """`None` now means only what it says: outside, not between."""
    assert band_of(timedelta(minutes=5)) is None
    assert band_of(timedelta(days=2)) is None


# --- fitting --------------------------------------------------------


def test_the_curve_is_a_fraction_of_each_posts_own_reference() -> None:
    """Normalising per post is what removes channel size from the shape."""
    small = [
        Observation((1, 1), timedelta(hours=1), 33.0),
        Observation((1, 1), timedelta(hours=8), 100.0),
    ]
    large = [
        Observation((2, 2), timedelta(hours=1), 33_000.0),
        Observation((2, 2), timedelta(hours=8), 100_000.0),
    ]
    curve = fit_curve(small * 10 + large * 10, min_samples=5)

    assert curve.fractions["48m"] == pytest.approx(0.33)


def test_a_post_without_a_reference_reading_is_left_out() -> None:
    """No denominator, and guessing one would bias the shape."""
    curve = fit_curve(
        [Observation((1, 1), timedelta(hours=1), 50.0)] * 30, min_samples=5
    )

    assert curve.fractions == {}


def test_a_thin_band_is_omitted_rather_than_fitted() -> None:
    """A gap is a gap; a band fitted on four points is a wrong number."""
    observations = [
        Observation((n, n), timedelta(hours=8), 100.0) for n in range(30)
    ] + [Observation((0, 0), timedelta(hours=1), 30.0)]

    curve = fit_curve(observations, min_samples=20)

    assert "420m" in curve.fractions
    assert "48m" not in curve.fractions
    # ...and the count is still reported, so the gap is explicable.
    assert curve.samples["48m"] == 1


def test_the_factor_joins_the_curve_to_history() -> None:
    """Without it the two halves of the estimate are in different units."""
    observations = [
        Observation((1, n), timedelta(hours=8), 440.0) for n in range(30)
    ]
    factor = fit_factor(observations, {1: 1000.0}, min_samples=20)

    assert factor == pytest.approx(0.44)


def test_too_few_posts_means_no_factor() -> None:
    """Better none than one fitted on six posts and scaling every score."""
    observations = [Observation((1, 1), timedelta(hours=8), 440.0)]

    assert fit_factor(observations, {1: 1000.0}, min_samples=20) is None


def test_the_spread_is_robust_to_the_thing_it_measures() -> None:
    """The tail is what is being detected; it must not widen the ruler.

    Stated against the alternative, because that is the whole reason for
    the choice: a standard deviation would take the two spikes as
    evidence that this channel simply varies a lot, widen accordingly,
    and stop scoring the next spike as unusual. The estimator must not
    learn from the thing it exists to find.
    """
    ordinary = [n / 100 for n in range(-50, 51)]
    with_spike = ordinary + [80.0, 90.0]

    robust_before = fit_spread(ordinary, min_samples=10)
    robust_after = fit_spread(with_spike, min_samples=10)
    assert robust_before is not None and robust_after is not None

    def stdev(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

    # The robust estimate barely moves; the naive one nearly quadruples.
    assert robust_after == pytest.approx(robust_before, rel=0.05)
    assert stdev(with_spike) > stdev(ordinary) * 3


def test_a_spread_of_zero_is_no_spread() -> None:
    """A zero denominator makes every deviation infinite, not certain."""
    assert fit_spread([0.0] * 50, min_samples=10) is None


def test_the_median_is_the_median() -> None:
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5


# --- scoring --------------------------------------------------------


def test_channel_size_drops_out() -> None:
    """The first of the three things this feature exists for.

    The same absolute reach is remarkable on a small channel and ordinary
    on a large one.
    """
    age = timedelta(hours=1)
    small = score_metric("views", 500.0, age, baseline(1000.0))
    large = score_metric("views", 500.0, age, baseline(100_000.0))

    assert small is not None and large is not None
    assert small.z > large.z


def test_age_drops_out() -> None:
    """The second. The same reach at one hour is further along than at eight."""
    young = score_metric("views", 500.0, timedelta(hours=1), baseline())
    old = score_metric("views", 500.0, timedelta(hours=8), baseline())

    assert young is not None and old is not None
    assert young.z > old.z


def test_an_ordinary_post_scores_near_nothing() -> None:
    """The third, and the one that makes a threshold mean anything.

    A post reaching exactly what the estimate says must land at zero, or
    every threshold is measuring the calibration error instead.
    """
    reference = expected(baseline(), timedelta(hours=2))
    assert reference is not None

    score = score_metric("views", reference, timedelta(hours=2), baseline())

    assert score is not None
    assert score.z == pytest.approx(0.0)


def test_the_multiple_reads_the_way_a_person_would() -> None:
    """A z is comparable and means nothing to anyone; a multiple is the reverse."""
    reference = expected(baseline(), timedelta(hours=2))
    assert reference is not None

    score = score_metric(
        "views", reference * 3, timedelta(hours=2), baseline()
    )

    assert score is not None
    assert score.times_expected == pytest.approx(3.0)
    assert score.z == pytest.approx(math.log(3) / 0.38, rel=1e-6)


def test_the_spread_decides_what_a_threshold_means() -> None:
    """0.38 for views against 1.01 for comments is not a detail.

    At the same z, a wider spread demands a far larger excursion — which
    is why one hardcoded spread would have fired reaction alerts at half
    the intended distance into the tail.
    """
    narrow = score_metric(
        "views", 3000.0, timedelta(hours=2), baseline(spread=0.38)
    )
    wide = score_metric(
        "views", 3000.0, timedelta(hours=2), baseline(spread=1.01)
    )

    assert narrow is not None and wide is not None
    assert narrow.z > wide.z * 2


def test_an_unmeasured_metric_is_not_a_zero() -> None:
    """The same absent-is-not-zero distinction `derive/metrics.py` keeps."""
    assert (
        score_metric("reactions", None, timedelta(hours=1), baseline()) is None
    )


def test_a_channel_without_a_baseline_is_not_scored() -> None:
    assert score_metric("views", 500.0, timedelta(hours=1), None) is None


def test_an_age_outside_the_window_is_not_scored() -> None:
    assert score_metric("views", 500.0, timedelta(days=2), baseline()) is None


def test_a_reading_of_zero_is_still_scored() -> None:
    """A post nobody reacted to, on a channel where they usually do.

    Dropping it would quietly exclude the quietest posts from being
    measured at all; the logarithm is floored instead.
    """
    score = score_metric("views", 0.0, timedelta(hours=2), baseline())

    assert score is not None
    assert score.z < 0


def test_the_highest_metric_comes_first() -> None:
    """One post raises one alert, and this decides which."""
    scores = score_post(
        {"views": 5000.0, "forwards": 1.0},
        timedelta(hours=2),
        {"views": baseline(1000.0), "forwards": baseline(50.0)},
        metrics=("views", "forwards"),
    )

    assert [entry.metric for entry in scores] == ["views", "forwards"]


def test_a_metric_can_be_measured_and_excluded_from_alerting() -> None:
    """How comments are handled until they can be measured well enough.

    The baseline is computed and stored like any other; only the alerting
    is off, so re-enabling it is configuration rather than code.
    """
    scores = score_post(
        {"views": 5000.0, "comments": 900.0},
        timedelta(hours=2),
        {"views": baseline(), "comments": baseline(10.0)},
        metrics=("views",),
    )

    assert [entry.metric for entry in scores] == ["views"]


# --- the ruler changes with age -------------------------------------


def test_the_spread_is_taken_from_the_readings_own_band() -> None:
    """1.18 at fifteen minutes against 0.98 at eight hours, measured.

    One figure for every age would make the threshold mean something
    different at each point of a post's life without saying so, which is
    the one thing a score comparable across posts may not do.
    """
    wide = Baseline(
        mature_median=1000.0,
        factor=0.44,
        curve=VIEWS_CURVE,
        spread=0.38,
        band_spreads={"15m": 0.76, "420m": 0.38},
    )
    young, old = timedelta(minutes=16), timedelta(hours=8)
    # The same excursion at both ages: three times what was expected.
    early = score_metric("views", expected(wide, young) * 3, young, wide)
    late = score_metric("views", expected(wide, old) * 3, old, wide)

    assert early is not None and late is not None
    assert early.times_expected == pytest.approx(late.times_expected)
    # Identical in the raw, halved by the wider early ruler.
    assert early.z == pytest.approx(late.z / 2)


def test_a_band_without_its_own_spread_falls_back() -> None:
    """A slightly wrong ruler at one age beats a hole at that age.

    The same trade the contiguous bands were introduced to make, applied
    to the dispersion rather than to the shape.
    """
    partial = Baseline(
        mature_median=1000.0,
        factor=0.44,
        curve=VIEWS_CURVE,
        spread=0.38,
        band_spreads={"420m": 0.30},
    )

    scored = score_metric("views", 400.0, timedelta(minutes=16), partial)

    assert scored is not None
    assert partial.spread_at(timedelta(minutes=16)) == 0.38


def test_fitting_skips_a_band_too_thin_to_measure() -> None:
    """Absent, so the caller falls back rather than trusting four points."""
    fitted = fit_band_spreads(
        {
            "15m": [n / 100 for n in range(-50, 51)],
            "22m": [0.1, -0.1, 0.2],
        },
        min_samples=10,
    )

    assert "15m" in fitted
    assert "22m" not in fitted
