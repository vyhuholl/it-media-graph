"""Telegram's rising offers: a day's selection, its delivery, its decisions.

A day is stored whole, once. An offer holds the ranking's numbers as
they stood, the bot's delivery, the decision pressed and whether the
detection pass could apply it — never a name or a title, which the bot
reads from the inventory at sending, and never who pressed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.channels import families_subquery, mark_channel
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    Edge,
    RejectReason,
    RisingOffer,
    RisingOfferDay,
)

__all__ = [
    "DAY_KEY",
    "Decided",
    "NewOffer",
    "OfferState",
    "PendingOffer",
    "SeedPost",
    "apply_decision",
    "card_posts",
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
DAY_KEY = "pk_tg_rising_offer_days"


@dataclass(frozen=True, slots=True)
class NewOffer:
    """One channel of a day's selection, as the ranking gave it."""

    rank: int
    channel_id: int
    families: int
    families_before: int
    first_in_window: datetime | None


async def offered_channels(session: AsyncSession) -> set[int]:
    """Every channel ever offered, whatever came of it."""
    return set(await session.scalars(select(RisingOffer.channel_id)))


async def day_has_selection(session: AsyncSession, day: date) -> bool:
    """Whether this day's selection is stored."""
    return bool(
        await session.scalar(select(exists().where(RisingOfferDay.day == day)))
    )


async def store_day(
    session: AsyncSession,
    *,
    day: date,
    computed_at: datetime,
    size: int,
    offers: Sequence[NewOffer],
) -> bool:
    """Write a day's selection whole, in the caller's transaction.

    ``False``, writing nothing, when the day already has its selection.
    Two runs at once — one by hand beside the scheduled one — end the
    same way: the second meets the day's key inside a savepoint, which is
    rolled back. Any other refusal is raised.
    """
    if await day_has_selection(session, day):
        return False
    try:
        async with session.begin_nested():
            session.add(
                RisingOfferDay(day=day, computed_at=computed_at, size=size)
            )
            await session.flush()
            session.add_all(
                RisingOffer(
                    day=day,
                    channel_id=offer.channel_id,
                    rank=offer.rank,
                    families=offer.families,
                    families_before=offer.families_before,
                    first_in_window=offer.first_in_window,
                    offered_at=computed_at,
                )
                for offer in offers
            )
            await session.flush()
    except IntegrityError as exc:
        if DAY_KEY in str(exc.orig):
            return False
        raise
    return True


@dataclass(frozen=True, slots=True)
class PendingOffer:
    """An offer to send, with its channel as it stands now."""

    id: int
    channel_id: int
    rank: int
    families: int
    families_before: int
    first_in_window: datetime | None
    offered_at: datetime
    status: ChannelStatus
    username: str | None
    title: str | None


async def claim_offers(session: AsyncSession) -> list[PendingOffer]:
    """The offers not yet delivered, oldest day first, then by rank."""
    rows = await session.execute(
        select(
            RisingOffer.id,
            RisingOffer.channel_id,
            RisingOffer.rank,
            RisingOffer.families,
            RisingOffer.families_before,
            RisingOffer.first_in_window,
            RisingOffer.offered_at,
            Channel.status,
            Channel.username,
            Channel.title,
        )
        .join(Channel, Channel.tg_id == RisingOffer.channel_id)
        .where(RisingOffer.delivered_at.is_(None))
        .order_by(RisingOffer.day, RisingOffer.rank)
    )
    return [PendingOffer(*row) for row in rows.tuples()]


async def mark_offer_delivered(
    session: AsyncSession, offer_id: int, *, at: datetime
) -> None:
    """Record that the card went out, or that it was closed unsent."""
    await session.execute(
        update(RisingOffer)
        .where(RisingOffer.id == offer_id)
        .values(delivered_at=at, last_error=None)
    )


async def mark_offer_failed(
    session: AsyncSession, offer_id: int, *, error: str
) -> None:
    """Record a failed send, leaving the offer undelivered."""
    await session.execute(
        update(RisingOffer)
        .where(RisingOffer.id == offer_id)
        .values(attempts=RisingOffer.attempts + 1, last_error=error[:500])
    )


@dataclass(frozen=True, slots=True)
class SeedPost:
    """A seed's post that referred to the offered channel."""

    username: str | None
    title: str | None
    msg_id: int


async def card_posts(
    session: AsyncSession,
    channel_id: int,
    *,
    since: datetime,
    until: datetime,
    limit: int = 3,
) -> list[SeedPost]:
    """The seed posts a card links: the newest of each referring family.

    From seeds that are not chats, between ``since`` and ``until``, the
    channel's own family left out as the ranking leaves it; the families
    whose reference is newest first, ``limit`` of them.
    """
    families = families_subquery()
    own = await session.scalar(
        select(families.c.family_key).where(
            families.c.channel_id == channel_id
        )
    )
    seed_family = func.coalesce(families.c.family_key, Channel.tg_id)
    newest = (
        select(
            seed_family.label("family"),
            Channel.username,
            Channel.title,
            Edge.msg_id,
            Edge.published_at,
        )
        .join(Channel, Channel.tg_id == Edge.src_channel_id)
        .outerjoin(families, families.c.channel_id == Channel.tg_id)
        .where(
            Edge.dst_channel_id == channel_id,
            Edge.published_at >= since,
            Edge.published_at <= until,
            Channel.status == ChannelStatus.SEED,
            Channel.is_chat.is_(False),
            seed_family.is_distinct_from(own),
        )
        .distinct(seed_family)
        .order_by(seed_family, Edge.published_at.desc(), Edge.msg_id.desc())
        .subquery()
    )
    rows = await session.execute(
        select(newest.c.username, newest.c.title, newest.c.msg_id)
        .order_by(newest.c.published_at.desc(), newest.c.family)
        .limit(limit)
    )
    return [SeedPost(*row) for row in rows.tuples()]


@dataclass(frozen=True, slots=True)
class OfferState:
    """Where a pressed offer stands: whether it is one the bot sent, and
    the decision on it, if any."""

    sent: bool
    decision: ChannelStatus | None = None
    kind: ChannelKind | None = None
    reason: RejectReason | None = None


async def offer_state(
    session: AsyncSession, offer_id: int
) -> OfferState | None:
    """The offer's state, or ``None`` for an id this database lacks."""
    row = (
        await session.execute(
            select(
                RisingOffer.delivered_at.is_not(None),
                RisingOffer.decision,
                RisingOffer.kind,
                RisingOffer.reject_reason,
            ).where(RisingOffer.id == offer_id)
        )
    ).one_or_none()
    return OfferState(*row) if row is not None else None


async def record_decision(
    session: AsyncSession,
    offer_id: int,
    *,
    status: ChannelStatus,
    kind: ChannelKind | None = None,
    reason: RejectReason | None = None,
) -> bool:
    """Record a decision on a sent offer that has none; whether it did.

    One statement, so that of two presses at once one decision is
    recorded and the other finds it taken. Nothing about who pressed.
    """
    recorded = await session.execute(
        update(RisingOffer)
        .where(
            RisingOffer.id == offer_id,
            RisingOffer.decision.is_(None),
            RisingOffer.delivered_at.is_not(None),
        )
        .values(
            decision=status,
            kind=kind,
            reject_reason=reason,
            decided_at=func.now(),
        )
        .returning(RisingOffer.id)
    )
    return recorded.first() is not None


# What a channel may be for a decision taken in the bot to land on it:
# not reviewed some other way since.
STILL_OPEN = (ChannelStatus.CANDIDATE, ChannelStatus.MAYBE)


@dataclass(frozen=True, slots=True)
class Decided:
    """A decision taken in the bot and not yet applied."""

    id: int
    channel_id: int
    decision: ChannelStatus
    kind: ChannelKind | None
    reason: RejectReason | None


async def decided_unapplied(session: AsyncSession) -> list[Decided]:
    """Every decision not yet applied, the oldest first."""
    rows = await session.execute(
        select(
            RisingOffer.id,
            RisingOffer.channel_id,
            RisingOffer.decision,
            RisingOffer.kind,
            RisingOffer.reject_reason,
        )
        .where(
            RisingOffer.decided_at.is_not(None),
            RisingOffer.applied_at.is_(None),
        )
        .order_by(RisingOffer.decided_at, RisingOffer.id)
    )
    # The check on the table holds a decision wherever `decided_at` is
    # set; the filter only says so to the type checker.
    return [
        Decided(offer_id, channel_id, decision, kind, reason)
        for offer_id, channel_id, decision, kind, reason in rows.tuples()
        if decision is not None
    ]


async def apply_decision(session: AsyncSession, decided: Decided) -> bool:
    """Apply one decision to its channel's review, in the caller's
    transaction; whether it landed.

    Under the channel's row lock, so a review by `itgraph mark` at the
    same moment is either before it or after it. It lands only on a
    channel still a candidate or a maybe, through `mark_channel` as
    `itgraph mark` would; otherwise it is recorded as not applied. Either
    way the offer is applied once.
    """
    channel = await session.get(
        Channel,
        decided.channel_id,
        with_for_update=True,
        populate_existing=True,
    )
    landed = channel is not None and channel.status in STILL_OPEN
    if landed:
        await mark_channel(
            session,
            decided.channel_id,
            status=decided.decision,
            kind=decided.kind,
            reject_reason=decided.reason,
        )
    await session.execute(
        update(RisingOffer)
        .where(RisingOffer.id == decided.id)
        .values(applied_at=func.now(), applied=landed)
    )
    return landed
