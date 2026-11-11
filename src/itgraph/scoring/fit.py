"""Fitting a platform's baselines: the shape, its join to history, its rulers.

Pure: observations and references in, a fit or the reason there is none
out. No database and no settings — the floors, the bands and the rule a
channel's own reference is taken by arrive as arguments, so Telegram's
refresh and YouTube's run the same arithmetic under their own numbers.

Moved here from ``scoring/refresh.py`` unchanged in what it computes, when
YouTube's refresh became its second caller; ``test_telegram_scoring_record``
holds Telegram's answers to what they were before the move.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from itgraph.scoring.counts import MEAN_FLOOR, fit_dispersion
from itgraph.scoring.curves import (
    AgeBands,
    Curve,
    Observation,
    fit_band_spreads,
    fit_factor,
    fit_spread,
    reference_at,
)

__all__ = [
    "CurveFitter",
    "Fit",
    "Floors",
    "RecentRule",
    "Reference",
    "fit",
    "residuals",
    "rolling",
]

# What a residual is measured from: the reference one observation's post
# would have been scored against, or ``None`` where it had none.
type Reference[C, P] = Callable[[Observation[C, P]], float | None]


class CurveFitter(Protocol):
    """``fit_curve`` or ``fit_count_curve``: how a shape is fitted.

    Passed in rather than chosen here, so that what a refresh fits its
    curves with is visible where the refresh is.
    """

    def __call__[C, P](
        self,
        observations: Sequence[Observation[C, P]],
        *,
        min_samples: int,
        bands: AgeBands,
    ) -> Curve: ...


@dataclass(frozen=True, slots=True)
class Floors:
    """Below these a band is not fitted, or keeps no spread of its own.

    ``band_samples`` counts readings; ``band_spread_posts`` counts distinct
    posts, because a post is read several times inside one band and a
    spread from few posts is biased low — the direction that over-alerts.
    """

    band_samples: int
    band_spread_posts: int


@dataclass(frozen=True, slots=True)
class RecentRule:
    """How a channel's own reference is taken: its newest posts, at least
    a minimum of them, none older than a maximum age."""

    newest: int
    minimum: int
    max_age: timedelta

    def at(
        self,
        history: Sequence[tuple[datetime, float]],
        moment: datetime,
        *,
        bands: AgeBands,
        counted: bool = False,
    ) -> tuple[float, int] | None:
        """The reference a channel had at ``moment``, under this rule."""
        return reference_at(
            history,
            moment,
            newest=self.newest,
            minimum=self.minimum,
            max_age=self.max_age,
            counted=counted,
            bands=bands,
        )


def residuals[C, P](
    entries: Sequence[Observation[C, P]],
    *,
    curve: Curve,
    reference: Reference[C, P],
) -> dict[str, dict[tuple[C, P], list[float]]]:
    """``log(actual / expected)`` per age band and post, where both exist.

    Computed against the curve just fitted, not against a previous run's:
    the spread has to describe *this* estimate's error, or a threshold
    expressed in it would be measuring the difference between two
    vintages of baseline.

    ``reference`` is the value each observation is measured from — the one
    its post would actually have been scored against, which for a channel
    on its recent reference is built only from posts settled before this
    one was published. A reference containing its own posts fits them:
    measured, the residuals come out 5% narrower, and a narrow ruler is the
    direction that over-alerts.

    Grouped by band because the dispersion is not constant across a post's
    life — measured, 1.18 at fifteen minutes against 0.98 at eight hours.
    Kept per post within the band because how many posts a band saw, not
    how many readings, is what says whether it can speak for itself.
    """
    found: dict[str, dict[tuple[C, P], list[float]]] = {}
    for entry in entries:
        band = curve.bands.band_of(entry.age)
        if band is None:
            continue
        fraction = curve.fractions.get(band)
        if fraction is None or fraction <= 0:
            continue
        base = reference(entry)
        if base is None:
            continue
        expected = base * fraction
        if expected <= 0 or entry.value <= 0:
            continue
        found.setdefault(band, {}).setdefault(entry.post_key, []).append(
            math.log(entry.value / expected)
        )
    return found


@dataclass(frozen=True, slots=True)
class Fit:
    """A shape and its two rulers, fitted from one pool of observations."""

    curve: Curve
    factor: float
    spread: float
    band_spreads: dict[str, float]
    samples: int
    dispersion: float | None = None


def _dispersion[C, P](
    entries: Sequence[Observation[C, P]],
    *,
    curve: Curve,
    reference: Reference[C, P],
    min_posts: int,
) -> float | str:
    """A counted metric's dispersion for one pool, or why it has none.

    Each reading against the mean its post would have been scored against —
    the reference built from the posts settled before it, so a post never
    fits its own ruler. Measured on at least as many distinct posts as a
    band's own spread needs, for the same reason: fitted on a handful of
    posts, a dispersion comes out too narrow as often as not, and too
    narrow is the direction that over-alerts.
    """
    pairs: list[tuple[int, float]] = []
    posts: set[tuple[C, P]] = set()
    for entry in entries:
        fraction = curve.at(entry.age)
        base = reference(entry)
        if fraction is None or base is None:
            continue
        pairs.append((round(entry.value), max(base * fraction, MEAN_FLOOR)))
        posts.add(entry.post_key)
    if len(posts) < min_posts:
        return "too few posts to fit a dispersion"
    dispersion = fit_dispersion(pairs)
    return "no dispersion" if dispersion is None else dispersion


def fit[C, P](
    entries: Sequence[Observation[C, P]],
    medians: Mapping[C, tuple[float, int]],
    *,
    shape: CurveFitter,
    floors: Floors,
    bands: AgeBands,
    recent: Reference[C, P] | None = None,
    counted: bool = False,
) -> Fit | str:
    """Everything one pool of observations supports, or why it does not.

    Returns the reason as a string rather than ``None`` so a caller can say
    which of the steps failed. "Not fitted" without a reason is the kind of
    silence a refresh exists to remove.

    ``recent`` gives an observation's own reference where its channel had
    one when the post went out; anything it does not cover is measured from
    the mature median and the factor fitted here, which is what scoring
    would have done with it.

    ``counted`` is a counted metric's fit: no factor and no mature
    fallback, and a dispersion beside the spread. Its ``shape`` is then a
    ratio of sums, which the caller passes.
    """
    curve = shape(entries, min_samples=floors.band_samples, bands=bands)
    if not curve.fractions:
        return "no band met the minimum"

    if counted:
        factor: float | None = 1.0
    else:
        factor = fit_factor(
            entries,
            {channel: value for channel, (value, _) in medians.items()},
            min_samples=floors.band_samples,
            bands=bands,
        )
    if factor is None:
        return "too few posts with a mature median"

    def reference(entry: Observation[C, P]) -> float | None:
        if recent is not None and (value := recent(entry)) is not None:
            return value
        if counted:
            return None
        median = medians.get(entry.post_key[0])
        return None if median is None else median[0] * factor

    by_post = residuals(entries, curve=curve, reference=reference)
    pooled_by_band = {
        band: [value for values in posts.values() for value in values]
        for band, posts in by_post.items()
    }
    pooled = fit_spread(
        [value for values in pooled_by_band.values() for value in values],
        min_samples=floors.band_samples,
    )
    if pooled is None:
        # Without a spread there is no z, and one borrowed from another
        # metric would apply that metric's shape to this one.
        return "no measurable spread"

    dispersion: float | None = None
    if counted:
        found = _dispersion(
            entries,
            curve=curve,
            reference=reference,
            min_posts=floors.band_spread_posts,
        )
        if isinstance(found, str):
            return found
        dispersion = found

    return Fit(
        curve=curve,
        factor=factor,
        spread=pooled,
        band_spreads=fit_band_spreads(
            pooled_by_band,
            min_samples=floors.band_samples,
            posts={band: len(posts) for band, posts in by_post.items()},
            min_posts=floors.band_spread_posts,
        ),
        samples=len(entries),
        dispersion=dispersion,
    )


def rolling[C, P](
    history: Mapping[C, Sequence[tuple[datetime, float]]],
    *,
    rule: RecentRule,
    bands: AgeBands,
    counted: bool = False,
) -> Reference[C, P]:
    """Each observation's own reference as of its post's publication.

    The stored reference evaluated at a different moment — the same
    function — so the residuals the spread is fitted on are the ones
    scoring will meet. Cached per post: a post is read many times, and its
    reference does not depend on which reading asks.
    """
    cache: dict[tuple[C, P], float | None] = {}

    def lookup(entry: Observation[C, P]) -> float | None:
        if entry.published is None:
            return None
        if entry.post_key not in cache:
            found = rule.at(
                history.get(entry.post_key[0], ()),
                entry.published,
                bands=bands,
                counted=counted,
            )
            cache[entry.post_key] = None if found is None else found[0]
        return cache[entry.post_key]

    return lookup
