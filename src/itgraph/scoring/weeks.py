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

**The second wind** (`add-second-wind`) judges older items by their own
pace, not by a score: three readings around the week — the last at or
before the previous week's start, this week's start, and its end — give
the views a day the week before and this week. An item qualifies when
this week's pace is at least so many times the last's, or it did not
grow the week before, and a week at that pace is at least a share of its
channel's norm: the first condition leaves out channels whose old items
always gain from search, the second an item that went from 10 views to
30. Channels are ranked by that share.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from itgraph.scoring.score import Best

__all__ = [
    "MIN_INTERVAL",
    "WEEK",
    "Candidate",
    "Paces",
    "Ranked",
    "Revival",
    "WeekRefused",
    "check_week",
    "last_ended_week",
    "merge_best",
    "pace_of",
    "qualifies",
    "rank_revivals",
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


# --- the second wind ------------------------------------------------------

# The shortest interval a pace is measured over: a few hours between two
# readings would make a pace of the counter's noise.
MIN_INTERVAL = timedelta(days=2)

type Mark = tuple[datetime, float]


@dataclass(frozen=True, slots=True)
class Paces:
    """An item's views a day the week before and this week.

    ``previous_pace`` is ``None`` where the item did not grow the week
    before: its acceleration is then no number, and it is judged on size
    alone.
    """

    before_at: datetime
    start_at: datetime
    end_at: datetime
    previous_pace: float | None
    pace: float

    @property
    def acceleration(self) -> float | None:
        if self.previous_pace is None:
            return None
        return self.pace / self.previous_pace


def _days(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 86400


def pace_of(
    before: Mark | None,
    start: Mark | None,
    end: Mark | None,
    *,
    week_start: datetime,
) -> Paces | None:
    """The two paces from three readings, or ``None`` if not judged.

    Not judged: a reading missing, the last one not after the week's
    start, an interval under ``MIN_INTERVAL``, or fewer views at the end
    than at the start — a count taken away makes no second wind.
    """
    if before is None or start is None or end is None:
        return None
    (before_at, before_views), (start_at, start_views) = before, start
    end_at, end_views = end
    if end_at <= week_start:
        return None
    if start_at - before_at < MIN_INTERVAL or end_at - start_at < MIN_INTERVAL:
        return None
    if end_views < start_views:
        return None
    previous = (start_views - before_views) / _days(before_at, start_at)
    return Paces(
        before_at=before_at,
        start_at=start_at,
        end_at=end_at,
        previous_pace=previous if previous > 0 else None,
        pace=(end_views - start_views) / _days(start_at, end_at),
    )


def qualifies(
    paces: Paces, *, norm: float, acceleration: float, share: float
) -> bool:
    """Sped up enough, and by enough for its channel."""
    if norm <= 0:
        return False
    sped_up = paces.previous_pace is None or (
        paces.pace >= acceleration * paces.previous_pace
    )
    return sped_up and WEEK.days * paces.pace / norm >= share


@dataclass(frozen=True, slots=True)
class Candidate[C, P: (int, str)]:
    """An item judged for the second wind, with its channel's norm."""

    channel: C
    item: P
    paces: Paces
    norm: float

    @property
    def share(self) -> float:
        """A week at this pace, as a multiple of the channel's norm."""
        return WEEK.days * self.paces.pace / self.norm


@dataclass(frozen=True, slots=True)
class Revival[C, P: (int, str)]:
    """A channel's item that sped up most, and how many more did."""

    candidate: Candidate[C, P]
    others: int


def _revival_order[C, P: (int, str)](
    candidate: Candidate[C, P],
) -> tuple[float, float, P]:
    acceleration = candidate.paces.acceleration
    faster = float("inf") if acceleration is None else acceleration
    return (-candidate.share, -faster, candidate.item)


def rank_revivals[C, P: (int, str)](
    candidates: Iterable[Candidate[C, P]],
    *,
    acceleration: float,
    share: float,
) -> list[Revival[C, P]]:
    """Every channel with a qualifying item, the largest share first.

    One per channel, its largest share; a tie goes to the larger
    acceleration — no previous growth counting as the largest — then to
    the item's id.
    """
    qualifying: dict[C, list[Candidate[C, P]]] = {}
    for candidate in candidates:
        if qualifies(
            candidate.paces,
            norm=candidate.norm,
            acceleration=acceleration,
            share=share,
        ):
            qualifying.setdefault(candidate.channel, []).append(candidate)
    revivals = [
        Revival(
            candidate=min(found, key=_revival_order),
            others=len(found) - 1,
        )
        for found in qualifying.values()
    ]
    revivals.sort(key=lambda revival: _revival_order(revival.candidate))
    return revivals
