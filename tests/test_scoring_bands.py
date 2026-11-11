"""Age bands as a value, and keys that are not Telegram's.

`scoring/` fits Telegram's posts and YouTube's videos alike: each brings
its own bands and its own kind of key. Telegram's are pinned elsewhere by
`test_telegram_scoring_record.py`; what is tested here is that YouTube's
bands are the ones `add-youtube-virality-scoring` decided, and that nothing
in the pure modules assumes an integer key.
"""

from datetime import UTC, datetime, timedelta

import pytest

from itgraph.scoring.curves import (
    AGE_BANDS,
    REFERENCE_BAND,
    REFERENCE_SETTLED,
    TELEGRAM_BANDS,
    YOUTUBE_BANDS,
    AgeBands,
    Curve,
    Observation,
    fit_curve,
    fit_factor,
    reference_at,
)
from itgraph.scoring.fold import Head, fold
from itgraph.scoring.score import Baseline, Score, Spike

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_telegram_s_bands_are_what_they_were() -> None:
    assert TELEGRAM_BANDS.reference == REFERENCE_BAND == "420m"
    assert (
        TELEGRAM_BANDS.settled == REFERENCE_SETTLED == timedelta(minutes=570)
    )
    assert TELEGRAM_BANDS.bands == AGE_BANDS
    assert AGE_BANDS[0][2] == "10m"


@pytest.mark.parametrize(
    ("age", "band"),
    [
        (timedelta(hours=4), "180m"),
        (timedelta(hours=8), "360m"),
        (timedelta(hours=12), "600m"),
        (timedelta(hours=18), "900m"),
        (timedelta(hours=24), "1260m"),
        (timedelta(hours=36), "1800m"),
        (timedelta(days=2), "2520m"),
        (timedelta(days=3), "3600m"),
        (timedelta(days=4), "5040m"),
        (timedelta(days=5), "6480m"),
        (timedelta(days=7), "8640m"),
    ],
)
def test_each_youtube_reading_age_has_a_band_of_its_own(
    age: timedelta, band: str
) -> None:
    """One scheduled reading per band, with room above it for a late one."""
    assert YOUTUBE_BANDS.band_of(age) == band
    assert YOUTUBE_BANDS.band_of(age + timedelta(minutes=15)) == band


def test_youtube_s_bands_start_at_three_hours_and_end_at_eight_and_a_half_days() -> (
    None
):
    assert YOUTUBE_BANDS.band_of(timedelta(hours=2)) is None
    assert YOUTUBE_BANDS.band_of(timedelta(hours=3)) == "180m"
    assert YOUTUBE_BANDS.last_edge == timedelta(days=8, hours=12)
    assert YOUTUBE_BANDS.band_of(timedelta(days=8, hours=12)) is None


def test_youtube_s_reference_is_the_day_band() -> None:
    assert YOUTUBE_BANDS.reference == "1260m"
    assert YOUTUBE_BANDS.settled == timedelta(hours=30)


def test_a_reference_age_outside_every_band_is_refused() -> None:
    with pytest.raises(ValueError, match="reference age"):
        AgeBands(edges_minutes=(10, 20), reference_age=timedelta(hours=1))


def _video(video: str, age: timedelta, value: float) -> Observation[str, str]:
    return Observation(post_key=("UCfakeChannel", video), age=age, value=value)


def test_a_curve_fitted_on_youtube_s_bands_is_named_by_them() -> None:
    observations = [
        observation
        for index in range(5)
        for observation in (
            _video(f"fakeVideo{index:02d}", timedelta(hours=4), 25.0),
            _video(f"fakeVideo{index:02d}", timedelta(hours=24), 100.0),
            _video(f"fakeVideo{index:02d}", timedelta(days=3), 140.0),
        )
    ]

    curve = fit_curve(observations, min_samples=5, bands=YOUTUBE_BANDS)

    assert curve.fractions == {"180m": 0.25, "1260m": 1.0, "3600m": 1.4}
    assert curve.at(timedelta(days=3)) == 1.4
    # Telegram's bands would have found no reference at all.
    assert fit_curve(observations, min_samples=5).fractions == {}


def test_a_factor_takes_a_string_channel() -> None:
    observations = [
        _video(f"fakeVideo{index:02d}", timedelta(hours=24), 50.0)
        for index in range(3)
    ]

    factor = fit_factor(
        observations,
        {"UCfakeChannel": 100.0},
        min_samples=3,
        bands=YOUTUBE_BANDS,
    )

    assert factor == 0.5


def test_a_youtube_reference_waits_thirty_hours() -> None:
    history = [
        (NOW - timedelta(hours=31), 100.0),
        (NOW - timedelta(hours=29), 900.0),
    ]

    found = reference_at(
        history,
        NOW,
        newest=20,
        minimum=1,
        max_age=timedelta(days=180),
        bands=YOUTUBE_BANDS,
    )

    assert found == (100.0, 1)


def test_a_ruler_uses_its_curve_s_bands() -> None:
    baseline = Baseline(
        reference=100.0,
        factor=1.0,
        curve=Curve(
            fractions={"1260m": 1.0},
            samples={"1260m": 100},
            bands=YOUTUBE_BANDS,
        ),
        spread=0.5,
        band_spreads={"1260m": 0.25},
    )

    assert baseline.spread_at(timedelta(hours=24)) == 0.25
    assert baseline.spread_at(timedelta(hours=1)) is None


def _spike(video: str, crossed: datetime, z: float) -> Spike[str, str]:
    return Spike(
        post_key=("UCfakeChannel", video),
        score=Score(
            metric="views",
            age=timedelta(hours=24),
            observed=1000.0,
            expected=100.0,
            z=z,
        ),
        crossed_at=crossed,
        scored_at=crossed,
    )


def test_videos_fold_by_their_channel() -> None:
    placed = fold(
        [
            _spike("fakeVideo01", NOW, 5.0),
            _spike("fakeVideo02", NOW + timedelta(hours=20), 6.0),
            _spike("fakeVideo03", NOW + timedelta(hours=25), 4.0),
        ],
        heads={},
        window=timedelta(hours=24),
    )

    into = [
        (
            entry.spike.post_key[1],
            None if entry.into is None else entry.into.post_key[1],
        )
        for entry in placed
    ]
    assert into == [
        ("fakeVideo01", None),
        ("fakeVideo02", "fakeVideo01"),
        ("fakeVideo03", None),
    ]


def test_a_video_folds_into_a_head_already_in_the_queue() -> None:
    head = Head(
        alert_id=7,
        post_key=("UCfakeChannel", "fakeVideo00"),
        raised_at=NOW - timedelta(hours=2),
    )

    placed = fold(
        [_spike("fakeVideo01", NOW, 5.0)],
        heads={"UCfakeChannel": head},
        window=timedelta(hours=24),
    )

    assert placed[0].into is head
