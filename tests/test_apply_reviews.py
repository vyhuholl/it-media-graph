"""Applying the decisions pressed in the bot, against a `_test` database.

Ids and names are fake and spelled so.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from typer.testing import CliRunner

from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.db.backfill import seeds_awaiting_walk
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    RejectReason,
)
from itgraph.db.offers import NewOffer, record_decision, store_day
from itgraph.db.session import Database
from itgraph.derive.offers import apply_reviews

runner = CliRunner()

RISING = 2000000001
OTHER = 2000000002
DAY = date(2026, 11, 18)
AT = datetime(2026, 11, 18, 8, tzinfo=UTC)


async def offered(database: Database, *channels: int) -> list[int]:
    """Candidates offered today and sent; their offers' ids, in order."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(tg_id, f"fake_rising_{n}", None, False)
                for n, tg_id in enumerate(channels)
            ],
            discovered_via=DiscoverySource.FORWARD,
        )
        await store_day(
            session,
            day=DAY,
            computed_at=AT,
            size=5,
            offers=[
                NewOffer(rank, tg_id, 2, 0, AT)
                for rank, tg_id in enumerate(channels, start=1)
            ],
        )
        await session.execute(
            text("UPDATE tg_rising_offers SET delivered_at = now()")
        )
        ids = await session.scalars(
            text("SELECT id FROM tg_rising_offers ORDER BY rank")
        )
        return list(ids)


async def decide(database: Database, offer_id: int, **decision: Any) -> None:
    async with database.session() as session:
        assert await record_decision(session, offer_id, **decision)


async def channel(database: Database, tg_id: int) -> Channel:
    async with database.session() as session:
        found = await session.get(Channel, tg_id)
    assert found is not None
    return found


async def applied(database: Database) -> list[tuple[bool | None, bool]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT applied, applied_at IS NOT NULL "
                "FROM tg_rising_offers ORDER BY rank"
            )
        )
        return [(row[0], row[1]) for row in rows]


async def test_a_seed_decided_in_the_bot_lands_and_is_walked(
    database: Database,
) -> None:
    (offer,) = await offered(database, RISING)
    await decide(
        database, offer, status=ChannelStatus.SEED, kind=ChannelKind.PERSONAL
    )

    summary = await apply_reviews(database)

    assert (summary.applied, summary.not_applied) == (1, 0)
    landed = await channel(database, RISING)
    assert landed.status is ChannelStatus.SEED
    assert landed.kind is ChannelKind.PERSONAL
    assert landed.reviewed_at is not None
    assert await applied(database) == [(True, True)]
    async with database.session() as session:
        waiting = await seeds_awaiting_walk(session)
    assert [seed.tg_id for seed in waiting] == [RISING]


async def test_a_rejection_lands_with_its_reason(database: Database) -> None:
    (offer,) = await offered(database, RISING)
    await decide(
        database,
        offer,
        status=ChannelStatus.REJECTED,
        reason=RejectReason.CRYPTO,
    )

    await apply_reviews(database)

    landed = await channel(database, RISING)
    assert landed.status is ChannelStatus.REJECTED
    assert landed.reject_reason is RejectReason.CRYPTO


async def test_a_maybe_sets_no_kind(database: Database) -> None:
    (offer,) = await offered(database, RISING)
    await decide(database, offer, status=ChannelStatus.MAYBE)

    await apply_reviews(database)

    landed = await channel(database, RISING)
    assert landed.status is ChannelStatus.MAYBE
    assert landed.kind is None


async def test_a_channel_reviewed_meanwhile_keeps_its_review(
    database: Database,
) -> None:
    first, second = await offered(database, RISING, OTHER)
    await decide(
        database,
        first,
        status=ChannelStatus.REJECTED,
        reason=RejectReason.NOT_IT,
    )
    await decide(database, second, status=ChannelStatus.MAYBE)
    async with database.session() as session:
        await mark_channel(
            session, RISING, status=ChannelStatus.SEED, kind=ChannelKind.MEDIA
        )

    summary = await apply_reviews(database)

    assert (summary.applied, summary.not_applied) == (1, 1)
    kept = await channel(database, RISING)
    assert kept.status is ChannelStatus.SEED
    assert kept.kind is ChannelKind.MEDIA
    assert await applied(database) == [(False, True), (True, True)]


async def test_a_decision_is_applied_once(database: Database) -> None:
    (offer,) = await offered(database, RISING)
    await decide(database, offer, status=ChannelStatus.MAYBE)
    await apply_reviews(database)
    reviewed = (await channel(database, RISING)).reviewed_at

    again = await apply_reviews(database)

    assert (again.applied, again.not_applied) == (0, 0)
    assert (await channel(database, RISING)).reviewed_at == reviewed


async def test_an_undecided_offer_is_left_alone(database: Database) -> None:
    await offered(database, RISING)

    summary = await apply_reviews(database)

    assert (summary.applied, summary.not_applied) == (0, 0)
    assert (await channel(database, RISING)).status is ChannelStatus.CANDIDATE
    assert await applied(database) == [(None, False)]


def test_the_command_prints_counts(
    database_url: str, monkeypatch: Any
) -> None:
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))

    result = runner.invoke(app, ["apply-reviews"])

    assert result.exit_code == 0, result.output
    assert "0 decision(s) applied, 0 not applied" in result.output
