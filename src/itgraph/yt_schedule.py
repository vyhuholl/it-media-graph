"""When a YouTube video is read again, and when a channel is listed.

Pure arithmetic over moments: no database, no network, no clock of its
own. Every function takes the moment it reasons from, as ``schedule``'s
do, and the rule is the same one — a reading is chosen from the video's
current age and a reading missed is dropped, never taken late. That
rule is the only part shared with Telegram; ``schedule.next_sample_at``
applies it, with YouTube's offsets and horizon.

What is YouTube's own:

- **Where age starts.** A broadcast's actual start when it has one,
  the publication time otherwise. A stream scheduled days ahead is
  "published" when it is scheduled; counting its age from then would
  spend the early readings on a video nobody could watch yet.
- **Broadcasts that have not started** are checked, not read: at the
  scheduled start, then hourly, and given up a week after it.
- **Pace.** The loop's pool is spread across the Pacific quota day, so
  a busy morning cannot leave the evening blind.
- **Listing backoff,** for a channel whose uploads cannot be listed.
"""

from datetime import datetime, timedelta

from itgraph.config import settings
from itgraph.schedule import next_sample_at
from itgraph.yt_quota import day_start, next_reset

__all__ = [
    "check_due",
    "deferrable",
    "horizon",
    "listing_retry_at",
    "next_check_of_upcoming",
    "next_reading_at",
    "origin",
    "pace_allows",
    "sample_offsets",
]

# How often a broadcast past its scheduled start is asked about again.
# Its first reading waits on this, so it is the latency of noticing a
# stream began; an hour costs one slot in a fifty-id request.
UPCOMING_CHECK = timedelta(hours=1)

# The ceiling on a failing channel's wait. A day, so a channel that comes
# back is listed again no later than the daily check would list it.
LISTING_BACKOFF_CAP = timedelta(hours=24)


def sample_offsets() -> tuple[timedelta, ...]:
    """The configured reading schedule, as ages."""
    return tuple(
        timedelta(minutes=minutes)
        for minutes in settings.youtube_watch_sample_offsets
    )


def horizon() -> timedelta:
    """Past this age a video is not read again."""
    return timedelta(days=settings.youtube_watch_horizon_days)


def origin(
    published_at: datetime | None, actual_start: datetime | None
) -> datetime | None:
    """Where a video's age is counted from.

    The broadcast's actual start when there is one, so a finished stream
    is aged from when people could watch it. Both moments stay in the
    stored payload, so a later pass can count from the other.
    """
    return actual_start if actual_start is not None else published_at


def next_reading_at(start: datetime, now: datetime) -> datetime | None:
    """When a started video is read next, or ``None`` once it is finished."""
    return next_sample_at(
        start, now, offsets=sample_offsets(), horizon=horizon()
    )


def next_check_of_upcoming(
    now: datetime,
    *,
    scheduled_start: datetime | None,
    tracked_since: datetime,
    fetched_at: datetime,
) -> datetime | None:
    """When to ask again about a broadcast that has not started.

    At its scheduled start, then hourly; ``None`` once the start is
    further behind than the give-up period. A broadcast with no
    scheduled start is counted from when it began to be tracked.

    Never later than the refresh age after the last fetch: a stream
    scheduled forty days ahead would otherwise sit on data older than
    the policy allows, and expire before it started.
    """
    anchor = scheduled_start if scheduled_start is not None else tracked_since
    give_up = timedelta(days=settings.youtube_watch_upcoming_give_up_days)
    if now >= anchor + give_up:
        return None

    due = anchor if now < anchor else now + UPCOMING_CHECK
    refetch_by = fetched_at + timedelta(
        days=settings.youtube_refresh_after_days
    )
    return max(min(due, refetch_by), now)


def deferrable(start: datetime, now: datetime) -> bool:
    """Whether this video's reading may wait when spending is ahead of pace.

    Old enough that a reading taken later costs least: its counters move
    slowly, and the next reading is days away anyway.
    """
    return now - start > timedelta(
        days=settings.youtube_watch_defer_older_than_days
    )


def pace_allows(spent: int, cap: int, now: datetime) -> bool:
    """Whether the loop is within the share of its pool the day allows so far.

    The pool spread evenly across the quota day, plus a margin — a
    share of the cap, not of the elapsed part, so the first pass of a day
    is never ahead of pace. The day is Google's, Pacific, 23 or 25 hours
    long twice a year; dividing by its real length is what keeps those
    two days from deferring too much or too little.
    """
    start = day_start(now)
    elapsed = (now - start) / (next_reset(now) - start)
    allowed = cap * elapsed + cap * settings.youtube_watch_pace_margin
    return spent <= allowed


def check_due(listed_at: datetime | None, now: datetime) -> bool:
    """Whether a channel's daily check of its uploads has come round.

    Counted from the last listing for any reason, so a channel listed
    because its count moved is not listed again for the check.
    """
    if listed_at is None:
        return True
    return now - listed_at >= timedelta(
        hours=settings.youtube_watch_check_hours
    )


def listing_retry_at(attempted_at: datetime, failures: int) -> datetime:
    """When a channel whose listing failed may be tried again.

    Doubling from two hours to a day. Without it a broken channel would
    be retried every pass — 96 units a day for an answer that does not
    change.
    """
    if failures <= 0:
        return attempted_at
    wait = timedelta(hours=2 ** min(failures, 5))
    return attempted_at + min(wait, LISTING_BACKOFF_CAP)
