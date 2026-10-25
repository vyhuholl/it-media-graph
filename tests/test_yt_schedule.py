"""When a YouTube video is read, checked or left alone; pure arithmetic.

Every test states a moment. The cases that carry the most weight are the
missed readings — skipped, never replayed — and the two Pacific days
that are not 24 hours long, which an even spread across "the day" gets
wrong if it assumes one.
"""

from datetime import UTC, datetime, timedelta

import pytest

from itgraph.yt_quota import day_start, next_reset
from itgraph.yt_schedule import (
    check_due,
    deferrable,
    listing_retry_at,
    next_check_of_upcoming,
    next_reading_at,
    origin,
    pace_allows,
)

START = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def at(**offset: float) -> datetime:
    """A moment, as an offset from the video's start."""
    return START + timedelta(**offset)


@pytest.mark.parametrize(
    ("now", "due"),
    [
        # Discovery at a few minutes: the first reading is at an hour.
        (at(minutes=5), at(hours=1)),
        # "A video is read at the next age it has not reached."
        (at(hours=3), at(hours=4)),
        # Exactly at an age, the next one — never the same one twice.
        (at(hours=4), at(hours=8)),
        # "A long gap produces no backlog": 2, 4 and 8 h are gone.
        (at(hours=10), at(hours=12)),
        (at(days=6), at(days=7)),
        (at(days=27), at(days=28)),
    ],
)
def test_the_next_reading_is_the_next_age_not_reached(
    now: datetime, due: datetime
) -> None:
    assert next_reading_at(START, now) == due


@pytest.mark.parametrize("now", [at(days=28), at(days=29), at(days=40)])
def test_reading_ends_at_the_horizon(now: datetime) -> None:
    assert next_reading_at(START, now) is None


def test_a_finished_stream_is_aged_from_its_start() -> None:
    """Published two hours before it went live: the hour counts from live."""
    published = START - timedelta(hours=2)
    assert origin(published, START) == START
    assert next_reading_at(START, at(minutes=30)) == at(hours=1)


def test_a_video_without_a_broadcast_is_aged_from_publication() -> None:
    assert origin(START, None) == START
    assert origin(None, None) is None


def test_a_scheduled_stream_is_checked_at_its_start() -> None:
    tomorrow = START + timedelta(days=1)
    assert (
        next_check_of_upcoming(
            START,
            scheduled_start=tomorrow,
            tracked_since=START,
            fetched_at=START,
        )
        == tomorrow
    )


def test_a_late_stream_is_checked_hourly() -> None:
    now = START + timedelta(hours=3)
    assert next_check_of_upcoming(
        now, scheduled_start=START, tracked_since=START, fetched_at=now
    ) == now + timedelta(hours=1)


def test_a_stream_that_never_starts_is_given_up() -> None:
    now = START + timedelta(days=7)
    assert (
        next_check_of_upcoming(
            now, scheduled_start=START, tracked_since=START, fetched_at=now
        )
        is None
    )


def test_a_stream_scheduled_far_ahead_is_fetched_before_the_refresh_age() -> (
    None
):
    far = START + timedelta(days=40)
    assert next_check_of_upcoming(
        START, scheduled_start=far, tracked_since=START, fetched_at=START
    ) == START + timedelta(days=25)


def test_a_broadcast_with_no_scheduled_start_counts_from_tracking() -> None:
    now = START + timedelta(days=8)
    assert (
        next_check_of_upcoming(
            now, scheduled_start=None, tracked_since=START, fetched_at=now
        )
        is None
    )


def test_only_old_videos_may_wait() -> None:
    assert not deferrable(START, at(days=7))
    assert deferrable(START, at(days=7, minutes=1))


def test_the_check_counts_from_the_last_listing() -> None:
    assert check_due(None, START)
    assert not check_due(START, at(hours=23))
    assert check_due(START, at(hours=24))


@pytest.mark.parametrize(
    ("failures", "wait"),
    [
        (0, timedelta()),
        (1, timedelta(hours=2)),
        (3, timedelta(hours=8)),
        (5, timedelta(hours=24)),
        (50, timedelta(hours=24)),
    ],
)
def test_a_failing_channel_waits_longer_each_time(
    failures: int, wait: timedelta
) -> None:
    assert listing_retry_at(START, failures) == START + wait


def test_pace_follows_the_share_of_the_day_gone() -> None:
    """A quarter of the day gone, half the pool spent: ahead of pace."""
    start = day_start(START)
    quarter = start + (next_reset(START) - start) / 4

    assert pace_allows(1050, 3000, quarter)  # 750 + 300 margin
    assert not pace_allows(1500, 3000, quarter)


def test_the_first_pass_of_a_day_is_never_ahead() -> None:
    assert pace_allows(300, 3000, day_start(START))


@pytest.mark.parametrize(
    ("midday", "length"),
    [
        # Leaves daylight saving: 07:00 UTC to 08:00 UTC the next day.
        (datetime(2026, 11, 1, 19, 30, tzinfo=UTC), timedelta(hours=25)),
        # Enters it: 08:00 UTC to 07:00 UTC the next day.
        (datetime(2027, 3, 14, 19, 30, tzinfo=UTC), timedelta(hours=23)),
    ],
)
def test_pace_divides_by_the_real_length_of_the_day(
    midday: datetime, length: timedelta, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 19:30 UTC both days are exactly half gone — by their own length."""
    from itgraph.config import settings

    monkeypatch.setattr(settings, "youtube_watch_pace_margin", 0.0)
    assert next_reset(midday) - day_start(midday) == length

    assert pace_allows(1500, 3000, midday)
    assert not pace_allows(1501, 3000, midday)
