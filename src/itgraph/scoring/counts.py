"""How improbable a count is, for a counter too sparse for a logarithm.

Comments are zero in half their readings. ``log(actual / expected)`` cannot
describe that: small counts carry most of its dispersion, so the spread
comes out near 1.1 and a post needs some sixty times its channel's usual
count to cross the threshold, while three hundred comments where a hundred
are usual — rare on any channel — reads as ordinary. A count is scored here
as a count instead: negative binomial, with the expectation as its mean and
a dispersion measured per channel kind, and the probability of a count at
least as large read as the z with the same upper tail.

That z goes where every other z goes — onto the alert, against the shared
threshold, into the ranking that gives a post one alert under its strongest
metric. A probability next to a z would not be comparable; the tail's z is.

Pure arithmetic over plain numbers: no database, no settings, and no
dependency. The image installs neither numpy nor scipy, and the far tail
has to be computed rather than cancelled out of ``1 − CDF``, so the
regularised incomplete beta is written out below.
"""

import math
from collections import Counter
from collections.abc import Iterable
from statistics import NormalDist

__all__ = [
    "CEILING",
    "MEAN_FLOOR",
    "count_z",
    "fit_dispersion",
    "log_pmf",
    "tail",
]

# Where a z stops. A tail past it underflows, and ±40 is far past any
# threshold anyone would set; a finite ceiling keeps it sortable and
# storable where infinity would be neither.
CEILING = 40.0

# The smallest mean a count is measured against. A channel whose newest
# posts all had no comments has a mean of zero, against which any comment
# at all would be infinitely improbable; a twentieth of a comment says
# "almost never" without saying "never".
MEAN_FLOOR = 0.05

_NORMAL = NormalDist()

# Lentz's method converges in O(√max(a, b)) steps; a count of a hundred
# thousand comments needs a few hundred. The cap is a guard, not a budget.
_MAX_STEPS = 10_000
_EPSILON = 3e-16
_TINY = 1e-300


def _continued_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta, by modified Lentz."""
    total = a + b
    up = a + 1.0
    down = a - 1.0
    c = 1.0
    d = 1.0 - total * x / up
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    result = d
    for step in range(1, _MAX_STEPS + 1):
        twice = 2 * step
        term = step * (b - step) * x / ((down + twice) * (a + twice))
        d = 1.0 + term * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + term / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        result *= d * c
        term = -(a + step) * (total + step) * x / ((a + twice) * (up + twice))
        d = 1.0 + term * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + term / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        change = d * c
        result *= change
        if abs(change - 1.0) < _EPSILON:
            break
    return result


def _regularised_beta(a: float, b: float, x: float) -> float:
    """``I_x(a, b)``, the regularised incomplete beta function.

    In log space up to the last step, so a tail of 1e-200 is computed
    rather than lost: the prefactor alone underflows long before the
    probability is too small to matter.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _continued_fraction(a, b, x) / a
    return 1.0 - math.exp(log_front) * _continued_fraction(b, a, 1.0 - x) / b


def tail(observed: int, mean: float, dispersion: float) -> float:
    """``P(X ≥ observed)`` for ``X`` negative binomial.

    Parametrised by its mean and dispersion: ``Var X = mean + dispersion ×
    mean²``. As ``scipy.stats.nbinom`` has it, ``n = 1 / dispersion``
    successes with probability ``p = n / (n + mean)``, and the upper tail
    is ``I_{1−p}(observed, n)``.
    """
    if observed <= 0:
        return 1.0
    n = 1.0 / dispersion
    return _regularised_beta(float(observed), n, mean / (n + mean))


def count_z(observed: int, mean: float, dispersion: float) -> float:
    """The z whose standard normal upper tail is ``P(X ≥ observed)``.

    ``−CEILING`` for no count at all, whose tail is certainty, and
    ``CEILING`` where the tail underflows: both finite, so a post is
    ranked and stored like any other. ``mean`` is floored at
    ``MEAN_FLOOR``.
    """
    if observed <= 0:
        return -CEILING
    probability = tail(observed, max(mean, MEAN_FLOOR), dispersion)
    if probability <= 0.0:
        return CEILING
    if probability >= 1.0:
        return -CEILING
    return max(-CEILING, min(CEILING, -_NORMAL.inv_cdf(probability)))


def log_pmf(observed: int, mean: float, dispersion: float) -> float:
    """``log P(X = observed)``, for the likelihood a dispersion is fitted by."""
    n = 1.0 / dispersion
    mean = max(mean, MEAN_FLOOR)
    return (
        math.lgamma(observed + n)
        - math.lgamma(n)
        - math.lgamma(observed + 1.0)
        + n * math.log(n / (n + mean))
        + observed * math.log(mean / (n + mean))
    )


# Where the dispersion is looked for, on a log scale. A kind measured at
# 0.4–17 on production; the bounds leave room on both sides.
_LOWEST = math.log(0.01)
_HIGHEST = math.log(50.0)
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0
_STEPS = 60


def fit_dispersion(pairs: Iterable[tuple[int, float]]) -> float | None:
    """The dispersion that makes observed counts likeliest, or ``None``.

    ``pairs`` is each reading's count and the mean it would have been
    scored against. Golden-section search over the logarithm of the
    dispersion: the likelihood is unimodal in it, and sixty steps narrow
    the bracket to a millionth of its width, so the answer does not depend
    on a grid's spacing.

    Maximum likelihood rather than moments, because the moments were tried
    and failed: fitted on the middle 98% of residuals they trimmed the very
    tail the dispersion exists to describe, and the threshold then passed
    1.5% of readings against the normal's 0.011%.
    """
    data = [(observed, max(mean, MEAN_FLOOR)) for observed, mean in pairs]
    if not data:
        return None

    # The terms that do not move with the dispersion are left out, and the
    # one that does, lgamma(y + n), is taken once per distinct count: half
    # the readings are zero and most of the rest are small, so a day's
    # likelihood is a few hundred lgammas rather than a hundred thousand.
    by_count = Counter(observed for observed, _ in data)
    size = len(data)
    weighted = sum(observed * math.log(mean) for observed, mean in data)

    def likelihood(log_dispersion: float) -> float:
        n = 1.0 / math.exp(log_dispersion)
        gammas = sum(
            times * math.lgamma(observed + n)
            for observed, times in by_count.items()
        )
        spread = sum(
            (n + observed) * math.log(n + mean) for observed, mean in data
        )
        return (
            gammas
            - size * math.lgamma(n)
            + size * n * math.log(n)
            + weighted
            - spread
        )

    low, high = _LOWEST, _HIGHEST
    left = high - _GOLDEN * (high - low)
    right = low + _GOLDEN * (high - low)
    at_left, at_right = likelihood(left), likelihood(right)
    for _ in range(_STEPS):
        if at_left < at_right:
            low, left, at_left = left, right, at_right
            right = low + _GOLDEN * (high - low)
            at_right = likelihood(right)
        else:
            high, right, at_right = right, left, at_left
            left = high - _GOLDEN * (high - low)
            at_left = likelihood(left)
    return math.exp((low + high) / 2.0)
