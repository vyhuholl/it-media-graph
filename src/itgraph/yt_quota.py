"""The YouTube quota day, and what the general pool may spend in it.

Pure arithmetic: no settings, no database, no clock of its own.

The day is Google's, not ours. The quota resets at midnight Pacific time,
so spending belongs to the Pacific date it happened on — which is 07:00
or 08:00 UTC depending on the season, and a day of 23 or 25 hours twice
a year. Computed through the zone database rather than as an offset, so
neither needs a special case.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

__all__ = ["QUOTA_ZONE", "general_cap", "next_reset", "quota_day"]

QUOTA_ZONE = ZoneInfo("America/Los_Angeles")


def quota_day(moment: datetime) -> date:
    """The Pacific date that spending at ``moment`` counts towards."""
    return moment.astimezone(QUOTA_ZONE).date()


def next_reset(moment: datetime) -> datetime:
    """When the quota day containing ``moment`` ends, in UTC.

    Midnight is never skipped or repeated in Pacific time — the clocks
    change at two in the morning — so the wall-clock midnight always
    exists exactly once.
    """
    following = quota_day(moment) + timedelta(days=1)
    midnight = datetime.combine(following, time(), tzinfo=QUOTA_ZONE)
    return midnight.astimezone(UTC)


def general_cap(daily: int, margin: float, reserved: int) -> int:
    """What every command but the poll loop may spend in one quota day.

    The daily quota less the margin held against the console's own
    count, less every share reserved for another pool. Rounded rather
    than truncated, so 10% of 10,000 is a thousand and not, by way of
    binary fractions, 999.
    """
    return daily - round(daily * margin) - reserved
