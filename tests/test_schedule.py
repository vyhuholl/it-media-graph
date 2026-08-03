"""When a post is read again, and when a channel is looked at.

Pure arithmetic, so every test states a moment rather than waiting for
one. The cases that matter most are the ones about *missed* samples —
that is the decision the loop's survival across a closed laptop rests on,
and it is the one a later refactor is most likely to "fix".
"""

from datetime import UTC, datetime, timedelta

import pytest

from itgraph.config import settings
from itgraph.schedule import (
    backoff_factor,
    idle_interval,
    in_quiet_hours,
    next_channel_poll,
    next_sample_at,
    window_size,
)

PUBLISHED = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def at(**offset: float) -> datetime:
    """A moment, as an offset from publication."""
    return PUBLISHED + timedelta(**offset)


def test_a_fresh_post_is_read_at_the_first_offset() -> None:
    assert next_sample_at(PUBLISHED, PUBLISHED) == at(minutes=15)


def test_the_schedule_advances_with_the_post() -> None:
    assert next_sample_at(PUBLISHED, at(minutes=20)) == at(minutes=30)
    assert next_sample_at(PUBLISHED, at(minutes=45)) == at(minutes=60)
    assert next_sample_at(PUBLISHED, at(hours=3)) == at(hours=4)


def test_a_sample_exactly_due_is_not_taken_twice() -> None:
    """At the offset, the next one is the following sample, not this one.

    Otherwise a poll landing exactly on a sample would schedule itself
    for the same moment and spin.
    """
    assert next_sample_at(PUBLISHED, at(minutes=15)) == at(minutes=30)


def test_the_last_sample_is_reachable() -> None:
    """The horizon has to sit strictly past the final offset.

    Set them equal and the last configured sample can never be taken: a
    post old enough to be due for it is already too old to be read. The
    settings validator refuses that, and this is the behaviour it
    protects.
    """
    assert next_sample_at(PUBLISHED, at(hours=47)) == at(hours=48)


def test_a_post_past_the_last_offset_is_finished() -> None:
    assert next_sample_at(PUBLISHED, at(hours=48, minutes=1)) is None


def test_a_post_past_the_horizon_is_finished() -> None:
    assert next_sample_at(PUBLISHED, at(hours=49)) is None
    assert next_sample_at(PUBLISHED, at(days=9)) is None


def test_missed_samples_are_skipped_not_replayed() -> None:
    """The decision the whole module exists to state.

    A post whose first four samples elapsed while the machine slept
    resumes at the sample appropriate to its *current* age. It does not
    get four readings in a row, and the missed ones never happen — a
    reading due at 30 minutes and taken at 8 hours would be a different
    measurement wearing the same name.
    """
    woken = at(hours=7)
    assert next_sample_at(PUBLISHED, woken) == at(hours=8)

    # And having taken it, the next is the one after — never a catch-up.
    assert next_sample_at(PUBLISHED, at(hours=8)) == at(hours=24)


def test_a_long_outage_costs_the_early_curve_and_nothing_else() -> None:
    """The cost is real and bounded: early samples gone, late ones intact."""
    assert next_sample_at(PUBLISHED, at(hours=23)) == at(hours=24)


def test_the_channel_follows_its_youngest_live_post() -> None:
    now = at(minutes=5)
    due = next_channel_poll(
        now, live_posts=[PUBLISHED, PUBLISHED - timedelta(hours=10)]
    )
    assert due == at(minutes=15)


def test_one_poll_serves_every_live_post() -> None:
    """The channel's schedule is the union of its posts', not the sum.

    One request refreshes all of them, so the cost is per channel per
    cycle — which is the arithmetic that makes the whole budget work.
    """
    older = PUBLISHED - timedelta(hours=3)
    now = at(minutes=5)

    assert next_channel_poll(now, live_posts=[PUBLISHED, older]) == at(
        minutes=15
    )


def test_a_burst_does_not_produce_a_burst_of_polls() -> None:
    """Five messages of an album are one poll, not five.

    Without the floor each part would be due within a minute of the
    others, and each would cost the same request.
    """
    now = at(minutes=1)
    posts = [PUBLISHED + timedelta(seconds=n) for n in range(5)]

    due = next_channel_poll(now, live_posts=posts, last_polled_at=now)

    assert due >= now + timedelta(
        minutes=settings.watch_min_gap_minutes
    ) - timedelta(seconds=1)


def test_a_channel_with_nothing_live_falls_back_to_its_rate() -> None:
    now = at(days=5)
    due = next_channel_poll(now, live_posts=[], posts_per_day=4.0)

    # 4/day is a 6-hour mean gap; divided by the configured divisor.
    assert due == now + timedelta(hours=6) / settings.watch_idle_divisor


def test_the_idle_interval_is_clamped_at_both_ends() -> None:
    # A prolific channel must not be polled every few minutes on the
    # path meant for quiet ones.
    assert idle_interval(500.0) == timedelta(
        minutes=settings.watch_idle_min_minutes
    )
    # And one that posts twice a year must not be forgotten.
    assert idle_interval(0.001) == timedelta(
        minutes=settings.watch_idle_max_minutes
    )


def test_an_unknown_rate_gets_the_slowest_interval() -> None:
    """Wrong in the cheap direction.

    A late discovery costs latency on one post; the other mistake costs
    requests on the 111 channels that have published nothing in a month.
    """
    assert idle_interval(None) == timedelta(
        minutes=settings.watch_idle_max_minutes
    )
    assert idle_interval(0.0) == timedelta(
        minutes=settings.watch_idle_max_minutes
    )


def test_backoff_stretches_the_quiet_and_the_broken_differently() -> None:
    assert backoff_factor() == 1.0
    assert backoff_factor(empty=2) > 1.0
    # Failing is the more serious of the two.
    assert backoff_factor(failures=2) > backoff_factor(empty=2)


def test_backoff_is_capped() -> None:
    """An uncapped exponential would quietly retire a channel that came back."""
    assert backoff_factor(empty=99) == backoff_factor(
        empty=settings.watch_empty_backoff_cap
    )
    assert backoff_factor(failures=99) == backoff_factor(
        failures=settings.watch_failure_backoff_cap
    )


def test_backoff_does_not_delay_a_live_post() -> None:
    """A quiet fortnight is not an argument against reading a post that exists."""
    now = at(minutes=5)
    due = next_channel_poll(
        now,
        live_posts=[PUBLISHED],
        consecutive_empty=4,
        consecutive_failures=3,
    )
    assert due == at(minutes=15)


def test_a_failing_channel_is_still_tried_eventually() -> None:
    now = at(days=1)
    due = next_channel_poll(
        now, live_posts=[], posts_per_day=2.0, consecutive_failures=99
    )
    assert due <= now + timedelta(minutes=settings.watch_failure_max_minutes)


def test_a_due_moment_is_never_in_the_past() -> None:
    """A schedule that returns the past would make the loop spin."""
    now = at(hours=30)
    due = next_channel_poll(now, live_posts=[PUBLISHED], last_polled_at=now)
    assert due >= now


def test_the_window_covers_the_horizon() -> None:
    # Roughly two days of posts, plus the padding that lets the loop tell
    # "this is the last live post" from "the response was truncated".
    assert window_size(10.0) >= 20


def test_the_window_stays_inside_one_request() -> None:
    """The ceiling is what keeps a poll to a single `getHistory`."""
    assert window_size(1000.0) == settings.watch_window_max
    assert window_size(None) == settings.watch_window_min


@pytest.mark.parametrize(
    ("hour", "quiet"),
    [(3, True), (6, True), (7, False), (1, False), (23, False)],
)
def test_quiet_hours_cover_the_configured_window(
    hour: int, quiet: bool
) -> None:
    from zoneinfo import ZoneInfo

    moment = datetime(
        2026, 8, 3, hour, 30, tzinfo=ZoneInfo(settings.watch_timezone)
    )
    assert in_quiet_hours(moment) is quiet


def test_a_wrapping_quiet_window_is_two_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """22:00–06:00 crosses midnight, which a naive comparison gets backwards."""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(settings, "watch_quiet_from_hour", 22)
    monkeypatch.setattr(settings, "watch_quiet_to_hour", 6)
    zone = ZoneInfo(settings.watch_timezone)

    assert in_quiet_hours(datetime(2026, 8, 3, 23, 0, tzinfo=zone))
    assert in_quiet_hours(datetime(2026, 8, 3, 2, 0, tzinfo=zone))
    assert not in_quiet_hours(datetime(2026, 8, 3, 12, 0, tzinfo=zone))


def test_quiet_hours_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal bounds mean always on, not quiet for a whole day."""
    monkeypatch.setattr(settings, "watch_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "watch_quiet_to_hour", 0)

    assert not in_quiet_hours(datetime(2026, 8, 3, 3, 0, tzinfo=UTC))
    assert not in_quiet_hours(datetime(2026, 8, 3, 15, 0, tzinfo=UTC))


def test_the_quiet_window_is_read_in_the_configured_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point is the operator's night, and the machine may not be in it."""
    monkeypatch.setattr(settings, "watch_timezone", "Asia/Tokyo")
    from zoneinfo import ZoneInfo

    # 03:00 in Tokyo is quiet whatever the machine's clock says.
    tokyo_night = datetime(2026, 8, 3, 3, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert in_quiet_hours(tokyo_night.astimezone(UTC))
