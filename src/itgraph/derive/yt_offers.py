"""The daily selection of YouTube's rising channels for review in the
bot.

Which channels a day offers is pure: the link ranking's head first, then
the chart's — at most ``youtube_rising_link_offers_per_day`` of the one
and ``youtube_rising_chart_offers_per_region`` of each region of the
other — less every channel offered before, and a channel both rankings
hold is taken by the link one. A channel is offered once, ever; the
overflow waits for the next day's selection, which ranks again.

The pass around it loads the rankings' rows as `itgraph yt rising`
does, ranks as of now, and stores the day whole — an empty day too, so
that the series can tell "nothing rose" from "the pass did not run" —
creating each offered channel's ``candidate`` record without API data,
as the queues' ``--add`` does; the refresh fetches the payload at its
next run. The record of a link offer carries the source of its
strongest evidence, a chart offer's ``chart``. It makes no request,
takes no lease, holds no key, and says counts alone: a job's output is
kept, and only `--preview` names the channels — by id.

The decisions pressed in the bot are applied by `apply-reviews`, which
the detection job runs every 15 minutes: each once, oldest first, a
transaction each, as `itgraph yt mark` would apply it — and only to a
channel not reviewed some other way since.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from itgraph.config import settings
from itgraph.db.models import (
    YtAlertRoute,
    YtDiscoverySource,
    YtRisingRanking,
)
from itgraph.db.session import Database
from itgraph.db.yt_offers import (
    NewOffer,
    apply_decision,
    decided_unapplied,
    offered_channels,
    store_day,
)
from itgraph.db.yt_rising import load_yt_rising_rows
from itgraph.derive.yt_rising import (
    ChartChannel,
    FamilyKey,
    LinkReference,
    RisingChannel,
    YtRisingRows,
    appearances,
    rank_yt_rising,
)

__all__ = [
    "ApplySummary",
    "Chosen",
    "OfferSummary",
    "apply_reviews",
    "card_topic",
    "choose_offers",
    "offer_day",
    "run_offer",
    "strongest_sources",
]


@dataclass(frozen=True, slots=True)
class Chosen:
    """One channel the day takes, and what its ranking filled."""

    ranking: YtRisingRanking
    channel_id: str
    # A chart offer's place in its region's reading.
    region: str | None = None
    position: int | None = None
    # A link offer's evidence, as the ranking counted it.
    families: int | None = None
    families_before: int | None = None
    first_in_window: datetime | None = None


def choose_offers(
    links: Sequence[RisingChannel],
    chart: Sequence[ChartChannel],
    offered: AbstractSet[str],
    *,
    size_links: int,
    size_chart: int,
) -> list[Chosen]:
    """The day's offers: the link ranking's head first, then the
    chart's.

    ``links`` and ``chart`` are the rankings' lists, already in rank
    order. Each side skips channels offered before; the chart side also
    skips every channel the link side took, and counts its size per
    region. What neither side takes waits for the next day's selection,
    which ranks again.
    """
    chosen: list[Chosen] = []
    taken: set[str] = set()
    for item in links:
        if len(chosen) >= size_links:
            break
        if item.channel_id in offered or item.channel_id in taken:
            continue
        taken.add(item.channel_id)
        chosen.append(
            Chosen(
                ranking=YtRisingRanking.LINK,
                channel_id=item.channel_id,
                families=item.families,
                families_before=item.families_before,
                first_in_window=item.first_in_window,
            )
        )
    per_region: dict[str, int] = {}
    for row in chart:
        if row.channel_id in offered or row.channel_id in taken:
            continue
        if per_region.get(row.region, 0) >= size_chart:
            continue
        per_region[row.region] = per_region.get(row.region, 0) + 1
        taken.add(row.channel_id)
        chosen.append(
            Chosen(
                ranking=YtRisingRanking.CHART,
                channel_id=row.channel_id,
                region=row.region,
                position=row.position,
            )
        )
    return chosen


# Which topic a chart offer's region reads in; a region without an entry
# takes the English topic, as a link offer of an unknown language does.
CHART_TOPICS: Mapping[str, YtAlertRoute] = {
    "US": YtAlertRoute.EN,
    "RU": YtAlertRoute.RU,
}


def card_topic(ranking: YtRisingRanking, region: str | None) -> YtAlertRoute:
    """The topic an offer's card is stored to go to.

    Decided at selection, so the bot never re-derives a chart offer's; a
    link offer's is the English default — nothing tells its language
    before a payload exists, and once one does, the delivery reads the
    language from it.
    """
    if (
        ranking is YtRisingRanking.CHART
        and region is not None
        and region in CHART_TOPICS
    ):
        return CHART_TOPICS[region]
    return YtAlertRoute.EN


# A link offer's evidence sources, strongest first on a tie.
SOURCE_ORDER: tuple[YtDiscoverySource, ...] = (
    YtDiscoverySource.TELEGRAM,
    YtDiscoverySource.MENTION,
    YtDiscoverySource.FEATURED,
)


def strongest_sources(
    rows: YtRisingRows,
    moment: datetime,
    *,
    window_days: int,
    answers_fresh_days: int,
) -> Mapping[str, YtDiscoverySource]:
    """Each link-ranked channel's strongest evidence source: the one
    most of its families in the window came from, a tie going
    ``telegram``, then ``mention``, then ``featured``.

    The same three streams the ranking counts, under the same rule —
    evidence dated by its content, none after ``moment`` — so the source
    named is the source of the families the ranking judged the channel
    by.
    """
    start = moment - timedelta(days=window_days)

    def families_of(
        evidence: Sequence[LinkReference], source: YtDiscoverySource
    ) -> dict[str, set[FamilyKey]]:
        counted: dict[str, set[FamilyKey]] = {}
        for reference in evidence:
            if reference.at > moment or reference.at < start:
                continue
            counted.setdefault(reference.channel, set()).add(reference.family)
        return counted

    streams = (
        families_of(rows.links, YtDiscoverySource.TELEGRAM),
        families_of(rows.references, YtDiscoverySource.MENTION),
        families_of(
            appearances(rows.featured, moment, answers_fresh_days),
            YtDiscoverySource.FEATURED,
        ),
    )
    strongest: dict[str, YtDiscoverySource] = {}
    for channel in streams[0].keys() | streams[1].keys() | streams[2].keys():
        counted = {
            source: len(families[channel])
            for source, families in zip(SOURCE_ORDER, streams, strict=True)
            if channel in families
        }
        strongest[channel] = min(
            counted,
            key=lambda source: (
                -counted[source],
                SOURCE_ORDER.index(source),
            ),
        )
    return strongest


def offer_day(moment: datetime) -> date:
    """The day a selection computed at ``moment`` belongs to: the
    YouTube chat's date."""
    return moment.astimezone(ZoneInfo(settings.youtube_alert_timezone)).date()


@dataclass(slots=True)
class OfferSummary:
    """What one run of the selection did. Ids and counts only."""

    day: date
    rising_links: int = 0
    rising_chart: int = 0
    offered: int = 0
    left: int = 0
    stored: bool = False
    # The day had its selection already, and nothing was written.
    already: bool = False
    offers: list[Chosen] = field(default_factory=list)

    def line(self) -> str:
        if self.already:
            return f"{self.day} has its selection; nothing written"
        line = (
            f"{self.day}: {self.rising_links} rising by links, "
            f"{self.rising_chart} in the day's charts, "
            f"{self.offered} offered, {self.left} left for a later day"
        )
        return line if self.stored else f"{line} (preview, nothing written)"

    def preview(self) -> list[str]:
        """The offers by id, for an operator at a terminal."""
        lines: list[str] = []
        for rank, offer in enumerate(self.offers, start=1):
            if offer.ranking is YtRisingRanking.CHART:
                lines.append(
                    f"{rank:>3}. {offer.channel_id}  chart "
                    f"{offer.region} {offer.position}"
                )
            else:
                lines.append(
                    f"{rank:>3}. {offer.channel_id}  link  "
                    f"{offer.families} families in the window, "
                    f"{offer.families_before} before"
                )
        return lines or ["  none"]


def _stored_offers(
    chosen: Sequence[Chosen],
    sources: Mapping[str, YtDiscoverySource],
) -> list[NewOffer]:
    return [
        NewOffer(
            ranking=offer.ranking,
            channel_id=offer.channel_id,
            discovered_via=(
                YtDiscoverySource.CHART
                if offer.ranking is YtRisingRanking.CHART
                else sources[offer.channel_id]
            ),
            topic=card_topic(offer.ranking, offer.region),
            region=offer.region,
            position=offer.position,
            families=offer.families,
            families_before=offer.families_before,
            first_in_window=offer.first_in_window,
        )
        for offer in chosen
    ]


async def run_offer(
    database: Database,
    *,
    now: datetime | None = None,
    preview: bool = False,
) -> OfferSummary:
    """Compute and store the day's selection, or say the day has one.

    With ``preview`` nothing is written, and the offers are kept on the
    summary to be printed — by id, since a title or a handle may not
    exist yet and a job's output is kept.
    """
    moment = now or datetime.now(UTC)
    summary = OfferSummary(day=offer_day(moment))

    async with database.session() as session:
        rows = await load_yt_rising_rows(session, now=moment)
        offered = await offered_channels(session)
    ranked = rank_yt_rising(
        rows,
        moment,
        window_days=settings.rising_window_days,
        min_families=settings.rising_min_families,
        answers_fresh_days=settings.youtube_answers_fresh_days,
    )
    chosen = choose_offers(
        ranked.links,
        ranked.chart,
        offered,
        size_links=settings.youtube_rising_link_offers_per_day,
        size_chart=settings.youtube_rising_chart_offers_per_region,
    )
    summary.rising_links = len(ranked.links)
    summary.rising_chart = len(ranked.chart)
    summary.offered = len(chosen)
    fresh = len(
        [item for item in ranked.links if item.channel_id not in offered]
    ) + len([item for item in ranked.chart if item.channel_id not in offered])
    summary.left = fresh - len(chosen)
    if preview:
        summary.offers = list(chosen)
        return summary

    sources = strongest_sources(
        rows,
        moment,
        window_days=settings.rising_window_days,
        answers_fresh_days=settings.youtube_answers_fresh_days,
    )
    async with database.session() as session:
        stored = await store_day(
            session,
            day=summary.day,
            computed_at=moment,
            size_chart=settings.youtube_rising_chart_offers_per_region,
            size_links=settings.youtube_rising_link_offers_per_day,
            offers=_stored_offers(chosen, sources),
        )
        await session.commit()
    if stored is None:
        summary.already = True
        return summary
    summary.stored = True
    summary.offered = len(stored)
    return summary


@dataclass(slots=True)
class ApplySummary:
    """What one run of applying decisions did."""

    applied: int = 0
    not_applied: int = 0

    def line(self) -> str:
        return (
            f"youtube: {self.applied} decision(s) applied, "
            f"{self.not_applied} not applied: the channel was reviewed "
            "some other way first"
        )


async def apply_reviews(database: Database) -> ApplySummary:
    """Apply every decision taken in the bot and not yet applied."""
    summary = ApplySummary()
    async with database.session() as session:
        pending = await decided_unapplied(session)
    for decided in pending:
        async with database.session() as session:
            if await apply_decision(session, decided):
                summary.applied += 1
            else:
                summary.not_applied += 1
    return summary
