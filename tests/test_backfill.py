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
    link_discussion_chat,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    BackfillState,
    BackfillStatus,
    Channel,
    CollectionCommand,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    FailureKind,
    RawMessage,
    RejectReason,
)
from itgraph.db.session import Database
from itgraph.tg import backfill as backfill_module
from itgraph.tg import pacing as pacing_module
from itgraph.tg.backfill import backfill_channel, backfill_channels
from itgraph.tg.floods import FloodRecorder

NOTES = FakeChannel(1000000001, "example_notes", "Example Notes")
CHAT = FakeChannel(1000000002, "example_notes_chat", "Example Notes - chat")
JOBS = FakeChannel(1000000005, "example_jobs", "Example Jobs")
COMMUNITY = FakeChannel(1000000009, "example_community", "Example Community")

CUTOFF = datetime(2026, 5, 1, tzinfo=UTC)
DEEPER = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleeps instead of taking them.

    Both seams: pacing gaps come from ``tg.pacing``, FloodWait sleeps
    from ``waiting_out_floods`` in ``tg.backfill``. One list, because
    what most tests want to know is simply that a run never sleeps for
    real.
    """
    taken: list[float] = []

    async def sleep(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(pacing_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)
    return taken


@pytest.fixture
def no_long_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch off the rare long pause, so a band assertion is decidable.

    Tests that care about the band want the band; the long pause has its
    own test.
    """
    from itgraph.config import settings

    monkeypatch.setattr(settings, "pacing_long_pause_chance", 0.0)


@pytest.fixture
def gaps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Only the pacing gaps, in order, with the channel pauses removed.

    The band a gap falls in is the contract now, so a test asserting on
    it must not have to sift 10-to-40-second channel pauses out of the
    same list.
    """
    taken: list[float] = []
    real_gap = pacing_module.request_gap

    def record(delay: float) -> float:
        gap = real_gap(delay)
        taken.append(gap)
        return gap

    monkeypatch.setattr(pacing_module, "request_gap", record)
    return taken


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """The real mix: two channels, a linked chat, a standalone one, junk.

    The two chats differ in the one way that matters here. ``CHAT``
    belongs to ``NOTES``, so it is out of scope because its parent is
    what was reviewed; ``COMMUNITY`` belongs to nothing and was accepted
    on its own, so it is deferred rather than passed over.
    """
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(NOTES.id, "example_notes", "Notes", False),
                DiscoveredChannel(JOBS.id, "example_jobs", "Jobs", False),
                DiscoveredChannel(CHAT.id, "example_notes_chat", "Chat", True),
                DiscoveredChannel(
                    COMMUNITY.id, "example_community", "Community", True
                ),
                DiscoveredChannel(999000001, "rejected_one", "Nope", False),
                DiscoveredChannel(999000002, None, "No username", False),
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await link_discussion_chat(
            session,
            parent_tg_id=NOTES.id,
            chat=DiscoveredChannel(
                CHAT.id, "example_notes_chat", "Chat", True
            ),
        )
        for tg_id in (NOTES.id, JOBS.id, CHAT.id, COMMUNITY.id, 999000002):
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
            database=database,
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


async def test_a_standalone_chat_is_deferred_rather_than_silently_skipped(
    inventory: Database, slept: list[float]
) -> None:
    """A chat accepted on its own is waiting work, not work passed over.

    Nothing walks community chats yet, so the run must say so: otherwise
    a reviewed chat sits untouched behind a summary that reads clean.
    """
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(2, newest_id=500)}
    )

    summary = await run(inventory, telegram)

    walked = {entity_id for entity_id, _, _ in telegram.windows}
    assert COMMUNITY.id not in walked
    # The linked chat is not deferred — its parent channel was what was
    # reviewed, and that is what gets walked.
    assert summary.deferred == 1
    assert "1 standalone chat deferred" in summary.line()


async def test_a_run_with_no_standalone_chats_says_nothing_about_them(
    inventory: Database, slept: list[float]
) -> None:
    async with inventory.session() as session:
        await mark_channel(
            session,
            COMMUNITY.id,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.NOT_IT,
        )

    summary = await run(inventory, client(histories={NOTES.id: history(1)}))

    assert summary.deferred == 0
    assert "deferred" not in summary.line()


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
    """A completed run writes the raw layer and touches nothing derived.

    Edges and pending mentions belong to the derivation change; the
    collector must leave them empty. Their tables exist in the schema now,
    so the invariant is about rows, not about which tables are present: a
    row in either would mean parsing had crept into the collector.
    """
    telegram = client(histories={NOTES.id: history(3)})

    await run(inventory, telegram)

    async with inventory.session() as session:
        # History did arrive — otherwise this passes vacuously.
        assert (await session.execute(select(RawMessage).limit(1))).first()

        edges = await session.scalar(text("SELECT count(*) FROM edges"))
        pending = await session.scalar(
            text("SELECT count(*) FROM pending_mentions")
        )

    assert edges == 0
    assert pending == 0


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
    inventory: Database,
    slept: list[float],
    gaps: list[float],
    no_long_pauses: None,
) -> None:
    """One at a time, with a gap before every request.

    Concurrency would buy nothing — Telegram's limits are per account, so
    parallel workers reach the same ceiling faster and look worse doing
    it.

    The gap is drawn per request now, so the band is what can be asserted
    — an exact value would only prove the randomization was not wired up.
    """
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)}
    )

    await run(inventory, telegram, request_delay=2.5)

    # A channel is finished before the next is touched: the windows are
    # grouped, not interleaved.
    walked = [entity_id for entity_id, _, _ in telegram.windows]
    assert walked == sorted(walked)
    # A gap before every request — the history windows, and the metadata
    # request that opens each channel.
    assert len(gaps) == len(telegram.windows) + 2
    # Every one of them inside the band, and none of them the bare delay
    # repeated, which is what this used to assert.
    assert all(1.25 <= gap <= 3.75 for gap in gaps)
    assert len(set(gaps)) > 1


async def test_the_defaults_are_the_slow_ones(
    inventory: Database, slept: list[float], gaps: list[float]
) -> None:
    """No pacing options means the conservative configured defaults."""
    from itgraph.config import settings

    telegram = client(histories={NOTES.id: history(2), JOBS.id: history(2)})

    async with inventory.session() as session:
        await backfill_channels(
            telegram, session, cutoff=CUTOFF, database=inventory
        )

    delay = settings.backfill_request_delay
    assert delay >= 1
    assert gaps
    # Either the ordinary band around the configured delay, or one of the
    # rare long pauses — nothing in between, and nothing shorter.
    assert all(
        delay * 0.5 <= gap <= delay * 1.5
        or settings.pacing_long_pause_min
        <= gap
        <= settings.pacing_long_pause_max
        for gap in gaps
    )


async def test_pacing_can_be_switched_off(
    inventory: Database, slept: list[float]
) -> None:
    """A delay of zero takes no gap at all, not a jittered almost-zero.

    Zero is how an operator says they know what they are doing. A
    mechanism that occasionally sleeps 40 seconds regardless would be a
    surprise, so the long pause must not fire either.
    """
    telegram = client(histories={NOTES.id: history(2), JOBS.id: history(2)})

    await run(inventory, telegram, request_delay=0)

    # Only the pauses between channels are left; no per-request gap of
    # any size was taken, including a literal sleep(0).
    assert len(slept) == 1
    assert 10 <= slept[0] <= 40


async def test_a_long_pause_replaces_the_gap_rather_than_adding_to_it(
    inventory: Database, slept: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rare long pause is the gap, not an extra sleep beside it."""
    from itgraph.config import settings

    monkeypatch.setattr(settings, "pacing_long_pause_chance", 1.0)
    telegram = client(histories={NOTES.id: history(2)})

    await run(inventory, telegram, request_delay=2.5, limit=1)

    long_pauses = [
        gap
        for gap in slept
        if settings.pacing_long_pause_min
        <= gap
        <= settings.pacing_long_pause_max
    ]
    assert long_pauses
    # Nothing from the ordinary band alongside them.
    assert not [gap for gap in slept if 1.25 <= gap <= 3.75]


async def test_gaps_do_not_come_from_the_seedable_global_random() -> None:
    """A `random.seed()` anywhere must not make the pacing predictable.

    This is the only property `secrets` buys over `random` here, so it is
    the one worth pinning.
    """
    import random

    from itgraph.tg.pacing import request_gap

    random.seed(1234)
    first = [request_gap(4.0) for _ in range(20)]
    random.seed(1234)
    second = [request_gap(4.0) for _ in range(20)]

    assert first != second


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
        result = await backfill_channel(
            later,
            session,
            channel_id=NOTES.id,
            username="example_notes",
            cutoff=DEEPER,
            max_messages=10,
            recorder=FloodRecorder(inventory, CollectionCommand.BACKFILL),
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
    assert summary.deferred == 1
    assert "completed 2" in summary.line()


# --- the pause between channels ---------------------------------------


async def test_a_longer_pause_separates_channels(
    inventory: Database, slept: list[float], no_long_pauses: None
) -> None:
    """Where the quota-bearing per-channel requests cluster.

    Before this existed, a channel's metadata request followed the
    previous channel's last history request with no gap whatsoever — the
    one boundary in the walk that was not paced at all, in front of the
    one request that is never cached.
    """
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)}
    )

    await run(inventory, telegram, request_delay=2.5)

    pauses = [gap for gap in slept if 10 <= gap <= 40]
    # Two channels walked, so exactly one transition between them.
    assert len(pauses) == 1


async def test_the_first_channel_is_not_delayed(
    inventory: Database, slept: list[float]
) -> None:
    """It separates channels; there is nothing before the first one."""
    telegram = client(histories={NOTES.id: history(2)})

    await run(inventory, telegram, request_delay=0, limit=1)

    assert slept == []


async def test_a_skipped_channel_costs_no_pause(
    inventory: Database, slept: list[float]
) -> None:
    """A channel that makes no request should not cost 25 seconds.

    The inventory holds a seed channel with no username, and its id sorts
    ahead of the rest, so the run skips it before touching anything. Were
    the pause taken in the loop header rather than past the guards, the
    channel that follows would pay for it.
    """
    telegram = client(histories={NOTES.id: history(2)})

    summary = await run(inventory, telegram, request_delay=0, limit=1)

    assert summary.skipped == 1
    assert summary.completed == 1
    # The skip is not work, so the channel after it is still the first
    # one to do any — and nothing was separated from anything.
    assert slept == []


# --- the conditional metadata pass -------------------------------------


async def test_a_recent_payload_is_not_refetched(
    inventory: Database, slept: list[float]
) -> None:
    """The least cacheable request in the walk, skipped when it is not due.

    Two hundred channels re-read every run is two hundred quota-bearing
    requests spent to learn that a description has not changed.
    """
    first = client(histories={NOTES.id: history(2)})
    await run(inventory, first, limit=1)
    assert len(first.requests) == 1

    second = client(histories={NOTES.id: history(2, newest_id=900)})
    await run(inventory, second, cutoff=DEEPER, limit=1)

    # No GetFullChannelRequest, and no username resolution either: the
    # peer came out of the session's own cache.
    assert second.requests == []
    assert second.resolved == []
    assert second.input_entities == ["example_notes"]
    # And the walk still happened.
    assert second.windows


async def test_a_stale_payload_is_refreshed(
    inventory: Database, slept: list[float]
) -> None:
    first = client(histories={NOTES.id: history(2)})
    await run(inventory, first, limit=1)

    # Age the stored payload past the freshness window.
    async with inventory.session() as session:
        await session.execute(
            text(
                "UPDATE raw_channels SET fetched_at = now() - interval "
                "'400 days' WHERE channel_id = :cid"
            ),
            {"cid": NOTES.id},
        )
        await session.commit()

    second = client(histories={NOTES.id: history(2, newest_id=900)})
    await run(inventory, second, cutoff=DEEPER, limit=1)

    assert len(second.requests) == 1


async def test_the_skip_falls_back_rather_than_failing(
    inventory: Database, slept: list[float]
) -> None:
    """A session that cannot supply the peer must not turn a skip into a
    failure — the fallback is the path that already worked."""
    first = client(histories={NOTES.id: history(2)})
    await run(inventory, first, limit=1)

    second = client(histories={NOTES.id: history(2, newest_id=900)})
    second.cached_peers = {}

    summary = await run(inventory, second, cutoff=DEEPER, limit=1)

    assert summary.failed == 0
    # Asked the cache, was refused, ran the full pass instead.
    assert second.input_entities == ["example_notes"]
    assert len(second.requests) == 1
    assert second.windows


async def test_a_refresh_can_be_demanded(
    inventory: Database, slept: list[float]
) -> None:
    first = client(histories={NOTES.id: history(2)})
    await run(inventory, first, limit=1)

    second = client(histories={NOTES.id: history(2, newest_id=900)})
    await run(inventory, second, cutoff=DEEPER, limit=1, refresh_metadata=True)

    assert len(second.requests) == 1
    # The cached path was not even consulted.
    assert second.input_entities == []


# --- halting on a long FloodWait ---------------------------------------


async def test_a_wait_within_the_threshold_is_still_slept_off(
    inventory: Database, slept: list[float]
) -> None:
    """The existing behaviour, unchanged below the threshold."""
    from itgraph.config import settings

    assert settings.flood_abort_threshold == 1800
    telegram = client(
        histories={NOTES.id: history(3)}, flood_on_window={0: 1800}
    )

    summary = await run(inventory, telegram, limit=1)

    assert 1800 in slept
    assert summary.halt is None
    assert summary.failed == 0
    assert await stored_ids(inventory, NOTES.id) == [1000, 999, 998]


async def test_a_long_wait_halts_the_run(
    inventory: Database, slept: list[float]
) -> None:
    """A day-long wait is not slept through inside the process."""
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)},
        flood_on_window={0: 86400},
    )

    summary = await run(inventory, telegram)

    assert summary.halt is not None
    assert summary.halt.seconds == 86400
    assert summary.halt.resume_after > datetime.now(UTC)
    # Never slept it off, and never asked for anything else.
    assert 86400 not in slept
    walked = {entity_id for entity_id, _, _ in telegram.windows}
    assert walked == {NOTES.id}


async def test_a_halt_is_not_recorded_as_a_channel_failure(
    inventory: Database, slept: list[float]
) -> None:
    """`FloodWaitError` is an `RPCError`; a halt must not be one.

    Were the halt absorbed by the per-channel handler, it would be filed
    as a transient failure and the run would ask the next channel
    immediately — a fresh request at the exact moment Telegram asked for
    silence, which is what escalates a limit into a ban.
    """
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)},
        flood_on_window={0: 86400},
    )

    summary = await run(inventory, telegram)

    assert summary.failed == 0
    state = await state_of(inventory, NOTES.id)
    assert state is not None
    assert state.failure_kind is None


async def test_a_halted_run_reports_what_it_committed(
    inventory: Database, slept: list[float]
) -> None:
    """The counts around a halt are real work, not a lost run."""
    telegram = client(
        histories={NOTES.id: history(3), JOBS.id: history(3, newest_id=500)},
        # The first channel finishes; the second is refused outright.
        flood_on_window={1: 86400},
    )

    summary = await run(inventory, telegram)

    assert summary.halt is not None
    assert summary.completed == 1
    assert summary.stored == 3
    assert await stored_ids(inventory, NOTES.id) == [1000, 999, 998]


async def test_a_halted_run_resumes_from_its_cursor(
    inventory: Database, slept: list[float]
) -> None:
    """A halt leaves the same state an interruption would."""
    telegram = client(
        histories={NOTES.id: history(5)}, flood_on_window={1: 86400}
    )
    halted = await run(inventory, telegram, batch_size=2, limit=1)
    assert halted.halt is not None
    partial = await stored_ids(inventory, NOTES.id)
    assert partial == [1000, 999]

    later = client(histories={NOTES.id: history(5)})
    resumed = await run(inventory, later, batch_size=2, limit=1)

    assert resumed.halt is None
    assert await stored_ids(inventory, NOTES.id) == [
        1000,
        999,
        998,
        997,
        996,
    ]
