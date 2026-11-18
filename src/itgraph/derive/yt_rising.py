"""Which YouTube channels are rising, and in what order.

Rising, for links, means newly referred to by several families at once:
at least ``min_families`` distinct families — Telegram families for
Telegram posts, YouTube families for references and appearances, a
family of one platform and a family of the other counting as two —
referred to it within the trailing window. Evidence is dated by its
content: a post's or a video's publication time, an appearance by the
reading that first lists it. An old video's description naming a
channel is old evidence, whenever it was parsed.

Three sources of evidence, each naming a channel by its own rules: a
Telegram seed's post linking it; a YouTube seed's video referencing it
in title or description — a standing feature is not this ranking's
evidence, only an appearance is; and a YouTube seed newly featuring it
in its sections. A channel the inventory holds is never ranked,
whatever its status: the manual review round owns those.

The chart ranking is the other half: the channels of the day's reading
of each region, absent from the inventory, in the reading's order —
every one of them, no threshold and no filter.

Pure: plain rows and a moment in, the two rankings and the counts out.
Nothing here is stored; ``itgraph yt rising`` prints it, and the
selection of the review change will call the same functions. Logs of
any of it name ids only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

__all__ = [
    "ChartChannel",
    "FeaturedReadings",
    "LinkReference",
    "RisingChannel",
    "YtRising",
    "YtRisingRows",
    "appearances",
    "rank_yt_rising",
    "report",
]

# A family's key as both platforms' rankings count it: the platform's
# own key, prefixed, so a Telegram family and a YouTube family never
# collide and an unaffiliated channel is its own family.
FamilyKey = str


@dataclass(frozen=True, slots=True)
class LinkReference:
    """One seed's post or video naming a YouTube channel, and when.

    ``family`` is the referrer's — a Telegram family for a Telegram
    post, a YouTube family for a video's reference.
    """

    channel: str
    family: FamilyKey
    at: datetime


@dataclass(frozen=True, slots=True)
class FeaturedReadings:
    """A seed's two newest featured readings, as the loader read them.

    ``previous`` is ``None`` for a seed with one reading: the first
    establishes the list, and nothing in it is an appearance.
    """

    seed: str
    family: FamilyKey
    newest_at: datetime
    newest: tuple[str, ...]
    previous: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class YtRisingRows:
    """Everything both rankings read, as the loader read it.

    ``charts`` holds, per region, the day's reading as (video id,
    channel id) pairs in chart order; a configured region with no
    reading that day is absent from it and is said by the ranking.
    """

    inventory: frozenset[str]
    links: tuple[LinkReference, ...] = ()
    references: tuple[LinkReference, ...] = ()
    featured: tuple[FeaturedReadings, ...] = ()
    chart_regions: tuple[str, ...] = ()
    charts: Mapping[str, Sequence[tuple[str, str]]] = field(
        default_factory=dict
    )
    awaiting_lookup: int = 0
    not_found: int = 0
    lists_out_of_date: int = 0


@dataclass(frozen=True, slots=True)
class RisingChannel:
    """One link-ranked channel, in ranking order.

    ``first_in_window`` is its earliest evidence inside the window,
    whatever family it came from.
    """

    channel_id: str
    families: int
    families_before: int
    first_in_window: datetime | None


@dataclass(frozen=True, slots=True)
class ChartChannel:
    """One channel of a region's chart, at its position in the reading."""

    channel_id: str
    region: str
    position: int


@dataclass(slots=True)
class YtRising:
    """The two rankings, and what could not be counted."""

    links: list[RisingChannel] = field(default_factory=list)
    chart: list[ChartChannel] = field(default_factory=list)
    chart_without: list[str] = field(default_factory=list)
    awaiting_lookup: int = 0
    not_found: int = 0
    lists_out_of_date: int = 0


@dataclass(slots=True)
class _Evidence:
    """The families behind one channel, gathered before it is judged."""

    within: set[FamilyKey] = field(default_factory=set)
    before: set[FamilyKey] = field(default_factory=set)
    first_in_window: datetime | None = None

    def saw(self, family: FamilyKey, at: datetime, start: datetime) -> None:
        if at < start:
            self.before.add(family)
            return
        self.within.add(family)
        if self.first_in_window is None or at < self.first_in_window:
            self.first_in_window = at


def appearances(
    rows: Sequence[FeaturedReadings],
    moment: datetime,
    answers_fresh_days: int,
) -> list[LinkReference]:
    """What appeared in each seed's list: a channel in the newest
    reading that the one before it, of any age, lacked.

    A seed whose newest reading is out of date contributes nothing; a
    seed's own channel in its own list is no evidence of anything.
    """
    cutoff = moment - timedelta(days=answers_fresh_days)
    appeared: list[LinkReference] = []
    for reading in rows:
        if reading.newest_at < cutoff or reading.previous is None:
            continue
        for channel in reading.newest:
            if channel in reading.previous or channel == reading.seed:
                continue
            appeared.append(
                LinkReference(
                    channel=channel,
                    family=reading.family,
                    at=reading.newest_at,
                )
            )
    return appeared


def rank_yt_rising(
    rows: YtRisingRows,
    moment: datetime,
    *,
    window_days: int,
    min_families: int,
    answers_fresh_days: int,
) -> YtRising:
    """Rank what is rising as of ``moment``.

    Links first: most families in the window; a tie to the one fewer
    families referred to before the window, as the newer; then by
    channel id, so the same rows always give the same ranking. Evidence
    after ``moment`` is not read. Then the chart: every channel of the
    day's reading the inventory does not hold, in the reading's order.
    """
    start = moment - timedelta(days=window_days)
    evidence: dict[str, _Evidence] = {}
    for reference in (*rows.links, *rows.references):
        if reference.at > moment:
            continue
        evidence.setdefault(reference.channel, _Evidence()).saw(
            reference.family, reference.at, start
        )
    for appearance in appearances(rows.featured, moment, answers_fresh_days):
        evidence.setdefault(appearance.channel, _Evidence()).saw(
            appearance.family, appearance.at, start
        )

    rising = YtRising(
        awaiting_lookup=rows.awaiting_lookup,
        not_found=rows.not_found,
        lists_out_of_date=rows.lists_out_of_date,
    )
    ranked: list[tuple[tuple[int, int, str], RisingChannel]] = []
    for channel_id, item in evidence.items():
        if channel_id in rows.inventory or len(item.within) < min_families:
            continue
        candidate = RisingChannel(
            channel_id=channel_id,
            families=len(item.within),
            families_before=len(item.before),
            first_in_window=item.first_in_window,
        )
        ranked.append(
            (
                (-candidate.families, candidate.families_before, channel_id),
                candidate,
            )
        )
    rising.links = [row for _, row in sorted(ranked, key=lambda pair: pair[0])]

    for region in rows.chart_regions:
        reading = rows.charts.get(region, ())
        if not reading:
            rising.chart_without.append(region)
            continue
        # A channel that charts with two videos ranks once, at the
        # position of its first.
        seen: set[str] = set()
        for position, (_, channel_id) in enumerate(reading, start=1):
            if channel_id in rows.inventory or channel_id in seen:
                continue
            seen.add(channel_id)
            rising.chart.append(
                ChartChannel(
                    channel_id=channel_id, region=region, position=position
                )
            )
    return rising


def report(
    rising: YtRising,
    *,
    top: int,
    window_days: int,
    min_families: int,
) -> list[str]:
    """The lines ``itgraph yt rising`` prints.

    The link ranking's head — each channel's id, its families in the
    window and before it, its first reference in the window — then the
    chart ranking's rows with region and position, then what could not
    be counted. Ids, numbers and dates, and nothing read from the API
    beyond the ids: no title, no handle, no count of subscribers.
    """
    lines = [
        (
            f"rising: referred to by {min_families}+ families "
            f"in the last {window_days} days"
        )
    ]
    shown = rising.links[:top] if top else rising.links
    for rank, item in enumerate(shown, start=1):
        first = (
            f"{item.first_in_window:%Y-%m-%d}"
            if item.first_in_window is not None
            else "unknown"
        )
        lines.append(
            f"{rank:>3}. {item.channel_id}  "
            f"{item.families} families in the window, "
            f"{item.families_before} before; "
            f"first in the window {first}"
        )
    if not shown:
        lines.append("  none")
    if len(rising.links) > len(shown):
        lines.append(f"  … and {len(rising.links) - len(shown)} more")

    lines.append("chart channels of the day:")
    for row in rising.chart:
        lines.append(f"  {row.region}  {row.position:>3}  {row.channel_id}")
    if not rising.chart:
        lines.append("  none")
    for region in rising.chart_without:
        lines.append(f"  {region} has no chart reading today")

    lines.append(
        f"awaiting a lookup: {rising.awaiting_lookup}"
        + (
            " — run `itgraph yt rising-lookups`"
            if rising.awaiting_lookup
            else ""
        )
    )
    lines.append(f"looked up, no channel: {rising.not_found} references")
    lines.append(
        f"seeds with a featured list out of date: "
        f"{rising.lists_out_of_date} — run `itgraph yt refresh`"
        if rising.lists_out_of_date
        else f"seeds with a featured list out of date: "
        f"{rising.lists_out_of_date}"
    )
    return lines
