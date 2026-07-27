"""The metadata pass: which channels are due, and what a run costs.

The scenarios that used to live in `test_backfill.py` under "the
conditional metadata pass" are here now — freshness, refresh-on-demand,
staleness — because that is where the behaviour went. What is new is the
isolation: a halt here must leave a following backfill able to walk
everything.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fakes import FakeChannel, FakeFullChannel, FakeTelegramClient, history
from sqlalchemy import select, text

from itgraph.config import settings
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    FloodEvent,
    RawChannel,
)
from itgraph.db.session import Database
from itgraph.tg import backfill as backfill_module
from itgraph.tg import pacing as pacing_module
from itgraph.tg.backfill import backfill_channels
from itgraph.tg.metadata import refresh_metadata

NOTES = FakeChannel(1000000001, "example_notes", "Example Notes")
CHAT = FakeChannel(1000000002, "example_notes_chat", "Example Notes - chat")
JOBS = FakeChannel(1000000005, "example_jobs", "Example Jobs")

CUTOFF = datetime(2026, 5, 1, tzinfo=UTC)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleeps instead of taking them."""
    taken: list[float] = []

    async def sleep(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(pacing_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)
    return taken


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """Two accepted channels, plus one nobody accepted."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(NOTES.id, "example_notes", "Notes", False),
                DiscoveredChannel(JOBS.id, "example_jobs", "Jobs", False),
                DiscoveredChannel(999000001, "rejected_one", "Nope", False),
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        for tg_id in (NOTES.id, JOBS.id):
            await mark_channel(
                session,
                tg_id,
                status=ChannelStatus.SEED,
                kind=ChannelKind.PERSONAL,
            )
    yield database


def client(**kwargs: object) -> FakeTelegramClient:
    return FakeTelegramClient(
        entities={"example_notes": NOTES, "example_jobs": JOBS},
        full_channels={
            NOTES.id: FakeFullChannel(NOTES, linked_chat=CHAT),
            JOBS.id: FakeFullChannel(JOBS),
        },
        **kwargs,  # type: ignore[arg-type]
    )


async def age_payload(database: Database, channel_id: int) -> None:
    """Push a stored payload past the freshness window."""
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE raw_channels SET fetched_at = now() - interval "
                "'400 days' WHERE channel_id = :cid"
            ),
            {"cid": channel_id},
        )
        await session.commit()


# --- what is due ------------------------------------------------------


async def test_a_channel_never_seen_before_is_fetched(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client()

    summary = await refresh_metadata(telegram, inventory, delay=0)

    assert summary.fetched == 2
    async with inventory.session() as session:
        rows = (await session.scalars(select(RawChannel))).all()
    assert {row.channel_id for row in rows} == {NOTES.id, JOBS.id}


async def test_a_recent_payload_is_not_refetched(
    inventory: Database, slept: list[float]
) -> None:
    """The whole reason the freshness window exists.

    Two hundred channels re-read every run is two hundred quota-bearing
    requests spent to learn that a description has not changed.
    """
    await refresh_metadata(client(), inventory, delay=0)

    second = client()
    summary = await refresh_metadata(second, inventory, delay=0)

    assert summary.fetched == 0
    assert second.requests == []


async def test_a_stale_payload_is_refreshed(
    inventory: Database, slept: list[float]
) -> None:
    await refresh_metadata(client(), inventory, delay=0)
    await age_payload(inventory, NOTES.id)

    second = client()
    summary = await refresh_metadata(second, inventory, delay=0)

    # Only the aged one; the other is still fresh.
    assert summary.fetched == 1
    assert len(second.requests) == 1


async def test_a_refresh_can_be_demanded(
    inventory: Database, slept: list[float]
) -> None:
    await refresh_metadata(client(), inventory, delay=0)

    second = client()
    summary = await refresh_metadata(second, inventory, delay=0, refresh=True)

    assert summary.fetched == 2
    assert len(second.requests) == 2


async def test_only_accepted_channels_are_touched(
    inventory: Database, slept: list[float]
) -> None:
    """The same scope the walk keeps, from the same predicates."""
    telegram = client()

    await refresh_metadata(telegram, inventory, delay=0)

    async with inventory.session() as session:
        rows = (await session.scalars(select(RawChannel))).all()
    assert 999000001 not in {row.channel_id for row in rows}


# --- what a run costs -------------------------------------------------


async def test_the_pass_resolves_no_username(
    inventory: Database, slept: list[float]
) -> None:
    """`contacts.resolveUsername` belongs to one command, and not this one."""
    telegram = client()

    await refresh_metadata(telegram, inventory, delay=0)

    assert telegram.resolved == []
    assert telegram.input_entities == ["example_notes", "example_jobs"]


async def test_a_channel_with_no_cached_peer_is_skipped(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client()
    del telegram.cached_peers["example_notes"]

    summary = await refresh_metadata(telegram, inventory, delay=0)

    assert summary.skipped == 1
    assert summary.fetched == 1
    assert telegram.resolved == []


async def test_the_run_is_bounded(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client()

    summary = await refresh_metadata(telegram, inventory, delay=0, limit=1)

    assert summary.fetched == 1
    assert len(telegram.requests) == 1
    # The rest of the queue is still the queue.
    second = client()
    assert (await refresh_metadata(second, inventory, delay=0)).fetched == 1


async def test_the_linked_chat_is_resolved(
    inventory: Database, slept: list[float]
) -> None:
    summary = await refresh_metadata(client(), inventory, delay=0)

    assert summary.linked == 1
    async with inventory.session() as session:
        chat = await session.get(Channel, CHAT.id)
    assert chat is not None
    assert chat.linked_to == NOTES.id
    assert chat.discovered_via is DiscoverySource.LINKED_CHAT


# --- the walk reports the queue it no longer works --------------------


async def test_backfill_reports_stale_metadata_without_fetching_any(
    inventory: Database, slept: list[float]
) -> None:
    """The answer to "an operator will forget the command exists".

    Counted from the database before the walk starts, so it costs a query
    and no request — and a run halted by a rate limit still reports it.
    """
    walker = FakeTelegramClient(
        entities={"example_notes": NOTES, "example_jobs": JOBS},
        histories={NOTES.id: history(2), JOBS.id: history(2, newest_id=500)},
    )

    async with inventory.session() as session:
        summary = await backfill_channels(
            walker,
            session,
            cutoff=CUTOFF,
            request_delay=0,
            database=inventory,
        )

    assert summary.stale_metadata == 2
    assert "awaiting `itgraph metadata`" in summary.line()
    # Reported, not fetched.
    assert walker.requests == []


async def test_the_count_falls_as_the_pass_works_through_it(
    inventory: Database, slept: list[float]
) -> None:
    await refresh_metadata(client(), inventory, delay=0, limit=1)

    walker = FakeTelegramClient(
        entities={"example_notes": NOTES, "example_jobs": JOBS},
        histories={NOTES.id: history(2), JOBS.id: history(2, newest_id=500)},
    )
    async with inventory.session() as session:
        summary = await backfill_channels(
            walker,
            session,
            cutoff=CUTOFF,
            request_delay=0,
            database=inventory,
        )

    assert summary.stale_metadata == 1


async def test_nothing_stale_says_nothing(
    inventory: Database, slept: list[float]
) -> None:
    await refresh_metadata(client(), inventory, delay=0)

    walker = FakeTelegramClient(
        entities={"example_notes": NOTES, "example_jobs": JOBS},
        histories={NOTES.id: history(2), JOBS.id: history(2, newest_id=500)},
    )
    async with inventory.session() as session:
        summary = await backfill_channels(
            walker,
            session,
            cutoff=CUTOFF,
            request_delay=0,
            database=inventory,
        )

    assert summary.stale_metadata == 0
    assert "metadata" not in summary.line()


# --- halting, and what it does not take down --------------------------


async def test_a_short_flood_is_slept_off(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(flood_on_request={NOTES.id: 30})

    summary = await refresh_metadata(telegram, inventory, delay=0)

    assert summary.fetched == 2
    assert 30 in slept


async def test_a_long_flood_halts_and_reports_what_landed(
    inventory: Database, slept: list[float]
) -> None:
    """Committed work survives the halt, and the rest stays queued."""
    over = int(settings.flood_abort_threshold) + 1
    # NOTES sorts first, so it lands before JOBS is refused.
    telegram = client(flood_on_request={JOBS.id: over})

    summary = await refresh_metadata(telegram, inventory, delay=0)

    assert summary.halt is not None
    assert summary.fetched == 1
    # Not slept through — the run stopped instead.
    assert over not in slept

    async with inventory.session() as session:
        stored = (await session.scalars(select(RawChannel))).all()
    assert {row.channel_id for row in stored} == {NOTES.id}


async def test_the_halt_is_recorded_against_this_command(
    inventory: Database, slept: list[float]
) -> None:
    """A rate limit here must not read as one the history walk caused."""
    over = int(settings.flood_abort_threshold) + 1
    telegram = client(flood_on_request={NOTES.id: over})

    await refresh_metadata(telegram, inventory, delay=0)

    async with inventory.session() as session:
        events = (await session.scalars(select(FloodEvent))).all()

    assert len(events) == 1
    assert events[0].command is CollectionCommand.METADATA
    assert events[0].channel_id == NOTES.id
    assert events[0].halted is True


async def test_a_halted_metadata_pass_leaves_history_collectable(
    inventory: Database, slept: list[float]
) -> None:
    """The isolation this whole change exists for.

    While the metadata fetch opened every walk, a run that exhausted its
    quota collected no history either — the expensive pass took the cheap
    one down with it. Now it can stop dead on its first channel and a
    backfill afterwards still walks everything.
    """
    over = int(settings.flood_abort_threshold) + 1
    stopped = client(flood_on_request={NOTES.id: over})

    metadata_run = await refresh_metadata(stopped, inventory, delay=0)

    assert metadata_run.halt is not None
    assert metadata_run.fetched == 0

    walker = FakeTelegramClient(
        entities={"example_notes": NOTES, "example_jobs": JOBS},
        histories={NOTES.id: history(2), JOBS.id: history(2, newest_id=500)},
    )
    async with inventory.session() as session:
        walk = await backfill_channels(
            walker,
            session,
            cutoff=CUTOFF,
            request_delay=0,
            database=inventory,
        )

    assert walk.completed == 2
    assert walk.halt is None
    # And it spent nothing rationed doing it.
    assert walker.requests == []
    assert walker.resolved == []
