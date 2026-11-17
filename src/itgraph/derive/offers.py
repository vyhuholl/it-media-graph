"""The daily selection of Telegram's rising channels for review in the bot.

Which channels a day offers is pure: the ranking's channels with a
username, in rank order, less every channel offered before, up to the
day's size. A channel is offered once, ever; the overflow waits for the
next day's selection, which ranks again.

The pass around it loads the ranking's rows as `itgraph rising` does,
ranks as of now and stores the day whole — an empty day too, so that the
series can tell "nothing rose" from "the pass did not run". It makes no
request, takes no lease, and says counts alone: a job's output is kept,
and only `--preview` names the channels.

The decisions pressed in the bot are applied by `apply_reviews`, which
the detection job runs every 15 minutes: each once, oldest first, a
transaction each, as `itgraph mark` would apply it — and only to a
channel not reviewed some other way since.
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from itgraph.config import settings
from itgraph.db.offers import (
    NewOffer,
    apply_decision,
    decided_unapplied,
    offered_channels,
    store_day,
)
from itgraph.db.rising import load_rising_rows
from itgraph.db.session import Database
from itgraph.derive.rising import Rising, rank_rising

__all__ = [
    "ApplySummary",
    "OfferSummary",
    "apply_reviews",
    "choose_offers",
    "offer_day",
    "run_offer",
]


def choose_offers(
    ranked: Sequence[Rising], offered: AbstractSet[int], size: int
) -> list[NewOffer]:
    """The day's offers: the first ``size`` ranked channels never offered.

    ``ranked`` is the ranking's list of channels with a username, already
    in rank order; ranks are renumbered from 1 within the day.
    """
    chosen = [
        (item.tg_id, item)
        for item in ranked
        if item.tg_id is not None and item.tg_id not in offered
    ][:size]
    return [
        NewOffer(
            rank=rank,
            channel_id=tg_id,
            families=item.families,
            families_before=item.families_before,
            first_in_window=item.first_in_window,
        )
        for rank, (tg_id, item) in enumerate(chosen, start=1)
    ]


def offer_day(moment: datetime) -> date:
    """The day a selection computed at ``moment`` belongs to: its Moscow date."""
    return moment.astimezone(ZoneInfo(settings.watch_timezone)).date()


@dataclass(slots=True)
class OfferSummary:
    """What one run of the selection did."""

    day: date
    rising: int = 0
    offered: int = 0
    left: int = 0
    stored: bool = False
    # The day had its selection already, and nothing was written.
    already: bool = False
    offers: list[tuple[NewOffer, Rising]] = field(default_factory=list)

    def line(self) -> str:
        if self.already:
            return f"{self.day} has its selection; nothing written"
        line = (
            f"{self.day}: {self.rising} rising with a username, "
            f"{self.offered} offered, {self.left} left for a later day"
        )
        return line if self.stored else f"{line} (preview, nothing written)"

    def preview(self) -> list[str]:
        """The offers by name, for an operator at a terminal."""
        return [
            f"{offer.rank:>3}. @{item.username}  {item.title or '(no title)'}"
            f" — {offer.families} families in the window, "
            f"{offer.families_before} before"
            for offer, item in self.offers
        ]


async def run_offer(
    database: Database,
    *,
    now: datetime | None = None,
    preview: bool = False,
) -> OfferSummary:
    """Compute and store the day's selection, or say the day has one.

    With ``preview`` nothing is written, and the offers are kept on the
    summary to be printed.
    """
    moment = now or datetime.now(UTC)
    summary = OfferSummary(day=offer_day(moment))

    async with database.session() as session:
        rows = await load_rising_rows(session)
        offered = await offered_channels(session)
    ranked = rank_rising(
        rows,
        moment,
        window_days=settings.rising_window_days,
        min_families=settings.rising_min_families,
    ).ranked
    fresh = [item for item in ranked if item.tg_id not in offered]
    chosen = choose_offers(ranked, offered, settings.rising_offers_per_day)
    by_id = {item.tg_id: item for item in ranked}
    summary.rising = len(ranked)
    summary.offered = len(chosen)
    summary.left = len(fresh) - len(chosen)
    if preview:
        summary.offers = [(offer, by_id[offer.channel_id]) for offer in chosen]
        return summary

    async with database.session() as session:
        summary.stored = await store_day(
            session,
            day=summary.day,
            computed_at=moment,
            size=settings.rising_offers_per_day,
            offers=chosen,
        )
        await session.commit()
    summary.already = not summary.stored
    return summary


@dataclass(slots=True)
class ApplySummary:
    """What one run of applying decisions did."""

    applied: int = 0
    not_applied: int = 0

    def line(self) -> str:
        return (
            f"{self.applied} decision(s) applied, {self.not_applied} not "
            "applied: the channel was reviewed some other way first"
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
