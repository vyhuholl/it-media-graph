"""The quota day is Google's: the Pacific date, 23 or 25 hours twice a year."""

from datetime import UTC, date, datetime, timedelta

import pytest

from itgraph.yt_quota import general_cap, next_reset, quota_day


def utc(
    year: int, month: int, day: int, hour: int, minute: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_summer_spending_counts_against_the_pacific_date() -> None:
    """Pacific daylight time is seven hours behind UTC."""
    assert quota_day(utc(2026, 7, 15, 6, 59)) == date(2026, 7, 14)
    assert quota_day(utc(2026, 7, 15, 7, 0)) == date(2026, 7, 15)


def test_winter_spending_counts_against_the_pacific_date() -> None:
    """Pacific standard time is eight."""
    assert quota_day(utc(2026, 12, 15, 7, 59)) == date(2026, 12, 14)
    assert quota_day(utc(2026, 12, 15, 8, 0)) == date(2026, 12, 15)


def test_the_day_that_leaves_daylight_saving_is_25_hours_long() -> None:
    """1 November 2026: the clocks go back, and the day runs on to 08:00
    UTC the next morning instead of 07:00."""
    late = utc(2026, 11, 2, 7, 30)

    assert quota_day(late) == date(2026, 11, 1)
    assert next_reset(late) == utc(2026, 11, 2, 8, 0)

    start = next_reset(utc(2026, 10, 31, 12, 0))
    assert next_reset(late) - start == timedelta(hours=25)


def test_the_day_that_enters_daylight_saving_is_23_hours_long() -> None:
    start = next_reset(utc(2027, 3, 13, 12, 0))
    end = next_reset(utc(2027, 3, 14, 12, 0))

    assert start == utc(2027, 3, 14, 8, 0)
    assert end == utc(2027, 3, 15, 7, 0)
    assert end - start == timedelta(hours=23)


def test_the_reset_is_the_next_pacific_midnight() -> None:
    assert next_reset(utc(2026, 7, 15, 12, 0)) == utc(2026, 7, 16, 7, 0)


def test_a_moment_in_another_zone_is_the_same_moment() -> None:
    """A local clock is converted, not reinterpreted."""
    moscow = datetime.fromisoformat("2026-07-15T09:59:00+03:00")
    assert quota_day(moscow) == date(2026, 7, 14)


@pytest.mark.parametrize(
    ("daily", "margin", "reserved", "cap"),
    [
        (10_000, 0.10, 3_000, 6_000),
        (10_000, 0.10, 0, 9_000),
        (10_000, 0.0, 0, 10_000),
        # Rounded, not truncated: 10% of 999 is 99.9 held back as 100.
        (999, 0.10, 0, 899),
    ],
)
def test_the_general_cap(
    daily: int, margin: float, reserved: int, cap: int
) -> None:
    assert general_cap(daily, margin, reserved) == cap
