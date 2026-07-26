"""Resolving referenced channels — the one pass that talks to Telegram.

Telethon is a fake and sleeps are recorded, not taken. What is under test
is the queueing and the bookkeeping: which references are asked for, which
are skipped, and what each answer does to the inventory.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fakes import FakeTelegramClient, tl_channel, tl_user
from sqlalchemy import select

from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.edges import add_pending_mentions, create_discovered_channels
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    Edge,
    PendingMention,
)
from itgraph.db.raw import store_messages
from itgraph.db.session import Database
from itgraph.derive.edges import derive_graph
from itgraph.tg import backfill as backfill_module
from itgraph.tg import pacing as pacing_module
from itgraph.tg.resolve import resolve_inventory

SRC = 1000000001
FWD_ID = 2000000001  # discovered by forward, resolved by id
FAIL_ID = 2000000002  # an id the session cannot place yet
NEWCOMER = 3000000001  # what @newcomer resolves to


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleeps instead of taking them, on both paced paths.

    Pacing sleeps in the resolver, FloodWait sleeps in the collector's
    ``waiting_out_floods`` the resolver reuses — patch both.
    """
    taken: list[float] = []

    async def sleep(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(pacing_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)
    return taken


@pytest.fixture
def no_long_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch off the rare long pause, so a band assertion is decidable."""
    from itgraph.config import settings

    monkeypatch.setattr(settings, "pacing_long_pause_chance", 0.0)


async def add_forward_channel(database: Database, tg_id: int) -> None:
    async with database.session() as session:
        await create_discovered_channels(
            session, tg_ids=[tg_id], discovered_via=DiscoverySource.FORWARD
        )


async def add_pending(database: Database, *usernames: str) -> None:
    async with database.session() as session:
        await add_pending_mentions(session, usernames)


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """One resolved seed channel; references are added per test."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(SRC, "src_channel", "Source", False)],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await mark_channel(
            session,
            SRC,
            status=ChannelStatus.SEED,
            kind=ChannelKind.PERSONAL,
        )
    # The migration stamps existing rows resolved; the fixture creates
    # them through the ORM, so mark the seed resolved by hand.
    async with database.session() as session:
        seed = await session.get(Channel, SRC)
        assert seed is not None
        seed.resolved_at = datetime(2026, 1, 1, tzinfo=UTC)
    yield database


async def channel(database: Database, tg_id: int) -> Channel | None:
    async with database.session() as session:
        return await session.get(Channel, tg_id)


async def pending_row(
    database: Database, username: str
) -> PendingMention | None:
    async with database.session() as session:
        return await session.get(PendingMention, username)


# --- resolving a channel by id ---------------------------------------


async def test_a_cached_id_resolves_to_a_channel(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FWD_ID)
    client = FakeTelegramClient(
        entities_by_id={
            FWD_ID: tl_channel(FWD_ID, username="forwarded", title="Fwd")
        }
    )

    summary = await resolve_inventory(client, inventory, delay=0)

    assert FWD_ID in client.resolved
    assert summary.resolved == 1
    resolved = await channel(inventory, FWD_ID)
    assert resolved is not None
    assert resolved.username == "forwarded"
    assert resolved.title == "Fwd"
    assert resolved.resolved_at is not None


async def test_an_uncached_id_is_recorded_as_a_failed_attempt(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FAIL_ID)
    # entities_by_id is empty: the session cannot place this id.
    client = FakeTelegramClient()

    summary = await resolve_inventory(client, inventory, delay=0)

    assert summary.failed == 1
    failed = await channel(inventory, FAIL_ID)
    assert failed is not None
    assert failed.resolved_at is None
    assert failed.resolve_attempts == 1
    assert failed.resolve_last_error is not None


async def test_a_supergroup_is_stored_as_a_chat(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FWD_ID)
    client = FakeTelegramClient(
        entities_by_id={
            FWD_ID: tl_channel(FWD_ID, username="grp", megagroup=True)
        }
    )

    await resolve_inventory(client, inventory, delay=0)

    resolved = await channel(inventory, FWD_ID)
    assert resolved is not None
    assert resolved.is_chat is True


# --- resolving a pending username ------------------------------------


async def test_a_pending_username_becomes_a_channel(
    inventory: Database, slept: list[float]
) -> None:
    await add_pending(inventory, "newcomer")
    client = FakeTelegramClient(
        entities={"newcomer": tl_channel(NEWCOMER, username="newcomer")}
    )

    summary = await resolve_inventory(client, inventory, delay=0)

    assert summary.discovered == 1
    created = await channel(inventory, NEWCOMER)
    assert created is not None
    assert created.username == "newcomer"
    assert created.discovered_via is DiscoverySource.MENTION
    assert created.resolved_at is not None
    # The pending row is gone once it has become a channel.
    assert await pending_row(inventory, "newcomer") is None


async def test_a_username_that_is_a_person_is_discarded(
    inventory: Database, slept: list[float]
) -> None:
    await add_pending(inventory, "someone")
    client = FakeTelegramClient(entities={"someone": tl_user(999)})

    summary = await resolve_inventory(client, inventory, delay=0)

    assert summary.not_channels == 1
    assert summary.discovered == 0
    # No channel row for the person, and the pending row is kept but
    # marked so a routine re-run leaves it alone.
    assert await channel(inventory, 999) is None
    row = await pending_row(inventory, "someone")
    assert row is not None
    assert row.attempts == 1
    assert row.last_error is not None


# --- pacing and rate limits ------------------------------------------


async def test_a_flood_wait_is_waited_out_then_the_request_retried(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FWD_ID)
    client = FakeTelegramClient(
        entities_by_id={FWD_ID: tl_channel(FWD_ID, username="forwarded")},
        resolve_floods={FWD_ID: 33},
    )

    summary = await resolve_inventory(client, inventory, delay=0)

    assert 33 in slept
    assert summary.resolved == 1
    assert summary.failed == 0


async def test_requests_are_paced_and_bounded_by_a_limit(
    inventory: Database, slept: list[float], no_long_pauses: None
) -> None:
    await add_forward_channel(inventory, FWD_ID)
    await add_forward_channel(inventory, FAIL_ID)
    client = FakeTelegramClient(
        entities_by_id={
            FWD_ID: tl_channel(FWD_ID, username="a"),
            FAIL_ID: tl_channel(FAIL_ID, username="b"),
        }
    )

    summary = await resolve_inventory(client, inventory, delay=2.5, limit=1)

    # Only one of the two was asked for; the limit stopped the run.
    assert len(client.resolved) == 1
    assert summary.resolved == 1
    # And that one request was preceded by a pause from the band around
    # the configured delay — drawn, not the delay repeated.
    assert len(slept) == 1
    assert 1.25 <= slept[0] <= 3.75


async def test_a_resolved_channel_is_not_revisited(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FWD_ID)
    client = FakeTelegramClient(
        entities_by_id={FWD_ID: tl_channel(FWD_ID, username="forwarded")}
    )
    await resolve_inventory(client, inventory, delay=0)

    again = FakeTelegramClient(
        entities_by_id={FWD_ID: tl_channel(FWD_ID, username="forwarded")}
    )
    summary = await resolve_inventory(again, inventory, delay=0)

    assert again.resolved == []
    assert summary.resolved == 0


# --- retrying failures -----------------------------------------------


async def test_a_failed_id_is_skipped_until_retry_failed(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FAIL_ID)
    # First run: the id cannot be placed, so it fails and counts an attempt.
    await resolve_inventory(FakeTelegramClient(), inventory, delay=0)

    # A routine second run does not ask about it again.
    routine = FakeTelegramClient(
        entities_by_id={FAIL_ID: tl_channel(FAIL_ID, username="late")}
    )
    routine_summary = await resolve_inventory(routine, inventory, delay=0)
    assert routine.resolved == []
    assert routine_summary.resolved == 0

    # With --retry-failed, and now that the session can place the id, it
    # resolves: a cache miss is provisional, not final.
    retry = FakeTelegramClient(
        entities_by_id={FAIL_ID: tl_channel(FAIL_ID, username="late")}
    )
    retry_summary = await resolve_inventory(
        retry, inventory, delay=0, retry_failed=True
    )
    assert FAIL_ID in retry.resolved
    assert retry_summary.resolved == 1
    resolved = await channel(inventory, FAIL_ID)
    assert resolved is not None
    assert resolved.username == "late"


# --- the two-cycle workflow ------------------------------------------


async def test_derive_resolve_derive_completes_a_mention_edge(
    inventory: Database, slept: list[float]
) -> None:
    """A mention edge lags one cycle: derive, resolve, derive.

    The first derivation cannot write the edge — it has only a username.
    Resolution turns the username into a channel, and the second
    derivation, now able to look it up, writes the edge.
    """
    async with inventory.session() as session:
        await store_messages(
            session,
            channel_id=SRC,
            payloads={
                7: {
                    "_": "Message",
                    "id": 7,
                    "date": "2026-03-14T09:00:00+00:00",
                    "message": "@newcomer",
                    "entities": [
                        {
                            "_": "MessageEntityMention",
                            "offset": 0,
                            "length": 9,
                        }
                    ],
                }
            },
        )

    first = await derive_graph(inventory)
    assert first.edges == 0
    assert first.pending == 1

    client = FakeTelegramClient(
        entities={"newcomer": tl_channel(NEWCOMER, username="newcomer")}
    )
    await resolve_inventory(client, inventory, delay=0)

    second = await derive_graph(inventory)
    assert second.edges == 1

    async with inventory.session() as session:
        rows = (await session.scalars(select(Edge))).all()
    assert len(rows) == 1
    assert rows[0].src_channel_id == SRC
    assert rows[0].dst_channel_id == NEWCOMER
    assert rows[0].kind.value == "mention"


async def test_a_long_flood_wait_halts_resolution(
    inventory: Database, slept: list[float]
) -> None:
    """A day-long wait stops the run instead of being slept through.

    Both queues with it: the halt is about the account, not about the
    reference that happened to be next.
    """
    await add_forward_channel(inventory, FWD_ID)
    await add_forward_channel(inventory, FAIL_ID)
    await add_pending(inventory, "newcomer")
    client = FakeTelegramClient(
        entities_by_id={
            FWD_ID: tl_channel(FWD_ID, username="a"),
            FAIL_ID: tl_channel(FAIL_ID, username="b"),
        },
        entities={"newcomer": tl_channel(NEWCOMER, username="newcomer")},
        resolve_floods={FAIL_ID: 86400},
    )

    summary = await resolve_inventory(client, inventory, delay=0)

    assert summary.halt is not None
    assert summary.halt.seconds == 86400
    assert 86400 not in slept
    # The first reference resolved before the halt, and is committed.
    assert summary.resolved == 1
    # The pending-username queue was never reached.
    assert "newcomer" not in client.resolved
    async with inventory.session() as session:
        assert await session.get(PendingMention, "newcomer") is not None


async def test_a_short_flood_wait_is_still_waited_out_in_resolution(
    inventory: Database, slept: list[float]
) -> None:
    await add_forward_channel(inventory, FWD_ID)
    client = FakeTelegramClient(
        entities_by_id={FWD_ID: tl_channel(FWD_ID, username="a")},
        resolve_floods={FWD_ID: 30},
    )

    summary = await resolve_inventory(client, inventory, delay=0)

    assert 30 in slept
    assert summary.halt is None
    assert summary.resolved == 1
