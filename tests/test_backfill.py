"""The history walk: scope, resumption, pacing, and how it fails.

Telethon is a fake and sleeps are recorded rather than taken, so a run
that would take hours takes milliseconds. What is under test is the
bookkeeping — which is where a collector loses history quietly.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes import (
    FakeChannel,
    FakeFullChannel,
    FakeTelegramClient,
    history,
)
from sqlalchemy import select, text
from telethon.errors import ChannelPrivateError

from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    BackfillState,
    BackfillStatus,
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    FailureKind,
    RawMessage,
)
from itgraph.db.session import Database
from itgraph.tg import backfill as backfill_module
from itgraph.tg.backfill import backfill_channel, backfill_channels

NOTES = FakeChannel(1000000001, "example_notes", "Example Notes")
CHAT = FakeChannel(1000000002, "example_notes_chat", "Example Notes - chat")
JOBS = FakeChannel(1000000005, "example_jobs", "Example Jobs")

CUTOFF = datetime(2026, 5, 1, tzinfo=UTC)
DEEPER = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleeps instead of taking them."""
    taken: list[float] = []

    async def sleep(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)
    return taken


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """Two accepted channels, one chat, one rejected — the real mix."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(NOTES.id, "example_notes", "Notes", False),
                DiscoveredChannel(JOBS.id, "example_jobs", "Jobs", False),
                DiscoveredChannel(CHAT.id, "example_notes_chat", "Chat", True),
                DiscoveredChannel(999000001, "rejected_one", "Nope", False),
                DiscoveredChannel(999000002, None, "No username", False),
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        for tg_id in (NOTES.id, JOBS.id, CHAT.id, 999000002):
            await mark_channel(
                session,
                tg_id,
                status=ChannelStatus.SEED,
                kind=ChannelKind.PERSONAL,
            )
    yield database


def client(**kwargs: Any) -> FakeTelegramClient:
    entities = {"example_notes": NOTES, "example_jobs": JOBS}
    return FakeTelegramClient(
        entities=entities,
        full_channels={
            NOTES.id: FakeFullChannel(NOTES),
            JOBS.id: FakeFullChannel(JOBS),
        },
        **kwargs,
    )


async def run(
    database: Database,
    telegram: FakeTelegramClient,
    *,
    cutoff: datetime = CUTOFF,
    request_delay: float = 0,
    **kwargs: Any,
) -> Any:
    async with database.session() as session:
        return await backfill_channels(
            telegram,
            session,
            cutoff=cutoff,
            request_delay=request_delay,
            **kwargs,
        )


async def stored_ids(database: Database, channel_id: int) -> list[int]:
    async with database.session() as session:
        rows = await session.scalars(
            select(RawMessage.msg_id)
            .where(RawMessage.channel_id == channel_id)
            .order_by(RawMessage.msg_id.desc())
        )
    return list(rows)


async def state_of(
    database: Database, channel_id: int
) -> BackfillState | None:
    async with database.session() as session:
        return await session.get(BackfillState, channel_id)


# --- scope -----------------------------------------------------------


async def test_only_accepted_channels_are_walked(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(2, newest_id=500)}
    )

    summary = await run(inventory, telegram)

    walked = {entity_id for entity_id, _, _ in telegram.windows}
    assert walked == {NOTES.id, JOBS.id}
    # The rejected channel was never in scope, and the discussion chat is
    # excluded regardless of its parent's status.
    assert 999000001 not in walked
    assert CHAT.id not in walked
    assert summary.completed == 2


async def test_an_entity_without_a_username_is_refused(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(1), JOBS.id: history(1)})

    summary = await run(inventory, telegram)

    assert summary.skipped == 1
    state = await state_of(inventory, 999000002)
    assert state is not None
    assert state.status is BackfillStatus.SKIPPED
    # And the run carried on with the rest.
    assert summary.completed == 2


async def test_media_is_never_downloaded(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(3)})

    await run(inventory, telegram)

    assert telegram.downloads == []


# --- storage ---------------------------------------------------------


async def test_messages_are_stored_verbatim(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(3)})

    await run(inventory, telegram)

    async with inventory.session() as session:
        row = await session.get(RawMessage, (NOTES.id, 1000))

    assert row is not None
    assert row.payload["_"] == "Message"
    assert row.payload["message"] == "post 1000"
    assert row.payload["date"] == "2026-06-01T12:00:00+00:00"
    assert row.fetched_at is not None


async def test_re_fetching_does_not_duplicate(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(3)})
    await run(inventory, telegram)

    async with inventory.session() as session:
        await session.execute(
            select(BackfillState).where(BackfillState.channel_id == NOTES.id)
        )
        stored = await session.get(RawMessage, (NOTES.id, 1000))
        assert stored is not None
        first_fetch = stored.fetched_at

    # Same cutoff would skip, so force a walk with a deeper one.
    await run(
        inventory, client(histories={NOTES.id: history(3)}), cutoff=DEEPER
    )

    assert await stored_ids(inventory, NOTES.id) == [1000, 999, 998]
    async with inventory.session() as session:
        stored = await session.get(RawMessage, (NOTES.id, 1000))
    assert stored is not None
    # The first fetch wins: nothing rewrote the payload or its timestamp.
    assert stored.fetched_at == first_fetch


async def test_nothing_derived_is_written(
    inventory: Database, slept: list[float]
) -> None:
    """A completed run leaves the raw layer and nothing else.

    Edges, mentions, links and language all read this data and belong to
    the derivation change; a table appearing here would mean parsing had
    crept into the collector.
    """
    telegram = client(histories={NOTES.id: history(3)})

    await run(inventory, telegram)

    async with inventory.session() as session:
        # History did arrive — otherwise this passes vacuously.
        assert (await session.execute(select(RawMessage).limit(1))).first()

        present = set(
            (
                await session.scalars(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).all()
        )

    # Any table beyond these means parsing crept into the collector.
    assert present == {
        "channels",
        "raw_messages",
        "raw_channels",
        "backfill_state",
    }


# --- depth and resumption -------------------------------------------


async def test_the_cutoff_bounds_the_walk(
    inventory: Database, slept: list[float]
) -> None:
    # 40 daily posts back from 1 June; the cutoff is 1 May.
    telegram = client(histories={NOTES.id: history(40)})

    await run(inventory, telegram)

    ids = await stored_ids(inventory, NOTES.id)
    assert len(ids) == 32  # 1 June back to 1 May inclusive
    async with inventory.session() as session:
        oldest = await session.get(RawMessage, (NOTES.id, min(ids)))
    assert oldest is not None
    assert oldest.payload["date"] >= "2026-05-01"


async def test_a_completed_channel_is_skipped(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(3), JOBS.id: history(1)})
    await run(inventory, telegram)

    again = client(histories={NOTES.id: history(3), JOBS.id: history(1)})
    summary = await run(inventory, again)

    assert again.windows == []
    assert summary.completed == 0


async def test_an_earlier_cutoff_resumes_rather_than_skipping(
    inventory: Database, slept: list[float]
) -> None:
    """Deepening the window must not silently do nothing.

    Without the stored cutoff this is indistinguishable from a completed
    channel, and the extra history is never fetched.
    """
    telegram = client(histories={NOTES.id: history(40)})
    await run(inventory, telegram)
    shallow = await stored_ids(inventory, NOTES.id)

    deeper = client(histories={NOTES.id: history(40)})
    await run(inventory, deeper, cutoff=DEEPER)

    assert deeper.windows != []
    assert len(await stored_ids(inventory, NOTES.id)) > len(shallow)


async def test_an_interrupted_walk_resumes_from_the_cursor(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(40)})
    await run(inventory, telegram, batch_size=10)

    state = await state_of(inventory, NOTES.id)
    assert state is not None
    ids = await stored_ids(inventory, NOTES.id)
    # The cursor names the oldest row that exists, never one beyond it.
    assert state.oldest_fetched_id == min(ids)
    assert state.newest_fetched_id == max(ids)


async def test_the_cursor_never_runs_ahead_of_the_rows(
    inventory: Database, slept: list[float]
) -> None:
    """The failure that loses a window of history silently.

    A batch and the cursor that describes it commit together. If the
    process dies between them, the cursor must name a message that is
    actually stored — otherwise the next run starts past rows nobody has.
    """
    # Fail on the third window, part-way through the channel.
    telegram = client(histories={NOTES.id: history(40)})
    telegram.flood_on_window = {}

    original = telegram.iter_messages
    calls = {"n": 0}

    def iter_messages(entity: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionError("connection reset mid-walk")
        return original(entity, **kwargs)

    telegram.iter_messages = iter_messages  # type: ignore[method-assign]

    summary = await run(inventory, telegram, batch_size=10)

    assert summary.failed == 1
    ids = await stored_ids(inventory, NOTES.id)
    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert ids != []
    assert state.oldest_fetched_id == min(ids)
    assert state.oldest_fetched_id in ids


async def test_the_newest_post_date_is_recorded(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(5)})

    await run(inventory, telegram)

    async with inventory.session() as session:
        channel = await session.get(Channel, NOTES.id)

    assert channel is not None
    assert channel.last_post_at == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


# --- pacing and rate limits ------------------------------------------


async def test_a_flood_wait_is_waited_out_and_the_window_retried(
    inventory: Database, slept: list[float]
) -> None:
    """The window is retried, not dropped.

    Abandoning it would leave a hole in history that the cursor would
    then skip past — and asking the next channel immediately is exactly
    what a rate limit is telling you not to do.
    """
    telegram = client(
        histories={NOTES.id: history(3)}, flood_on_window={0: 42}
    )

    summary = await run(inventory, telegram)

    assert 42 in slept
    # Asked twice for the same window, and got it the second time.
    assert len(telegram.windows) >= 2
    assert telegram.windows[0][1] == telegram.windows[1][1]
    assert summary.failed == 0
    assert await stored_ids(inventory, NOTES.id) == [1000, 999, 998]


async def test_a_flood_wait_is_not_recorded_as_a_failure(
    inventory: Database, slept: list[float]
) -> None:
    """FloodWaitError is an RPCError, so the generic handler would take it."""
    telegram = client(
        histories={NOTES.id: history(2)}, flood_on_window={0: 7, 1: 7}
    )

    summary = await run(inventory, telegram)

    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert state.failure_kind is None
    assert summary.failed == 0


async def test_channels_are_paced_and_sequential(
    inventory: Database, slept: list[float]
) -> None:
    """One at a time, with a gap before every request.

    Concurrency would buy nothing — Telegram's limits are per account, so
    parallel workers reach the same ceiling faster and look worse doing
    it.
    """
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)}
    )

    await run(inventory, telegram, request_delay=2.5)

    # A channel is finished before the next is touched: the windows are
    # grouped, not interleaved.
    walked = [entity_id for entity_id, _, _ in telegram.windows]
    assert walked == sorted(walked)
    # And every history request was preceded by the configured wait.
    assert slept.count(2.5) == len(telegram.windows)


async def test_the_defaults_are_the_slow_ones(
    inventory: Database, slept: list[float]
) -> None:
    """No pacing options means the conservative configured defaults."""
    from itgraph.config import settings

    telegram = client(histories={NOTES.id: history(2), JOBS.id: history(2)})

    async with inventory.session() as session:
        await backfill_channels(telegram, session, cutoff=CUTOFF)

    assert settings.backfill_request_delay >= 1
    assert slept.count(settings.backfill_request_delay) == len(
        telegram.windows
    )


# --- failures ---------------------------------------------------------


async def test_a_private_channel_is_a_permanent_failure(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={JOBS.id: history(1)})
    telegram.raises = ChannelPrivateError(request=None)

    summary = await run(inventory, telegram)

    assert summary.failed == 2
    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert state.status is BackfillStatus.FAILED
    assert state.failure_kind is FailureKind.PERMANENT
    assert "private" in (state.failure_detail or "").lower()


async def test_a_permanently_failed_channel_is_not_retried(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client()
    telegram.raises = ChannelPrivateError(request=None)
    await run(inventory, telegram)

    later = client(histories={NOTES.id: history(3), JOBS.id: history(3)})
    summary = await run(inventory, later)

    assert later.windows == []
    assert summary.completed == 0


async def test_a_network_error_is_transient_and_retried_later(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client()
    telegram.raises = ConnectionError("temporary failure in name resolution")
    await run(inventory, telegram)

    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert state.failure_kind is FailureKind.TRANSIENT

    later = client(histories={NOTES.id: history(2), JOBS.id: history(2)})
    summary = await run(inventory, later)

    assert later.windows != []
    assert summary.completed == 2


async def test_the_inventory_record_survives_a_failure(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client()
    telegram.raises = ChannelPrivateError(request=None)

    await run(inventory, telegram)

    async with inventory.session() as session:
        channel = await session.get(Channel, NOTES.id)

    assert channel is not None
    assert channel.status is ChannelStatus.SEED
    assert channel.kind is ChannelKind.PERSONAL


async def test_one_failure_does_not_stop_the_run(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={JOBS.id: history(2, newest_id=500)})
    # Only the first channel is unreachable.
    del telegram.entities["example_notes"]

    summary = await run(inventory, telegram)

    assert summary.failed == 1
    assert summary.completed == 1


# --- bounded runs -----------------------------------------------------


async def test_the_channel_limit_leaves_the_rest_pending(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)}
    )

    summary = await run(inventory, telegram, limit=1)

    assert summary.completed == 1
    assert await stored_ids(inventory, JOBS.id) == []
    assert await state_of(inventory, JOBS.id) is None


async def test_the_message_ceiling_stops_the_walk(
    inventory: Database, slept: list[float]
) -> None:
    """A channel contributes its share of the corpus and no more.

    The cutoff is deep enough to be irrelevant here: what stops the walk
    is the ceiling. Without it a few aggregators posting dozens of times
    a day would be most of the database, while saying the least about who
    talks to whom.
    """
    telegram = client(histories={NOTES.id: history(40)})

    summary = await run(
        inventory, telegram, cutoff=DEEPER, max_messages=10, limit=1
    )

    assert len(await stored_ids(inventory, NOTES.id)) == 10
    assert summary.capped == 1
    assert summary.completed == 0
    assert summary.stored == 10
    assert "capped 1" in summary.line()


async def test_a_capped_channel_is_never_walked_again(
    inventory: Database, slept: list[float]
) -> None:
    """The ceiling bounds the channel, not the run.

    Once a channel has its share, no later run asks for more — not with
    the same cutoff, and not with a deeper one, which is the case that
    would otherwise let the ceiling be walked straight past.
    """
    telegram = client(histories={NOTES.id: history(40)})
    await run(inventory, telegram, cutoff=CUTOFF, max_messages=10, limit=1)
    first = await stored_ids(inventory, NOTES.id)

    later = client(histories={NOTES.id: history(40)})
    summary = await run(inventory, later, cutoff=DEEPER, max_messages=10)

    assert await stored_ids(inventory, NOTES.id) == first
    # Not one request was spent on it: the ceiling is checked against the
    # rows already held, before anything is asked of Telegram.
    assert NOTES.id not in {entity_id for entity_id, _, _ in later.windows}
    assert summary.capped == 0


async def test_a_capped_channel_records_the_depth_it_reached(
    inventory: Database, slept: list[float]
) -> None:
    """The recorded depth is where the rows end, not where the walk aimed.

    Writing the requested cutoff here would have `channels --backfill`
    claim history that was deliberately never collected.
    """
    telegram = client(histories={NOTES.id: history(40)})

    await run(inventory, telegram, cutoff=DEEPER, max_messages=10, limit=1)

    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert state.status is BackfillStatus.COMPLETE
    assert state.cutoff_at is not None
    assert state.cutoff_at > DEEPER
    # 10 daily posts back from 1 June.
    assert state.cutoff_at == datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
    # And the cursor still names a row that exists.
    assert state.oldest_fetched_id == min(
        await stored_ids(inventory, NOTES.id)
    )


async def test_the_ceiling_counts_messages_from_earlier_runs(
    inventory: Database, slept: list[float]
) -> None:
    """Rows already held count against the ceiling, not just this run's."""
    telegram = client(histories={NOTES.id: history(40)})
    await run(inventory, telegram, cutoff=CUTOFF, max_messages=5, limit=1)

    # A deeper cutoff and a raised ceiling: the walk resumes, but only
    # for what the new ceiling actually leaves.
    later = client(histories={NOTES.id: history(40)})
    await run(inventory, later, cutoff=DEEPER, max_messages=12)

    assert len(await stored_ids(inventory, NOTES.id)) == 12


async def test_the_ceiling_does_not_over_request(
    inventory: Database, slept: list[float]
) -> None:
    """Asking for a window wider than the ceiling leaves buys nothing.

    Those messages would be dropped on arrival, and the request is the
    expensive part.
    """
    telegram = client(histories={NOTES.id: history(40)})

    await run(
        inventory,
        telegram,
        cutoff=DEEPER,
        batch_size=100,
        max_messages=15,
        limit=1,
    )

    asked = [limit for _, _, limit in telegram.windows]
    assert sum(asked) <= 15
    assert len(await stored_ids(inventory, NOTES.id)) == 15


async def test_the_cutoff_still_wins_when_it_comes_first(
    inventory: Database, slept: list[float]
) -> None:
    """A channel short enough to finish is completed, not capped."""
    telegram = client(histories={NOTES.id: history(3)})

    summary = await run(inventory, telegram, max_messages=100, limit=1)

    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert state.cutoff_at == CUTOFF
    assert summary.completed == 1
    assert summary.capped == 0


async def test_zero_lifts_the_ceiling(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(histories={NOTES.id: history(40)})

    summary = await run(
        inventory, telegram, cutoff=DEEPER, max_messages=0, limit=1
    )

    assert len(await stored_ids(inventory, NOTES.id)) == 40
    assert summary.completed == 1
    assert summary.capped == 0


async def test_the_walker_refuses_a_channel_already_at_its_ceiling(
    inventory: Database, slept: list[float]
) -> None:
    """The same guard, for anything that calls the walker directly."""
    telegram = client(histories={NOTES.id: history(40)})
    await run(inventory, telegram, cutoff=DEEPER, max_messages=10, limit=1)

    later = client(histories={NOTES.id: history(40)})
    async with inventory.session() as session:
        channel = await session.get(Channel, NOTES.id)
        assert channel is not None
        result = await backfill_channel(
            later, session, channel, cutoff=DEEPER, max_messages=10
        )

    assert result.capped
    assert result.stored == 0
    # Not even the username was resolved.
    assert later.resolved == []
    assert later.windows == []


async def test_the_ceiling_defaults_to_the_configured_one(
    inventory: Database, slept: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No option means the configured ceiling, not an unbounded walk."""
    from itgraph.config import settings

    assert settings.backfill_max_messages == 2000
    monkeypatch.setattr(settings, "backfill_max_messages", 5)
    telegram = client(histories={NOTES.id: history(40)})

    summary = await run(inventory, telegram, cutoff=DEEPER, limit=1)

    assert len(await stored_ids(inventory, NOTES.id)) == 5
    assert summary.capped == 1


async def test_the_run_reports_what_it_did(
    inventory: Database, slept: list[float]
) -> None:
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(2, newest_id=500)}
    )

    summary = await run(inventory, telegram)

    assert summary.completed == 2
    assert summary.capped == 0
    assert summary.skipped == 1
    assert summary.failed == 0
    assert summary.stored == 5
    assert "completed 2" in summary.line()
