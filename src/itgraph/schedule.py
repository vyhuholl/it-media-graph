"""When to read a post again, and when to look at a channel at all.

Pure arithmetic over ages and rates: no database, no clock of its own,
no network. Every function takes the moment it should reason from, so a
test can state a scenario instead of arranging for time to pass.

**The rule the whole module is built on: a sample is chosen from the
post's current age, never from which sample was due last.** That single
decision is what makes the loop survive a closed laptop. A queue that
remembered what it owed would wake after eight hours with 565 channels
overdue and grind through the backlog at full rate for hours — the exact
traffic shape the pacing exists to avoid — and it would spend those
requests on readings that are worthless anyway. A sample due at post-age
30 minutes and taken at post-age 8 hours is not a late reading of the
early curve; it is a different measurement wearing its name.

So missed samples are dropped. The cost is real and is accepted: posts
published during quiet hours or an outage have no early curve, and are
scoreable on the late signals only. The consequence for everything
downstream is that **a snapshot's age is a fact about the row**
(``observed_at`` minus the publication date) and never an assumption
about the schedule.
"""

from collections.abc import Sequence
from datetime import datetime, time, timedelta

from itgraph.config import settings

__all__ = [
    "backoff_factor",
    "idle_interval",
    "in_quiet_hours",
    "in_quiet_window",
    "next_channel_poll",
    "next_sample_at",
    "quiet_until",
    "sample_offsets",
    "window_size",
]


def sample_offsets() -> tuple[timedelta, ...]:
    """The configured reading schedule, as durations after publication."""
    return tuple(
        timedelta(minutes=minutes) for minutes in settings.watch_sample_offsets
    )


def horizon() -> timedelta:
    """Past this age a post is finished and is not read again."""
    return timedelta(hours=settings.watch_horizon_hours)


def next_sample_at(
    published_at: datetime,
    now: datetime,
    *,
    offsets: Sequence[timedelta] | None = None,
) -> datetime | None:
    """When this post should next be read, or ``None`` if it is finished.

    The first offset that lands strictly after ``now`` — so a post whose
    early samples elapsed while nothing was running resumes at whichever
    sample is next from where it actually is, and the elapsed ones are
    simply gone.

    ``None`` once the post is past the last offset or past the horizon:
    it has stopped moving, and reading it again would cost a request to
    learn a number that no longer changes.
    """
    schedule = tuple(offsets) if offsets is not None else sample_offsets()
    age = now - published_at
    if age >= horizon():
        return None
    for offset in schedule:
        if offset > age:
            return published_at + offset
    return None


def idle_interval(posts_per_day: float | None) -> timedelta:
    """How long to leave a channel that has nothing live to watch.

    Derived from the channel's own rhythm — the mean gap between its
    posts, divided so that several checks fall inside one expected gap —
    and then clamped at both ends. The clamps are what make the
    arithmetic safe as well as sensible: a channel that has never posted
    has no rate to divide by, and a prolific one would otherwise be
    checked every few minutes on a path meant for quiet channels.

    A channel with no known rate gets the slowest interval rather than
    the fastest. Being wrong in that direction costs a late discovery;
    being wrong in the other costs requests on 111 channels that have
    published nothing in a month.
    """
    low = timedelta(minutes=settings.watch_idle_min_minutes)
    high = timedelta(minutes=settings.watch_idle_max_minutes)
    if not posts_per_day or posts_per_day <= 0:
        return high

    mean_gap = timedelta(days=1) / posts_per_day
    return max(low, min(high, mean_gap / settings.watch_idle_divisor))


def backoff_factor(*, empty: int = 0, failures: int = 0) -> float:
    """How much to stretch an interval for a channel that keeps quiet or fails.

    Two different situations with two different multipliers, because they
    are different facts. A channel that keeps coming back empty is
    working correctly and simply has nothing to say; a channel that keeps
    failing may be gone. Both are capped: neither is a reason to stop
    checking altogether, and an uncapped exponential would quietly retire
    a channel that came back.
    """
    stretch = settings.watch_empty_backoff ** min(
        empty, settings.watch_empty_backoff_cap
    )
    if failures:
        stretch *= settings.watch_failure_backoff ** min(
            failures, settings.watch_failure_backoff_cap
        )
    return stretch


def next_channel_poll(
    now: datetime,
    *,
    live_posts: Sequence[datetime] = (),
    posts_per_day: float | None = None,
    last_polled_at: datetime | None = None,
    consecutive_empty: int = 0,
    consecutive_failures: int = 0,
) -> datetime:
    """When to poll one channel next.

    The earliest reading due over the posts still inside the horizon —
    because one request refreshes all of them at once, so the channel's
    schedule is the union of its posts' schedules rather than a sum of
    their costs. With nothing live, the channel's own posting rate
    decides instead.

    The floor is applied last and to everything: no channel is polled
    twice inside the minimum gap, however many posts it just published.
    An album arriving as five messages would otherwise be five readings
    due within a minute of one another, each costing the same request.

    Backoff stretches the idle path only. A channel with a live post has
    a reason to be read at a particular moment, and "it was quiet last
    week" is not an argument against reading a post that exists now.
    """
    due: datetime | None = None
    for published_at in live_posts:
        sample = next_sample_at(published_at, now)
        if sample is not None and (due is None or sample < due):
            due = sample

    if due is None:
        interval = idle_interval(posts_per_day) * backoff_factor(
            empty=consecutive_empty, failures=consecutive_failures
        )
        if consecutive_failures:
            interval = min(
                interval,
                timedelta(minutes=settings.watch_failure_max_minutes),
            )
        due = now + interval

    if last_polled_at is not None:
        floor = last_polled_at + timedelta(
            minutes=settings.watch_min_gap_minutes
        )
        due = max(due, floor)
    return max(due, now)


def window_size(posts_per_day: float | None) -> int:
    """How many messages one poll asks for.

    Enough to cover everything published inside the horizon, with the
    bounds doing the real work. The ceiling is what keeps a poll to a
    single request: a channel needing more would have to publish over
    fifty times a day, and the busiest in this inventory manages
    twenty-nine.

    Asking for the ceiling every time would cost the same in requests —
    which is what is rationed — and more in bytes, for nothing.
    """
    if not posts_per_day or posts_per_day <= 0:
        return settings.watch_window_min
    expected = posts_per_day * settings.watch_horizon_hours / 24.0
    # Rounded up and then padded by one: a window that ends exactly at
    # the horizon cannot tell "this is the last live post" from "the
    # response was truncated".
    wanted = int(expected) + 2
    return max(
        settings.watch_window_min, min(settings.watch_window_max, wanted)
    )


def in_quiet_window(
    moment: datetime, *, start: int, end: int, zone: str
) -> bool:
    """Whether a moment falls inside a nightly window.

    Takes the window rather than reading a setting, because there are two
    of them now and they mean different things: the collector's is about
    not making requests, the bot's about not making noise. They coincide
    today only because both are the operator's night, and a shared
    setting would make diverging cost a migration.

    Read in the given zone, not the machine's — the point is somebody's
    night, and the machine may not be in it. Equal bounds mean no window
    at all, which is how it is switched off: an operator setting both to
    the same hour means "always on", not "quiet for zero seconds and also
    for a whole day".
    """
    from zoneinfo import ZoneInfo

    if start == end:
        return False

    local = moment.astimezone(ZoneInfo(zone))
    now = time(hour=local.hour, minute=local.minute)
    if start < end:
        return time(hour=start) <= now < time(hour=end)
    # Wrapping past midnight: 22:00-06:00 is two intervals, not one.
    return now >= time(hour=start) or now < time(hour=end)


def in_quiet_hours(moment: datetime) -> bool:
    """Whether the *collector* should be asleep at this moment."""
    return in_quiet_window(
        moment,
        start=settings.watch_quiet_from_hour,
        end=settings.watch_quiet_to_hour,
        zone=settings.watch_timezone,
    )


def quiet_until(
    moment: datetime, *, start: int, end: int, zone: str
) -> datetime | None:
    """When this quiet window lets go, or ``None`` if it is not holding.

    Exists so a status command can say "paused until 07:00" rather than
    leave the reader to work it out. A paused loop and a stuck one look
    identical from the outside — "2 due now, oldest overdue by 5h" is
    exactly what both produce — and telling them apart is the whole job
    of a status command in a system whose healthy state is silence.

    Returned as a moment rather than a duration because the answer a
    reader wants is a clock time they can compare against their own.
    """
    from zoneinfo import ZoneInfo

    if not in_quiet_window(moment, start=start, end=end, zone=zone):
        return None

    local = moment.astimezone(ZoneInfo(zone))
    release = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if release <= local:
        release += timedelta(days=1)
    return release
