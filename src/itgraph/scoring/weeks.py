"""A week of publication, and a ranking over it; pure, both platforms'.

What the weekly lists share, YouTube's per language and Telegram's one: a
week runs from Monday 00:00 to the next Monday 00:00 in the readers'
zone, a list is of the last week that has ended, and a week that has not
ended — or a date that is not a Monday — is refused before anything is
read.

**The ranking** takes each item's best score of the week — a video's or
a post's, across its readings and signals — and keeps, per channel, its
best item at or above the floor and how many more reached it. Channels
are ordered by that score, highest first; a tie goes to the earlier
crossing, then to the item's id, so the order never depends on the order
the scores arrived in.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from itgraph.scoring.score import Best

__all__ = [
    "WEEK",
    "Ranked",
    "WeekRefused",
    "check_week",
    "last_ended_week",
    "merge_best",
    "rank_week",
    "week_bounds",
]

WEEK = timedelta(days=7)


class WeekRefused(ValueError):
    """A week that cannot be listed: not a Monday, or not yet ended."""


def week_bounds(week_start: date, *, zone: str) -> tuple[datetime, datetime]:
    """The week's first moment and the next week's, in UTC."""
    tz = ZoneInfo(zone)
    start = datetime.combine(week_start, time(), tzinfo=tz)
    end = datetime.combine(week_start + WEEK, time(), tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def last_ended_week(moment: datetime, *, zone: str) -> date:
    """The Monday of the last week that had ended by ``moment``."""
    local = moment.astimezone(ZoneInfo(zone)).date()
    this_monday = local - timedelta(days=local.weekday())
    return this_monday - WEEK


def check_week(week_start: date, *, moment: datetime, zone: str) -> None:
    """Refuse a week that is not a Monday's, or has not ended by ``moment``."""
    if week_start.weekday() != 0:
        raise WeekRefused(f"{week_start} is not a Monday")
    _, end = week_bounds(week_start, zone=zone)
    if end > moment:
        raise WeekRefused(f"the week of {week_start} has not ended")


def merge_best(found: Best, current: Best | None) -> Best:
    """The better of two bests of one item, crossing at the earlier."""
    if current is None:
        return found
    crossings = [
        moment
        for moment in (found.crossed_at, current.crossed_at)
        if moment is not None
    ]
    better = found if found.score.z > current.score.z else current
    return Best(
        score=better.score,
        crossed_at=min(crossings) if crossings else None,
        scored_at=better.scored_at,
    )


@dataclass(frozen=True, slots=True)
class Ranked[C, P: (int, str)]:
    """A channel's best item of the week, and how many more reached the floor."""

    channel: C
    item: P
    best: Best
    others: int


def _order[P: (int, str)](item: P, best: Best) -> tuple[float, datetime, P]:
    crossed = best.crossed_at or datetime.max.replace(tzinfo=UTC)
    return (-best.score.z, crossed, item)


def rank_week[C, P: (int, str)](
    best: Mapping[tuple[C, P], Best], *, floor: float
) -> list[Ranked[C, P]]:
    """Every channel with an item at or above the floor, best first."""
    above: dict[C, list[tuple[P, Best]]] = {}
    for (channel, item), found in best.items():
        if found.score.z >= floor:
            above.setdefault(channel, []).append((item, found))
    ranked = []
    for channel, items in above.items():
        item, top = min(items, key=lambda pair: _order(*pair))
        ranked.append(
            Ranked(channel=channel, item=item, best=top, others=len(items) - 1)
        )
    ranked.sort(key=lambda entry: _order(entry.item, entry.best))
    return ranked
