"""YouTube's rising offers: a day's selection, its delivery, its
decisions.

Telegram's tables with a place and a language: which of the two rankings
offered a channel and what it filled, the discovery source of the record
the selection created, and the topic its card goes to. A seed's decision
carries a language, as ``itgraph yt mark`` records one. As on Telegram:
a channel is offered once, ever; an offer holds no title and no handle —
the bot reads the channel at sending — and nobody who pressed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import String, Text, and_, case, exists, func, select, update
from sqlalchemy import cast as sa_cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from itgraph.db.models import (
    ChannelStatus,
    YtAlertRoute,
    YtChannel,
    YtChannelKind,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
    YtRejectReason,
    YtRisingOffer,
    YtRisingOfferDay,
    YtRisingRanking,
)
from itgraph.db.yt_channels import Review, create_channels, mark_channel
from itgraph.db.yt_chart import charts_of_the_day
from itgraph.db.yt_rising import chart_pairs

__all__ = [
    "DAY_KEY",
    "STILL_OPEN",
    "Decided",
    "NewOffer",
    "OfferState",
    "PendingOffer",
    "apply_decision",
    "charting_video",
    "claim_offers",
    "day_has_selection",
    "decided_unapplied",
    "mark_offer_delivered",
    "mark_offer_failed",
    "offer_state",
    "offered_channels",
    "record_decision",
    "store_day",
]

# The primary key on a day, as the revision names it.
DAY_KEY = "pk_yt_rising_offer_days"


@dataclass(frozen=True, slots=True)
class NewOffer:
    """One channel of a day's selection, as it is stored."""

    ranking: YtRisingRanking
    channel_id: str
    discovered_via: YtDiscoverySource
    topic: YtAlertRoute
    # A chart offer's place in its region's reading.
    region: str | None = None
    position: int | None = None
    # A link offer's evidence, as the ranking counted it.
    families: int | None = None
    families_before: int | None = None
    first_in_window: datetime | None = None


async def offered_channels(session: AsyncSession) -> set[str]:
    """Every channel ever offered, whatever came of it."""
    return set(await session.scalars(select(YtRisingOffer.channel_id)))


async def day_has_selection(session: AsyncSession, day: date) -> bool:
    """Whether this day's selection is stored."""
    return bool(
        await session.scalar(
            select(exists().where(YtRisingOfferDay.day == day))
        )
    )


async def store_day(
    session: AsyncSession,
    *,
    day: date,
    computed_at: datetime,
    size_chart: int,
    size_links: int,
    offers: Sequence[NewOffer],
) -> Sequence[NewOffer] | None:
    """Write a day's selection whole, in the caller's transaction.

    ``None``, writing nothing, when the day already has its selection —
    two runs at once end the same way, the second meeting the day's key
    inside a savepoint that is rolled back. Each offered channel gains a
    ``candidate`` record if the inventory holds none, and a channel that
    gained one another way — before the run, or while it ranked — is not
    offered: only a channel whose record this transaction created is,
    which is the same uniqueness that stops a re-offer.
    """
    if await day_has_selection(session, day):
        return None
    by_source: dict[YtDiscoverySource, list[str]] = {}
    for offer in offers:
        by_source.setdefault(offer.discovered_via, []).append(offer.channel_id)
    created: set[str] = set()
    for source, channel_ids in by_source.items():
        created.update(
            await create_channels(session, channel_ids, discovered_via=source)
        )
    stored = [offer for offer in offers if offer.channel_id in created]
    try:
        async with session.begin_nested():
            session.add(
                YtRisingOfferDay(
                    day=day,
                    computed_at=computed_at,
                    size_chart=size_chart,
                    size_links=size_links,
                )
            )
            await session.flush()
            session.add_all(
                YtRisingOffer(
                    day=day,
                    channel_id=offer.channel_id,
                    ranking=offer.ranking,
                    region=offer.region,
                    position=offer.position,
                    families=offer.families,
                    families_before=offer.families_before,
                    first_in_window=offer.first_in_window,
                    discovered_via=offer.discovered_via,
                    topic=offer.topic,
                    offered_at=computed_at,
                )
                for offer in stored
            )
            await session.flush()
    except IntegrityError as exc:
        if DAY_KEY in str(exc.orig):
            return None
        raise
    return stored


@dataclass(frozen=True, slots=True)
class PendingOffer:
    """An offer to send, with its channel as it stands now.

    ``payload`` is the channel's stored payload, or ``None`` before the
    refresh has fetched one: a day-one card links by id and marks the
    language unknown.
    """

    id: int
    channel_id: str
    ranking: YtRisingRanking
    region: str | None
    position: int | None
    families: int | None
    families_before: int | None
    first_in_window: datetime | None
    offered_at: datetime
    status: ChannelStatus
    payload: dict[str, object] | None


# A link offer's payload may have fetched a language since it was
# offered; that language moves the card, which is why the claim routes
# by it rather than by the stored topic alone.
def _default_language() -> ColumnElement[str]:
    return sa_cast(
        YtRawChannel.payload["snippet"]["defaultLanguage"].astext, Text
    )


def _routed_topic() -> ColumnElement[str]:
    """The topic an offer's card goes to as of now: its stored one,
    unless a link offer's stored payload names a language of a topic."""
    return case(
        (
            and_(
                YtRisingOffer.ranking == YtRisingRanking.LINK,
                _default_language().in_(("en", "ru")),
            ),
            _default_language(),
        ),
        else_=YtRisingOffer.topic.cast(String),
    )


async def claim_offers(
    session: AsyncSession, route: YtAlertRoute
) -> list[PendingOffer]:
    """This topic's offers not yet delivered, oldest day first, then in
    the order the day chose them."""
    rows = await session.execute(
        select(
            YtRisingOffer.id,
            YtRisingOffer.channel_id,
            YtRisingOffer.ranking,
            YtRisingOffer.region,
            YtRisingOffer.position,
            YtRisingOffer.families,
            YtRisingOffer.families_before,
            YtRisingOffer.first_in_window,
            YtRisingOffer.offered_at,
            YtChannel.status,
            YtRawChannel.payload,
        )
        .join(YtChannel, YtChannel.channel_id == YtRisingOffer.channel_id)
        .outerjoin(
            YtRawChannel, YtRawChannel.channel_id == YtRisingOffer.channel_id
        )
        .where(YtRisingOffer.delivered_at.is_(None))
        .where(_routed_topic() == route.value)
        .order_by(YtRisingOffer.day, YtRisingOffer.id)
    )
    return [PendingOffer(*row) for row in rows.tuples()]


async def mark_offer_delivered(
    session: AsyncSession, offer_id: int, *, at: datetime
) -> None:
    """Record that the card went out, or that it was closed unsent."""
    await session.execute(
        update(YtRisingOffer)
        .where(YtRisingOffer.id == offer_id)
        .values(delivered_at=at, last_error=None)
    )


async def mark_offer_failed(
    session: AsyncSession, offer_id: int, *, error: str
) -> None:
    """Record a failed send, leaving the offer undelivered."""
    await session.execute(
        update(YtRisingOffer)
        .where(YtRisingOffer.id == offer_id)
        .values(attempts=YtRisingOffer.attempts + 1, last_error=error[:500])
    )


async def charting_video(
    session: AsyncSession,
    channel_id: str,
    *,
    region: str,
    day_start: datetime,
) -> str | None:
    """The video that charted the channel, read from the stored reading.

    The channel's first in the day's reading of its region — the chart's
    own order — or ``None`` if no reading of that day holds it.
    """
    charts = await charts_of_the_day(session, (region,), day_start=day_start)
    for video_id, charted in chart_pairs(charts.get(region, {})):
        if charted == channel_id:
            return video_id
    return None


@dataclass(frozen=True, slots=True)
class OfferState:
    """Where a pressed offer stands: whether it is one the bot sent, the
    decision on it if any, and the language its stored payload tells."""

    sent: bool
    decision: ChannelStatus | None = None
    kind: YtChannelKind | None = None
    language: YtLang | None = None
    reason: YtRejectReason | None = None
    # The payload's `defaultLanguage` when it is one of the two a topic
    # takes; it fills a seed's language silently.
    known_language: YtLang | None = None


async def offer_state(
    session: AsyncSession, offer_id: int
) -> OfferState | None:
    """The offer's state, or ``None`` for an id this database lacks."""
    row = (
        await session.execute(
            select(
                YtRisingOffer.delivered_at.is_not(None),
                YtRisingOffer.decision,
                YtRisingOffer.kind,
                YtRisingOffer.language,
                YtRisingOffer.reject_reason,
                _default_language(),
            )
            .outerjoin(
                YtRawChannel,
                YtRawChannel.channel_id == YtRisingOffer.channel_id,
            )
            .where(YtRisingOffer.id == offer_id)
        )
    ).one_or_none()
    if row is None:
        return None
    sent, decision, kind, language, reason, default_language = row.tuple()
    known = (
        YtLang(default_language) if default_language in ("en", "ru") else None
    )
    return OfferState(
        sent,
        decision,
        kind=kind,
        language=language,
        reason=reason,
        known_language=known,
    )


async def record_decision(
    session: AsyncSession,
    offer_id: int,
    *,
    status: ChannelStatus,
    kind: YtChannelKind | None = None,
    language: YtLang | None = None,
    reason: YtRejectReason | None = None,
) -> bool:
    """Record a decision on a sent offer that has none; whether it did.

    One statement, so that of two presses at once one decision is
    recorded and the other finds it taken. Nothing about who pressed.
    """
    recorded = await session.execute(
        update(YtRisingOffer)
        .where(
            YtRisingOffer.id == offer_id,
            YtRisingOffer.decision.is_(None),
            YtRisingOffer.delivered_at.is_not(None),
        )
        .values(
            decision=status,
            kind=kind,
            language=language,
            reject_reason=reason,
            decided_at=func.now(),
        )
        .returning(YtRisingOffer.id)
    )
    return recorded.first() is not None


# What a channel may be for a decision taken in the bot to land on it:
# not reviewed some other way since.
STILL_OPEN = (ChannelStatus.CANDIDATE, ChannelStatus.MAYBE)


@dataclass(frozen=True, slots=True)
class Decided:
    """A decision taken in the bot and not yet applied."""

    id: int
    channel_id: str
    decision: ChannelStatus
    kind: YtChannelKind | None
    language: YtLang | None
    reason: YtRejectReason | None


async def decided_unapplied(session: AsyncSession) -> list[Decided]:
    """Every decision not yet applied, the oldest first."""
    rows = await session.execute(
        select(
            YtRisingOffer.id,
            YtRisingOffer.channel_id,
            YtRisingOffer.decision,
            YtRisingOffer.kind,
            YtRisingOffer.language,
            YtRisingOffer.reject_reason,
        )
        .where(
            YtRisingOffer.decided_at.is_not(None),
            YtRisingOffer.applied_at.is_(None),
        )
        .order_by(YtRisingOffer.decided_at, YtRisingOffer.id)
    )
    # The check on the table holds a decision wherever `decided_at` is
    # set; the filter only says so to the type checker.
    return [
        Decided(offer_id, channel_id, decision, kind, language, reason)
        for offer_id, channel_id, decision, kind, language, reason in (
            rows.tuples()
        )
        if decision is not None
    ]


async def apply_decision(session: AsyncSession, decided: Decided) -> bool:
    """Apply one decision to its channel's review, in the caller's
    transaction; whether it landed.

    Under the channel's row lock, so a review by `itgraph yt mark` at
    the same moment is either before it or after it. It lands only on a
    channel still a candidate or a maybe, through ``mark_channel`` as
    `itgraph yt mark` would — which sets ``reviewed_at``, and so orders
    the backfill walk of a new seed; otherwise it is recorded as not
    applied. Either way the offer is applied once.
    """
    channel = await session.get(
        YtChannel,
        decided.channel_id,
        with_for_update=True,
        populate_existing=True,
    )
    landed = channel is not None and channel.status in STILL_OPEN
    if landed:
        await mark_channel(
            session,
            decided.channel_id,
            Review(
                status=decided.decision,
                kind=decided.kind,
                lang=decided.language,
                reason=decided.reason,
            ),
        )
    await session.execute(
        update(YtRisingOffer)
        .where(YtRisingOffer.id == decided.id)
        .values(applied_at=func.now(), applied=landed)
    )
    return landed
