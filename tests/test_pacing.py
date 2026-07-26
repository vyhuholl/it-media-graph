"""How long to wait before a request.

``request_gap`` is a pure function, so it is tested against a stubbed
random source rather than by sampling: what matters is which branch it
takes and what bounds it hands the draw, and sampling can only show that
a few hundred draws happened to land inside them.
"""

from typing import Any

import pytest

from itgraph.config import settings
from itgraph.tg import pacing as pacing_module
from itgraph.tg.pacing import channel_gap, request_gap


class StubRandom:
    """A random source that answers exactly what a test tells it to.

    Records the bounds it was handed, which is the part under test — a
    real draw would only show that the result fell somewhere plausible.
    """

    def __init__(self, *, roll: float = 1.0, draw: float = 0.0) -> None:
        self._roll = roll
        self._draw = draw
        self.rolls = 0
        self.bounds: list[tuple[float, float]] = []

    def random(self) -> float:
        self.rolls += 1
        return self._roll

    def uniform(self, low: float, high: float) -> float:
        self.bounds.append((low, high))
        return self._draw


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(**kwargs: Any) -> StubRandom:
        source = StubRandom(**kwargs)
        monkeypatch.setattr(pacing_module, "_rng", source)
        return source

    return install


def test_the_ordinary_gap_is_a_band_around_the_delay(stub: Any) -> None:
    """Relative, not absolute: the band scales with the configured delay."""
    source = stub(roll=1.0)

    request_gap(4.0)

    assert source.bounds == [(2.0, 6.0)]


def test_the_band_stays_sensible_for_a_small_delay(stub: Any) -> None:
    """A fixed ±2 would reach below zero here. A fraction cannot."""
    source = stub(roll=1.0)

    request_gap(1.0)

    assert source.bounds == [(0.5, 1.5)]
    assert source.bounds[0][0] > 0


def test_a_rare_roll_takes_the_long_range_instead(stub: Any) -> None:
    """Instead, not in addition — one draw, from the other range."""
    source = stub(roll=0.0)

    request_gap(4.0)

    assert source.bounds == [
        (settings.pacing_long_pause_min, settings.pacing_long_pause_max)
    ]


def test_the_long_range_is_far_above_the_ordinary_band() -> None:
    """Otherwise the two are the same feature with extra settings."""
    assert settings.pacing_long_pause_min > (
        settings.backfill_request_delay * (1 + settings.pacing_jitter)
    )


def test_the_roll_decides_by_the_configured_chance(stub: Any) -> None:
    source = stub(roll=settings.pacing_long_pause_chance)

    request_gap(4.0)

    # Exactly at the chance is not below it: the ordinary band.
    assert source.bounds == [(2.0, 6.0)]


def test_a_zero_delay_takes_no_branch_at_all(stub: Any) -> None:
    """Switched off means switched off — not a band around zero, and not
    a long pause that might still fire."""
    source = stub(roll=0.0)

    assert request_gap(0) == 0.0
    assert request_gap(-1) == 0.0
    assert source.rolls == 0
    assert source.bounds == []


def test_the_channel_gap_uses_the_configured_range(stub: Any) -> None:
    source = stub()

    channel_gap()

    assert source.bounds == [
        (
            settings.backfill_channel_pause_min,
            settings.backfill_channel_pause_max,
        )
    ]


def test_the_channel_gap_is_far_longer_than_a_request_gap() -> None:
    """It exists to space the per-channel requests that carry quotas; a
    pause the size of an ordinary gap would not be doing that."""
    assert settings.backfill_channel_pause_min > (
        settings.backfill_request_delay * (1 + settings.pacing_jitter)
    )


def test_real_draws_land_inside_the_band() -> None:
    """The stub proves the bounds; this proves they are actually used."""
    drawn = [request_gap(4.0) for _ in range(500)]
    ordinary = [gap for gap in drawn if gap <= 10]

    assert ordinary
    assert all(2.0 <= gap <= 6.0 for gap in ordinary)
    # Drawn per call, not computed once.
    assert len(set(ordinary)) > 1
