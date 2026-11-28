"""The daily selection of YouTube's rising channels for review in the
bot.

Which channels a day offers is pure: the ranking's head — at most
``youtube_rising_link_offers_per_day`` of it — less every channel
offered before. A channel is offered once, ever; the overflow waits
for the next day's selection, which ranks again.

The pass around it loads the ranking's rows as `itgraph yt rising`
does, ranks as of now, and stores the day whole — an empty day too, so
that the series can tell "nothing rose" from "the pass did not run" —
creating each offered channel's ``candidate`` record, as the queues'
``--add`` does. Where a key is configured it buys the offered channels'
payloads with the selection's one call of its own, stored in the same
transaction as the day, before the bot can claim an offer, so a card is
titled the day it is sent; where none is — the test environment's
selection, which holds no key — the day is stored without a request, as
it always was, and the night refresh fetches the payload. The record of
an offer carries the source of its strongest evidence. The pass takes
no lease, and says counts alone: a job's output is kept, and only
`--preview` names the channels — by id.

The decisions pressed in the bot are applied by `apply-reviews`, which
the detection job runs every 15 minutes: each once, oldest first, a
transaction each, as `itgraph yt mark` would apply it — and only to a
channel not reviewed some other way since.
"""

import logging
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from itgraph.config import settings
from itgraph.db.models import (
    YtAlertRoute,
    YtCommand,
    YtDiscoverySource,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import store_payloads
from itgraph.db.yt_offers import (
    NewOffer,
    apply_decision,
    decided_unapplied,
    offered_channels,
    store_day,
)
from itgraph.db.yt_quota import QuotaError
from itgraph.db.yt_rising import load_yt_rising_rows
from itgraph.derive.yt_rising import (
    FamilyKey,
    LinkReference,
    RisingChannel,
    YtRisingRows,
    appearances,
    rank_yt_rising,
)
from itgraph.yt import client as yt_client
from itgraph.yt.channels import by_ids
from itgraph.yt.errors import MissingKeyError, YtError

__all__ = [
    "ApplySummary",
    "OfferSummary",
    "apply_reviews",
    "choose_offers",
    "offer_day",
    "run_offer",
    "strongest_sources",
]


# The topic an offer's card is stored to go to: the English default —
# nothing tells its language before a payload exists, and once one
# does, the delivery reads the language from it.
CARD_TOPIC = YtAlertRoute.EN

logger = logging.getLogger(__name__)

# What one bought payload looks like, as `channels.by_ids` returns it.
BoughtPayloads = Mapping[str, Mapping[str, object]]


def choose_offers(
    links: Sequence[RisingChannel],
    offered: AbstractSet[str],
    *,
    size_links: int,
) -> list[RisingChannel]:
    """The day's offers: the ranking's head.

    ``links`` is the ranking's list, already in rank order. It skips
    channels offered before. What it does not take waits for the next
    day's selection, which ranks again.
    """
    chosen: list[RisingChannel] = []
    for item in links:
        if len(chosen) >= size_links:
            break
        if item.channel_id in offered:
            continue
        chosen.append(item)
    return chosen


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
        evidence: Sequence[LinkReference],
    ) -> dict[str, set[FamilyKey]]:
        counted: dict[str, set[FamilyKey]] = {}
        for reference in evidence:
            if reference.at > moment or reference.at < start:
                continue
            counted.setdefault(reference.channel, set()).add(reference.family)
        return counted

    streams = (
        families_of(rows.links),
        families_of(rows.references),
        families_of(appearances(rows.featured, moment, answers_fresh_days)),
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
    offered: int = 0
    left: int = 0
    stored: bool = False
    # The day had its selection already, and nothing was written.
    already: bool = False
    offers: list[RisingChannel] = field(default_factory=list)

    def line(self) -> str:
        if self.already:
            return f"{self.day} has its selection; nothing written"
        line = (
            f"{self.day}: {self.rising_links} rising by links, "
            f"{self.offered} offered, {self.left} left for a later day"
        )
        return line if self.stored else f"{line} (preview, nothing written)"

    def preview(self) -> list[str]:
        """The offers by id, for an operator at a terminal."""
        lines = [
            f"{rank:>3}. {offer.channel_id}  link  "
            f"{offer.families} families in the window, "
            f"{offer.families_before} before"
            for rank, offer in enumerate(self.offers, start=1)
        ]
        return lines or ["  none"]


def _stored_offers(
    chosen: Sequence[RisingChannel],
    sources: Mapping[str, YtDiscoverySource],
) -> list[NewOffer]:
    return [
        NewOffer(
            channel_id=offer.channel_id,
            discovered_via=sources[offer.channel_id],
            topic=CARD_TOPIC,
            families=offer.families,
            families_before=offer.families_before,
            first_in_window=offer.first_in_window,
        )
        for offer in chosen
    ]


async def _buy_payloads(
    database: Database, chosen: Sequence[RisingChannel]
) -> BoughtPayloads:
    """The offered channels' payloads, bought as the day is computed.

    One call for everything the day offers, whatever the sizes say.
    Empty, and the day is stored as it always was, when no key is
    configured — the test environment's selection — or the call fails:
    a card without a title is the fallback, not a reason to lose the
    day.
    """
    if not chosen:
        return {}
    try:
        # Through the module, as the refresh does, so a test can put a
        # fake API behind the same call the job makes.
        async with yt_client.youtube(database, YtCommand.OFFER) as client:
            return await by_ids(client, [offer.channel_id for offer in chosen])
    except MissingKeyError:
        return {}
    except (QuotaError, YtError) as exc:
        logger.warning("could not buy the offered payloads: %s", exc)
        return {}


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
        offered,
        size_links=settings.youtube_rising_link_offers_per_day,
    )
    summary.rising_links = len(ranked.links)
    summary.offered = len(chosen)
    fresh = len(
        [item for item in ranked.links if item.channel_id not in offered]
    )
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
    # Bought before the day is stored: the payloads land in the same
    # transaction, so the bot never claims an offer whose payload could
    # have been there.
    payloads = await _buy_payloads(database, chosen)
    fetched_at = datetime.now(UTC)
    async with database.session() as session:
        stored = await store_day(
            session,
            day=summary.day,
            computed_at=moment,
            size_links=settings.youtube_rising_link_offers_per_day,
            offers=_stored_offers(chosen, sources),
        )
        if stored is not None:
            await store_payloads(session, payloads, fetched_at=fetched_at)
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
