"""Fitting curves and scoring against them.

Pure arithmetic, so every test states its inputs. The cases that matter
are the three the whole feature exists for — that channel size drops out,
that age drops out, and that an ordinary post scores near nothing — plus
the refusals, because every ``None`` here is a decision not to answer
rather than an answer of zero.
"""

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from itgraph.scoring.curves import (
    AGE_BANDS,
    REFERENCE_SETTLED,
    Curve,
    Observation,
    band_of,
    fit_band_spreads,
    fit_curve,
    fit_factor,
    fit_spread,
    median,
    recent_reference,
    reference_at,
)
from itgraph.scoring.fold import Head, burst_heads, fold
from itgraph.scoring.score import (
    Baseline,
    Score,
    Spike,
    expected,
    score_metric,
    score_post,
)

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
        reference=median_views,
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
        reference=1000.0,
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
        reference=1000.0,
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


def test_a_band_seen_on_too_few_posts_is_not_fitted() -> None:
    """Twenty readings can be four posts, and four posts are too narrow.

    The 18 October band met the reading minimum and came out at a third
    of the pooled spread; counting posts is what refuses it.
    """
    values = [n / 100 for n in range(-50, 51)]
    fitted = fit_band_spreads(
        {"150m": values, "220m": values},
        min_samples=10,
        posts={"150m": 4, "220m": 120},
        min_posts=100,
    )

    assert "150m" not in fitted
    assert "220m" in fitted


# --- what counts as one post, and one event -------------------------

T0 = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


def test_messages_seconds_apart_are_one_post() -> None:
    """Text, then each video: chained by the gaps, named by the first."""
    heads = burst_heads(
        [
            ((1, 41), T0),
            ((1, 42), T0 + timedelta(seconds=14)),
            ((1, 43), T0 + timedelta(seconds=62)),
            ((1, 44), T0 + timedelta(seconds=85)),
        ],
        gap=timedelta(seconds=60),
    )

    # 85 s end to end, but no single gap reaches a minute.
    assert set(heads.values()) == {(1, 41)}


def test_messages_minutes_apart_are_separate_posts() -> None:
    heads = burst_heads(
        [((1, 41), T0), ((1, 42), T0 + timedelta(minutes=3))],
        gap=timedelta(seconds=60),
    )

    assert heads == {(1, 41): (1, 41), (1, 42): (1, 42)}


def test_bursts_never_cross_channels() -> None:
    """Two channels posting in the same second are two posts."""
    heads = burst_heads(
        [((1, 41), T0), ((2, 7), T0), ((1, 42), T0)],
        gap=timedelta(seconds=60),
    )

    assert heads[(2, 7)] == (2, 7)
    assert heads[(1, 42)] == (1, 41)


def test_a_zero_gap_joins_nothing() -> None:
    heads = burst_heads(
        [((1, 41), T0), ((1, 42), T0)], gap=timedelta(seconds=0)
    )

    assert heads[(1, 42)] == (1, 42)


def crossing(
    channel: int, msg: int, *, at: timedelta, z: float = 4.0
) -> Spike:
    """A spike of one post that crossed `at` after T0."""
    return Spike(
        post_key=(channel, msg),
        score=Score(
            metric="views",
            age=timedelta(hours=2),
            observed=400.0,
            expected=100.0,
            z=z,
        ),
        crossed_at=T0 + at,
    )


WINDOW = timedelta(hours=6)


def test_a_channel_crossing_together_has_one_head() -> None:
    """The whole series of 15 October, as one event."""
    spikes = [crossing(1, msg, at=timedelta(0)) for msg in range(48, 55)]

    placed = fold(spikes, heads={}, window=WINDOW)

    heads = [entry for entry in placed if entry.into is None]
    folded = [entry for entry in placed if entry.into is not None]
    assert len(heads) == 1
    assert len(folded) == 6
    assert all(entry.into is heads[0].spike for entry in folded)


def test_the_head_is_the_earliest_crossing_whatever_the_scores() -> None:
    late_but_bigger = crossing(1, 2, at=timedelta(minutes=30), z=9.0)
    early = crossing(1, 1, at=timedelta(0), z=3.5)

    placed = fold([late_but_bigger, early], heads={}, window=WINDOW)

    heads = [entry.spike for entry in placed if entry.into is None]
    assert heads == [early]


def test_among_simultaneous_crossings_the_highest_score_heads() -> None:
    placed = fold(
        [
            crossing(1, 1, at=timedelta(0), z=4.0),
            crossing(1, 2, at=timedelta(0), z=6.0),
        ],
        heads={},
        window=WINDOW,
    )

    heads = [entry.spike.post_key for entry in placed if entry.into is None]
    assert heads == [(1, 2)]


def test_a_crossing_after_the_window_opens_a_new_event() -> None:
    """The next day's boost is a new message, not a fold into yesterday."""
    placed = fold(
        [
            crossing(1, 1, at=timedelta(0)),
            crossing(1, 2, at=timedelta(hours=18)),
        ],
        heads={},
        window=WINDOW,
    )

    assert [entry.into for entry in placed] == [None, None]


def test_the_window_is_anchored_at_the_head() -> None:
    """Sliding, crossings every four hours would chain forever."""
    placed = fold(
        [
            crossing(1, 1, at=timedelta(0)),
            crossing(1, 2, at=timedelta(hours=4)),
            crossing(1, 3, at=timedelta(hours=8)),
        ],
        heads={},
        window=WINDOW,
    )

    folded = {entry.spike.post_key: entry.into for entry in placed}
    assert folded[(1, 2)] is not None
    assert folded[(1, 3)] is None


def test_two_channels_never_fold_together() -> None:
    placed = fold(
        [crossing(1, 1, at=timedelta(0)), crossing(2, 1, at=timedelta(0))],
        heads={},
        window=WINDOW,
    )

    assert [entry.into for entry in placed] == [None, None]


def test_a_crossing_folds_into_a_head_already_in_the_queue() -> None:
    raised = Head(alert_id=17, post_key=(1, 1), raised_at=T0)

    placed = fold(
        [crossing(1, 2, at=timedelta(hours=2))],
        heads={1: raised},
        window=WINDOW,
    )

    assert placed[0].into is raised


def test_a_head_in_the_queue_past_its_window_is_not_folded_into() -> None:
    raised = Head(alert_id=17, post_key=(1, 1), raised_at=T0)

    placed = fold(
        [crossing(1, 2, at=timedelta(hours=7))],
        heads={1: raised},
        window=WINDOW,
    )

    assert placed[0].into is None


# --- a channel's own newest posts -----------------------------------


def test_the_newest_posts_make_the_reference() -> None:
    """A channel that grew is measured against what it is now."""
    old, new = [100.0] * 30, [900.0] * 20

    reference = recent_reference(old + new, newest=20, minimum=10)

    assert reference == (900.0, 20)


def test_too_few_posts_make_no_reference() -> None:
    """Refused, not a median of three: the channel has its fallback."""
    assert recent_reference([500.0] * 9, newest=20, minimum=10) is None


def test_fewer_than_newest_but_enough_still_count() -> None:
    assert recent_reference([500.0] * 12, newest=20, minimum=10) == (500.0, 12)


def history(*pairs: tuple[timedelta, float]) -> list[tuple[datetime, float]]:
    """A channel's posts as (published before T0, value), oldest first."""
    return sorted((T0 - ago, value) for ago, value in pairs)


def test_a_reference_never_contains_the_post_or_what_followed() -> None:
    """The residual's ruler, as scoring would have met it."""
    posts = history(
        *[(timedelta(days=d), 100.0) for d in range(2, 12)],
        (timedelta(0), 10_000.0),  # the post itself
        (-timedelta(hours=3), 10_000.0),  # published after it
    )

    assert reference_at(
        posts, T0, newest=20, minimum=10, max_age=timedelta(days=90)
    ) == (100.0, 10)


def test_a_post_whose_band_is_still_open_is_left_out() -> None:
    """Its largest reading so far is not its largest reading."""
    settled = [(timedelta(days=d), 100.0) for d in range(2, 12)]
    still_open = REFERENCE_SETTLED - timedelta(minutes=1)
    posts = history(*settled, (still_open, 1.0))

    reference = reference_at(
        posts, T0, newest=20, minimum=10, max_age=timedelta(days=90)
    )

    assert reference == (100.0, 10)


def test_the_maximum_age_cuts() -> None:
    """A slow channel's reference may not be older than the cap."""
    posts = history(
        *[(timedelta(days=100 + d), 100.0) for d in range(10)],
        *[(timedelta(days=d), 300.0) for d in range(2, 7)],
    )

    assert (
        reference_at(
            posts, T0, newest=20, minimum=10, max_age=timedelta(days=90)
        )
        is None
    )
    assert reference_at(
        posts, T0, newest=20, minimum=5, max_age=timedelta(days=90)
    ) == (300.0, 5)


# --- comments, scored as counts ------------------------------------------------

# Comments' shape as measured for personal channels on 2026-11-07, as a
# ratio of sums: about a quarter by fifteen minutes, all of it by eight hours.
COMMENTS_CURVE = Curve(
    fractions={"15m": 0.27, "48m": 0.59, "220m": 0.85, "420m": 1.0},
    samples=dict.fromkeys(("15m", "48m", "220m", "420m"), 500),
)


def comments(mean: float = 1.0, dispersion: float | None = 1.58) -> Baseline:
    """A channel whose newest posts average `mean` comments at eight hours."""
    return Baseline(
        reference=mean,
        factor=1.0,
        curve=COMMENTS_CURVE,
        spread=1.1,
        dispersion=dispersion,
    )


EIGHT_HOURS = timedelta(hours=8)


def test_no_comments_on_a_quiet_channel_scores_below_zero() -> None:
    score = score_metric("comments", 0.0, EIGHT_HOURS, comments(1.0))
    assert score is not None
    assert score.z < 0


def test_thirty_comments_on_a_channel_of_about_one_cross() -> None:
    score = score_metric("comments", 30.0, EIGHT_HOURS, comments(1.0))
    assert score is not None
    assert score.z >= 3.7
    assert score.expected == pytest.approx(1.0)
    assert score.observed == 30.0


def test_a_kind_whose_discussions_vary_widely_needs_more() -> None:
    calm = score_metric("comments", 12.0, EIGHT_HOURS, comments(1.0, 0.4))
    wild = score_metric("comments", 12.0, EIGHT_HOURS, comments(1.0, 12.9))
    assert calm is not None and wild is not None
    assert wild.z < calm.z


def test_a_young_post_is_measured_against_its_age() -> None:
    """Ten at fifteen minutes is more than ten at eight hours."""
    young = score_metric(
        "comments", 10.0, timedelta(minutes=16), comments(4.0)
    )
    settled = score_metric("comments", 10.0, EIGHT_HOURS, comments(4.0))
    assert young is not None and settled is not None
    assert young.expected == pytest.approx(4.0 * 0.27)
    assert young.z > settled.z


def test_a_run_without_a_dispersion_scores_no_comments() -> None:
    assert (
        score_metric("comments", 30.0, EIGHT_HOURS, comments(1.0, None))
        is None
    )


def test_a_count_past_the_arithmetic_is_a_finite_ceiling() -> None:
    score = score_metric(
        "comments", 100_000.0, EIGHT_HOURS, comments(1.0, 0.05)
    )
    assert score is not None
    assert score.z == 40.0


def test_a_channel_whose_posts_had_no_comments_is_floored_not_refused() -> (
    None
):
    score = score_metric("comments", 10.0, EIGHT_HOURS, comments(0.0))
    assert score is not None
    assert score.expected == pytest.approx(0.05)
    assert 3.7 < score.z < 40.0


def test_a_dispersion_changes_nothing_for_the_other_metrics() -> None:
    plain = score_metric("views", 900.0, EIGHT_HOURS, baseline())
    with_one = score_metric(
        "views",
        900.0,
        EIGHT_HOURS,
        Baseline(
            reference=1000.0,
            factor=0.44,
            curve=VIEWS_CURVE,
            spread=0.38,
            dispersion=1.58,
        ),
    )
    assert plain == with_one


def test_the_counted_metrics_are_the_enums() -> None:
    from itgraph.db.models import Metric
    from itgraph.scoring.score import COUNTED

    assert COUNTED == {Metric.COMMENTS.value}


# --- comment baselines ---------------------------------------------------------


def test_the_comment_reference_keeps_the_posts_nobody_commented_on() -> None:
    from itgraph.scoring.curves import recent_mean

    values = [0.0] * 18 + [10.0, 10.0]
    assert recent_mean(values, newest=20, minimum=10) == (1.0, 20)
    # The median of the commented ones would have said ten.
    assert recent_reference(values, newest=20, minimum=2) == (10.0, 2)


def test_a_comment_reference_needs_ten_posts() -> None:
    from itgraph.scoring.curves import recent_mean

    assert recent_mean([0.0] * 9, newest=20, minimum=10) is None
    assert recent_mean([0.0] * 10, newest=20, minimum=10) == (0.0, 10)


def test_a_counted_reference_at_a_moment_is_a_mean() -> None:
    moment = datetime(2026, 11, 7, tzinfo=UTC)
    history = [
        (moment - timedelta(days=d), 3.0 if d % 5 == 0 else 0.0)
        for d in range(25, 0, -1)
    ]
    found = reference_at(
        history,
        moment,
        newest=20,
        minimum=10,
        max_age=timedelta(days=90),
        counted=True,
    )
    assert found == (pytest.approx(0.6), 20)


def test_zeros_shape_the_comment_curve() -> None:
    """Fifteen minutes is mostly zeros, and the fraction has to say so."""
    from itgraph.scoring.curves import fit_count_curve

    observations = []
    for post in range(40):
        settled = 4.0 if post % 2 else 0.0
        early = 1.0 if post % 4 == 1 else 0.0
        observations += [
            Observation(
                post_key=(1, post), age=timedelta(minutes=16), value=early
            ),
            Observation(
                post_key=(1, post), age=timedelta(hours=8), value=settled
            ),
        ]

    counted = fit_count_curve(observations, min_samples=20)
    medians = fit_curve(observations, min_samples=5)

    # Ten early comments over eighty settled ones.
    assert counted.fractions["15m"] == pytest.approx(10 / 80)
    assert counted.samples["15m"] == 40
    # The median of the non-zero fractions knows only the posts that had some.
    assert medians.fractions["15m"] == pytest.approx(0.25)


def test_a_known_comment_dispersion_is_recovered_by_the_fit() -> None:
    """Posts drawn with dispersion 1.58, a reference of two at eight hours
    and three fifths of it at fifty minutes: the fit gives the 1.58 back."""
    import random

    from test_counts import negative_binomial

    from itgraph.scoring.refresh import _fit

    rng = random.Random(7)
    entries = []
    for post in range(500):
        for age, mean in (
            (timedelta(minutes=50), 1.2),
            (timedelta(hours=8), 2.0),
        ):
            entries.append(
                Observation(
                    post_key=(1, post),
                    age=age,
                    value=float(negative_binomial(rng, mean, 1.58)),
                )
            )

    fit = _fit(entries, {}, recent=lambda entry: 2.0, counted=True)

    assert not isinstance(fit, str)
    assert fit.factor == 1.0
    assert fit.curve.fractions["48m"] == pytest.approx(0.6, rel=0.15)
    assert fit.dispersion == pytest.approx(1.58, rel=0.2)


def test_a_comment_reference_never_holds_its_own_post() -> None:
    from itgraph.scoring.refresh import _rolling

    moment = datetime(2026, 11, 7, tzinfo=UTC)
    published = [moment - timedelta(days=d) for d in range(30, 0, -1)]
    history = {1: [(at, 0.0) for at in published[:-1]]}
    history[1].append((published[-1], 500.0))
    discussed = Observation(
        post_key=(1, 30),
        age=timedelta(hours=2),
        value=300.0,
        published=published[-1],
    )

    assert _rolling(history, counted=True)(discussed) == 0.0
