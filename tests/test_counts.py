"""The negative binomial tail, its z, and the dispersion fitted by likelihood."""

import math
import random
from itertools import pairwise

import pytest

from itgraph.scoring.counts import (
    CEILING,
    count_z,
    fit_dispersion,
    log_pmf,
    tail,
)

# (observed, mean, dispersion, P(X >= observed), z), computed once with
# scipy.stats.nbinom.sf and norm.isf on 2026-11-07; scipy is not a
# dependency, so the numbers are written down rather than recomputed.
REFERENCE = [
    (1, 0.5, 1.0, 0.33333333333333337, 0.43072729929545744),
    (5, 1.0, 1.58, 0.0430248874041654, 1.7166137154359997),
    (30, 1.0, 1.58, 1.1451149775756012e-07, 5.174091159644633),
    (117, 0.5, 1.58, 4.1583983052226335e-43, 13.714473445539733),
    (10, 0.05, 1.58, 1.3588505843821072e-12, 6.9915993240786625),
    (1647, 9.3, 5.39, 8.630875510217447e-17, 8.239717661321722),
    (900, 300.0, 1.58, 0.07329452781143077, 1.451685562084144),
    (3, 2.0, 0.05, 0.32311868560906487, 0.45899553670778653),
    (50, 2.0, 0.05, 6.6909095350956934e-37, 12.635920906144182),
    (20, 1.0, 16.8, 0.011249417475915808, 2.2818392093469098),
]


@pytest.mark.parametrize(
    ("observed", "mean", "dispersion", "expected", "z"), REFERENCE
)
def test_the_tail_and_z_are_scipys(
    observed: int, mean: float, dispersion: float, expected: float, z: float
) -> None:
    assert tail(observed, mean, dispersion) == pytest.approx(
        expected, rel=1e-9
    )
    assert count_z(observed, mean, dispersion) == pytest.approx(z, abs=1e-7)


@pytest.mark.parametrize("dispersion", [0.05, 0.5, 1.58, 12.9])
@pytest.mark.parametrize("mean", [0.3, 1.0, 4.0])
def test_the_tail_is_the_sum_of_the_terms_it_leaves_out(
    mean: float, dispersion: float
) -> None:
    """Computed the other way: one minus the probabilities below."""
    for observed in range(1, 15):
        below = sum(
            math.exp(log_pmf(k, mean, dispersion)) for k in range(observed)
        )
        assert tail(observed, mean, dispersion) == pytest.approx(
            1.0 - below, rel=1e-9, abs=1e-14
        )


def test_the_probabilities_sum_to_one() -> None:
    total = sum(math.exp(log_pmf(k, 2.0, 1.58)) for k in range(20_000))
    assert total == pytest.approx(1.0, abs=1e-9)


def test_more_comments_are_less_probable() -> None:
    tails = [tail(k, 1.5, 1.58) for k in range(1, 200)]
    assert all(a > b for a, b in pairwise(tails))
    zs = [count_z(k, 1.5, 1.58) for k in range(1, 200)]
    assert all(a <= b for a, b in pairwise(zs))


def test_no_comments_is_the_floor_and_an_underflow_the_ceiling() -> None:
    assert count_z(0, 1.0, 1.58) == -CEILING
    assert tail(0, 1.0, 1.58) == 1.0
    # Far past what a double can hold: finite, not infinite, not missing.
    assert count_z(100_000, 1.0, 0.05) == CEILING


def test_a_tail_far_below_the_double_epsilon_is_still_computed() -> None:
    """1 − CDF would be exactly zero here; the continued fraction is not."""
    far = tail(117, 0.5, 1.58)
    assert 0.0 < far < 1e-40


def test_a_zero_mean_is_floored() -> None:
    """A channel whose newest posts had no comments is "almost never"."""
    assert count_z(10, 0.0, 1.58) == count_z(10, 0.05, 1.58)
    assert count_z(10, 0.0, 1.58) < CEILING


def negative_binomial(
    rng: random.Random, mean: float, dispersion: float
) -> int:
    """A gamma-Poisson draw: the negative binomial's own construction."""
    shape = 1.0 / dispersion
    rate = rng.gammavariate(shape, mean / shape)
    # Knuth's Poisson, fine for the small rates drawn here.
    limit, count, product = math.exp(-rate), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


@pytest.mark.parametrize("dispersion", [0.4, 1.58, 5.0])
def test_a_known_dispersion_is_recovered(dispersion: float) -> None:
    rng = random.Random(20261107)
    means = [rng.choice([0.3, 1.0, 2.5, 6.0]) for _ in range(6_000)]
    pairs = [
        (negative_binomial(rng, mean, dispersion), mean) for mean in means
    ]

    fitted = fit_dispersion(pairs)

    assert fitted is not None
    assert fitted == pytest.approx(dispersion, rel=0.15)


def test_nothing_to_fit_is_no_dispersion() -> None:
    assert fit_dispersion([]) is None
