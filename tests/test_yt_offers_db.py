"""Storing a day's selection of YouTube's rising channels, against a
`_test` database, and applying what was pressed on its cards.

Ids are fake and spelled so.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select

from itgraph.db.models import (
    ChannelStatus,
    YtAlertRoute,
    YtChannel,
    YtChannelKind,
    YtDiscoverySource,
    YtLang,
    YtRejectReason,
    YtRisingOffer,
)
from itgraph.db.session import Database
from itgraph.db.yt_backfill import channels_to_walk
from itgraph.db.yt_channels import Review, create_channels, mark_channel
from itgraph.db.yt_offers import (
    NewOffer,
    decided_unapplied,
    mark_offer_delivered,
    record_decision,
    store_day,
)
from itgraph.derive.yt_offers import apply_reviews

DAY = date(2026, 11, 19)
AT = datetime(2026, 11, 19, 8, tzinfo=UTC)

LINKED = "UCfakeRisingLinks0000001"
ADDED = "UCfakeRisingAdded0000001"


def link_offer(channel: str) -> NewOffer:
    return NewOffer(
        channel_id=channel,
        discovered_via=YtDiscoverySource.TELEGRAM,
        topic=YtAlertRoute.EN,
        families=2,
        families_before=1,
        first_in_window=AT,
    )


async def store(database: Database, *offers: NewOffer) -> None:
    """Store a day's selection, asserting the day was new and every
    offer landed unless its channel held a record already."""
    async with database.session() as session:
        stored = await store_day(
            session,
            day=DAY,
            computed_at=AT,
            size_links=3,
            offers=list(offers),
        )
        await session.commit()
    assert stored is not None


async def test_a_channel_that_gained_a_record_is_not_offered(
    database: Database,
) -> None:
    """However it gained it — the record wins, and no second one is
    made."""
    async with database.session() as session:
        await create_channels(
            session, [ADDED], discovered_via=YtDiscoverySource.MANUAL
        )
        await session.commit()

    await store(database, link_offer(LINKED), link_offer(ADDED))

    async with database.session() as session:
        rows = await session.execute(
            select(YtRisingOffer.channel_id, YtRisingOffer.discovered_via)
        )
        offers = {(row[0], row[1]) for row in rows.tuples()}
        record = await session.get(YtChannel, ADDED)
    assert offers == {(LINKED, YtDiscoverySource.TELEGRAM)}
    assert record is not None
    # First discovery wins: the manual record keeps what it came with.
    assert record.discovered_via is YtDiscoverySource.MANUAL


# --- the decisions, and applying them --------------------------------------


async def offered_and_sent(database: Database, *channels: str) -> list[int]:
    await store(database, *(link_offer(channel) for channel in channels))
    async with database.session() as session:
        ids = list(await session.scalars(select(YtRisingOffer.id)))
        for offer_id in ids:
            await mark_offer_delivered(session, offer_id, at=AT)
        return ids


async def test_a_seed_decided_in_the_bot_lands_and_is_walked(
    database: Database,
) -> None:
    (offer,) = await offered_and_sent(database, LINKED)
    async with database.session() as session:
        assert await record_decision(
            session,
            offer,
            status=ChannelStatus.SEED,
            kind=YtChannelKind.MEDIA,
            language=YtLang.RU,
        )

    summary = await apply_reviews(database)

    assert (summary.applied, summary.not_applied) == (1, 0)
    async with database.session() as session:
        channel = await session.get(YtChannel, LINKED)
        assert channel is not None
        assert channel.status is ChannelStatus.SEED
        assert channel.kind is YtChannelKind.MEDIA
        assert channel.lang is YtLang.RU
        assert channel.reviewed_at is not None
        # `reviewed_at` is what orders the backfill walk of a new seed.
        assert [
            walk.channel_id for walk in await channels_to_walk(session)
        ] == ([LINKED])


async def test_a_channel_reviewed_meanwhile_keeps_its_review(
    database: Database,
) -> None:
    (offer,) = await offered_and_sent(database, LINKED)
    async with database.session() as session:
        assert await record_decision(
            session,
            offer,
            status=ChannelStatus.REJECTED,
            reason=YtRejectReason.CRYPTO,
        )
        await mark_channel(
            session,
            LINKED,
            Review(
                status=ChannelStatus.SEED,
                kind=YtChannelKind.COMPANY,
                lang=YtLang.EN,
            ),
        )

    summary = await apply_reviews(database)

    assert (summary.applied, summary.not_applied) == (0, 1)
    async with database.session() as session:
        channel = await session.get(YtChannel, LINKED)
        assert channel is not None
        assert channel.status is ChannelStatus.SEED
        assert channel.kind is YtChannelKind.COMPANY


async def test_a_decision_is_applied_once(database: Database) -> None:
    (offer,) = await offered_and_sent(database, LINKED)
    async with database.session() as session:
        assert await record_decision(
            session, offer, status=ChannelStatus.MAYBE
        )
    await apply_reviews(database)

    again = await apply_reviews(database)

    assert (again.applied, again.not_applied) == (0, 0)


async def test_a_decision_on_an_undelivered_offer_is_refused(
    database: Database,
) -> None:
    """The card must have gone out before a press can decide."""
    await store(database, link_offer(LINKED))
    async with database.session() as session:
        rows = list(await session.scalars(select(YtRisingOffer.id)))
    assert rows
    async with database.session() as session:
        assert not await record_decision(
            session, rows[0], status=ChannelStatus.MAYBE
        )
        assert not await decided_unapplied(session)
