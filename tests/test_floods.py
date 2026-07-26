"""The record of rate limits: what is stored, and what it must not break.

Two hazards are under test here as much as the happy path. Recording
must not be able to turn a survivable rate limit into a failed run, and
it must not disturb the transaction of the walk it interrupts — the
second one is how a run would lose history it had already fetched.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeChannel, FakeFullChannel, FakeTelegramClient, history
from sqlalchemy import select
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.types import InputChannel

from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.floods import flood_summary, recent_floods
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    FloodEvent,
    RawMessage,
)
from itgraph.db.session import Database
from itgraph.tg import backfill as backfill_module
from itgraph.tg import floods as floods_module
from itgraph.tg import pacing as pacing_module
from itgraph.tg.backfill import backfill_channels
from itgraph.tg.floods import UNKNOWN_METHOD, FloodRecorder, method_name

NOTES = FakeChannel(1000000001, "example_notes", "Example Notes")
CUTOFF = datetime(2026, 5, 1, tzinfo=UTC)


# --- unwrapping the method name ---------------------------------------


def resolve_request() -> ResolveUsernameRequest:
    return ResolveUsernameRequest(username="example_notes")


def test_a_bare_request_is_its_own_name() -> None:
    assert method_name(resolve_request()) == "ResolveUsernameRequest"


def test_a_wrapped_request_reports_the_inner_one() -> None:
    """The wrapper's name would file every method under one label."""
    wrapped = InvokeWithLayerRequest(layer=158, query=resolve_request())

    assert method_name(wrapped) == "ResolveUsernameRequest"


def test_a_doubly_wrapped_request_is_unwrapped_all_the_way() -> None:
    inner = GetFullChannelRequest(
        channel=InputChannel(channel_id=1, access_hash=2)
    )
    wrapped = InvokeWithLayerRequest(
        layer=158, query=InvokeWithLayerRequest(layer=158, query=inner)
    )

    assert method_name(wrapped) == "GetFullChannelRequest"


def test_a_missing_request_is_unknown() -> None:
    """A real value, not a failure: the duration is still worth having."""
    assert method_name(None) == UNKNOWN_METHOD


def test_a_search_request_is_not_mistaken_for_a_wrapper() -> None:
    """`query` on a search request is a string, not a nested request.

    Duck-typing on the presence of `.query` would unwrap this one and
    store the name of a `str`.
    """

    class SearchPostsRequest:
        query = "python"

    assert method_name(SearchPostsRequest()) == "SearchPostsRequest"


def test_an_unknown_wrapper_shape_does_not_crash() -> None:
    """A crash inside a rate-limit handler is the one unacceptable bug."""

    class InvokeWithSomethingNewRequest:
        query = None

    name = method_name(InvokeWithSomethingNewRequest())

    assert name == "InvokeWithSomethingNewRequest"


# --- recording, end to end --------------------------------------------


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    taken: list[float] = []

    async def sleep(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(pacing_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)
    return taken


@pytest.fixture
async def inventory(database: Database) -> Any:
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(NOTES.id, "example_notes", "Notes", False)],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await mark_channel(
            session,
            NOTES.id,
            status=ChannelStatus.SEED,
            kind=ChannelKind.PERSONAL,
        )
    return database


def client(**kwargs: Any) -> FakeTelegramClient:
    return FakeTelegramClient(
        entities={"example_notes": NOTES},
        full_channels={NOTES.id: FakeFullChannel(NOTES)},
        **kwargs,
    )


async def events_in(database: Database) -> list[FloodEvent]:
    async with database.session() as session:
        return list(
            await session.scalars(select(FloodEvent).order_by(FloodEvent.id))
        )


async def run(database: Database, telegram: Any, **kwargs: Any) -> Any:
    async with database.session() as session:
        return await backfill_channels(
            telegram,
            session,
            cutoff=CUTOFF,
            request_delay=0,
            database=database,
            **kwargs,
        )


async def test_a_slept_off_wait_is_recorded(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3)},
        flood_on_window={0: 42},
        flood_request=resolve_request(),
    )

    await run(inventory, telegram)

    events = await events_in(inventory)
    assert len(events) == 1
    assert events[0].method == "ResolveUsernameRequest"
    assert events[0].seconds == 42
    assert events[0].command is CollectionCommand.BACKFILL
    assert events[0].channel_id == NOTES.id
    assert events[0].halted is False
    # And the wait was still handled the way it always was.
    assert 42 in slept


async def test_a_halting_wait_is_recorded_as_halted(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3)},
        flood_on_window={0: 86400},
        flood_request=resolve_request(),
    )

    summary = await run(inventory, telegram)

    assert summary.halt is not None
    events = await events_in(inventory)
    assert len(events) == 1
    assert events[0].halted is True
    assert events[0].seconds == 86400


async def test_a_wait_naming_no_request_records_unknown(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3)}, flood_on_window={0: 42}
    )

    await run(inventory, telegram)

    events = await events_in(inventory)
    assert len(events) == 1
    assert events[0].method == UNKNOWN_METHOD
    # The duration survives, which is the point of not refusing.
    assert events[0].seconds == 42


async def test_the_wrapped_method_is_what_lands_in_the_table(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3)},
        flood_on_window={0: 42},
        flood_request=InvokeWithLayerRequest(
            layer=158, query=resolve_request()
        ),
    )

    await run(inventory, telegram)

    events = await events_in(inventory)
    assert events[0].method == "ResolveUsernameRequest"


async def test_recording_cannot_break_collection(
    inventory: Database, slept: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telemetry that can crash a run is worse than no telemetry."""

    async def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the recorder is broken")

    monkeypatch.setattr(floods_module, "store_flood_event", explode)
    telegram = client(
        histories={NOTES.id: history(3)}, flood_on_window={0: 42}
    )

    summary = await run(inventory, telegram)

    # The wait was still slept off and the walk still finished.
    assert 42 in slept
    assert summary.failed == 0
    assert summary.completed == 1
    assert await events_in(inventory) == []


async def test_recording_does_not_disturb_the_walk_in_progress(
    inventory: Database, slept: list[float]
) -> None:
    """A flood mid-walk must not cost the batches already committed.

    The recorder writes on a session of its own precisely so that
    committing an event cannot commit — or roll back — the caller's
    half-finished batch.
    """
    telegram = client(
        histories={NOTES.id: history(6)},
        # Third window: two batches are already committed by then.
        flood_on_window={2: 42},
        flood_request=resolve_request(),
    )

    summary = await run(inventory, telegram, batch_size=2)

    assert summary.failed == 0
    async with inventory.session() as session:
        stored = list(
            await session.scalars(
                select(RawMessage.msg_id).where(
                    RawMessage.channel_id == NOTES.id
                )
            )
        )
    # Everything fetched is still there — the recorder took nothing with it.
    assert len(stored) == 6
    assert len(await events_in(inventory)) == 1


# --- reading it back ---------------------------------------------------


async def test_the_summary_counts_and_ranks_by_method(
    database: Database,
) -> None:
    recorder = FloodRecorder(database, CollectionCommand.RESOLVE)
    for seconds in (10, 30):
        await recorder.record(
            request=resolve_request(), seconds=seconds, halted=False
        )
    await recorder.record(
        request=GetFullChannelRequest(
            channel=InputChannel(channel_id=1, access_hash=2)
        ),
        seconds=99,
        halted=True,
    )

    async with database.session() as session:
        tallies = await flood_summary(session)

    assert [t.method for t in tallies] == [
        "ResolveUsernameRequest",
        "GetFullChannelRequest",
    ]
    assert tallies[0].times == 2
    assert tallies[0].longest == 30
    assert tallies[1].times == 1


async def test_a_window_excludes_older_events(database: Database) -> None:
    recorder = FloodRecorder(database, CollectionCommand.BACKFILL)
    await recorder.record(request=resolve_request(), seconds=5, halted=False)

    async with database.session() as session:
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        assert await recent_floods(session, since=tomorrow) == []
        assert await flood_summary(session, since=tomorrow) == []
        assert len(await recent_floods(session)) == 1


async def test_the_two_commands_are_told_apart(database: Database) -> None:
    """Both spend the same methods; the command is what separates them."""
    for command in (CollectionCommand.BACKFILL, CollectionCommand.RESOLVE):
        await FloodRecorder(database, command).record(
            request=resolve_request(), seconds=7, halted=False
        )

    async with database.session() as session:
        events = await recent_floods(session)

    assert {event.command for event in events} == {
        CollectionCommand.BACKFILL,
        CollectionCommand.RESOLVE,
    }


async def test_resolution_records_its_own_rate_limits(
    database: Database, slept: list[float]
) -> None:
    """End to end, so the wiring in `resolve` is what is under test."""
    from itgraph.db.edges import create_discovered_channels
    from itgraph.tg.resolve import resolve_inventory

    referenced = 2000000001
    async with database.session() as session:
        await create_discovered_channels(
            session,
            tg_ids=[referenced],
            discovered_via=DiscoverySource.FORWARD,
        )

    telegram = FakeTelegramClient(
        entities_by_id={referenced: NOTES},
        resolve_floods={referenced: 55},
        flood_request=resolve_request(),
    )

    await resolve_inventory(telegram, database, delay=0)

    events = await events_in(database)
    assert len(events) == 1
    assert events[0].command is CollectionCommand.RESOLVE
    assert events[0].method == "ResolveUsernameRequest"
    assert events[0].seconds == 55
    # Resolution walks references, not channels, so none is attributed.
    assert events[0].channel_id is None
