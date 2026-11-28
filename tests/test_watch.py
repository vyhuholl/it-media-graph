"""The poll loop.

The most important assertion in this file is the dullest one: a poll
issues no ``resolveUsername`` and no ``getFullChannel``. That is the
invariant the loop is likeliest to break by accident — one convenience
call to ``get_entity`` and the tightest daily quota in the project is
gone every day until somebody reads `tg_flood_events`.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import (
    FakeChannel,
    FakeHistoryMessage,
    FakeMessageWith,
    FakeServiceMessage,
    FakeTelegramClient,
    tl_channel,
)
from people_cases import fields_naming_people, names_nobody, peer_channel
from sqlalchemy import text
from telethon.errors import ChannelPrivateError, FloodWaitError
from telethon.tl.functions.contacts import ResolveUsernameRequest

from itgraph.config import settings
from itgraph.db.backfill import record_complete
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.edges import (
    EdgeRow,
    MentionSource,
    add_pending_mentions,
    create_discovered_channels,
    insert_edges,
)
from itgraph.db.floods import store_flood_event
from itgraph.db.metrics import store_metrics
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    EdgeKind,
)
from itgraph.db.poll import due_channels
from itgraph.db.raw import store_messages
from itgraph.db.session import Database
from itgraph.derive.metrics import Counters
from itgraph.tg import watch as watch_module
from itgraph.tg.backfill import RequestTimedOut
from itgraph.tg.errors import WatchStalled
from itgraph.tg.manual import Review, add_channels
from itgraph.tg.payload import encode_payload
from itgraph.tg.watch import watch

FIRST = 1000000001
SECOND = 1000000002
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
# The depth a walked seed is recorded at; any date in the past would do.
WALKED_TO = datetime(2025, 7, 24, tzinfo=UTC)
DAY = 24 * 60  # minutes, for `fresh`

# A stall window a test can outlive, and enough cycles to outlive it.
# The stall tests are the one place where running *past* the window is
# the whole point: a test that finished inside it would pass whether or
# not the loop counts an idle cycle as progress.
STALL_MINUTES = 0.01
CYCLES_PAST_A_STALL = 100


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop's politeness is tested in `test_pacing.py`, not by waiting."""
    monkeypatch.setattr(settings, "watch_request_delay", 0.0)
    monkeypatch.setattr(settings, "watch_tick_seconds", 0.01)
    # The wait after a failed reconnect, for the same reason: a test
    # about what the loop does next should not sit out the real delay.
    monkeypatch.setattr(settings, "watch_reconnect_delay_seconds", 0.01)
    # The walk of new seeds paces itself as `itgraph backfill` does, and
    # pauses between seeds; neither is waited out here.
    monkeypatch.setattr(settings, "backfill_request_delay", 0.0)
    monkeypatch.setattr(settings, "backfill_channel_pause_min", 0.0)
    monkeypatch.setattr(settings, "backfill_channel_pause_max", 0.0)
    # Quiet hours off, so a test's outcome does not depend on the hour it
    # happens to run at.
    monkeypatch.setattr(settings, "watch_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "watch_quiet_to_hour", 0)


async def seed(database: Database, *tg_ids: int, walked: bool = True) -> None:
    """Seeds, walked to their depth unless asked otherwise.

    Walked, because that is what a seed in production is: the loop walks
    a new one before long, and a test about polling should not have its
    requests mixed with a walk's. The walk's own tests pass ``walked=False``.
    """
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=tg_id,
                    username=f"example_{tg_id}",
                    title=f"Example {tg_id}",
                    is_chat=False,
                )
                for tg_id in tg_ids
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await session.execute(
            text(
                "UPDATE tg_channels SET status = 'seed' WHERE tg_id = ANY(:ids)"
            ),
            {"ids": list(tg_ids)},
        )
        if walked:
            for tg_id in tg_ids:
                await record_complete(session, tg_id, WALKED_TO)


def client_for(
    histories: dict[int, list[Any]], **kwargs: Any
) -> FakeTelegramClient:
    entities = {
        f"example_{tg_id}": FakeChannel(tg_id, f"example_{tg_id}")
        for tg_id in histories
    }
    return FakeTelegramClient(entities=entities, histories=histories, **kwargs)


def fresh(msg_id: int, minutes_ago: float, **counters: Any) -> Any:
    return FakeHistoryMessage(
        msg_id,
        datetime.now(UTC) - timedelta(minutes=minutes_ago),
        f"post {msg_id}",
        **counters,
    )


async def snapshots(database: Database) -> list[tuple[Any, ...]]:
    async with database.session() as session:
        result = await session.execute(
            text(
                "SELECT channel_id, msg_id, views, forwards, reactions, "
                "comments FROM tg_message_metrics ORDER BY msg_id, observed_at"
            )
        )
        return [tuple(row) for row in result.all()]


async def test_a_poll_stores_posts_and_snapshots_them(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=100, forwards=2)]})

    stats = await watch(client, database, max_cycles=1)

    assert stats.polled == 1
    assert stats.stored == 1
    assert stats.snapshots == 1
    assert await snapshots(database) == [(FIRST, 10, 100, 2, None, None)]


async def test_a_polled_post_is_stored_naming_nobody(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    await seed(database, FIRST)
    message = FakeMessageWith(
        10,
        datetime.now(UTC) - timedelta(minutes=5),
        fields_naming_people(),
    )
    client = client_for({FIRST: [message]})
    caplog.set_level(logging.DEBUG)

    stats = await watch(client, database, max_cycles=1)

    assert stats.stored == 1
    async with database.session() as session:
        payload = await session.scalar(
            text(
                "SELECT payload FROM tg_raw_messages "
                "WHERE channel_id = :c AND msg_id = 10"
            ),
            {"c": FIRST},
        )
    assert names_nobody(payload)
    # What the post is for stays: its comment count and the channel among
    # the commenters.
    assert payload["replies"]["replies"] == 4
    assert payload["replies"]["recent_repliers"] == [peer_channel()]
    assert names_nobody(caplog.text)


async def test_one_request_serves_both_jobs(database: Database) -> None:
    """The arithmetic the whole budget rests on.

    Three live posts and one new one, refreshed and collected by a single
    `getHistory`. Cost is per channel per cycle, not per post.
    """
    await seed(database, FIRST)
    client = client_for(
        {
            FIRST: [
                fresh(12, 1, views=10),
                fresh(11, 200, views=900),
                fresh(10, 800, views=2000),
            ]
        }
    )

    await watch(client, database, max_cycles=1)

    assert len(client.windows) == 1
    assert len(await snapshots(database)) == 3


async def test_a_poll_resolves_nothing(database: Database) -> None:
    """The invariant this loop is likeliest to break by accident."""
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    assert client.resolved == []
    assert client.requests == []
    # And it did go to the session's cache, rather than skipping the
    # lookup altogether.
    assert client.input_entities


async def test_counters_that_move_are_two_readings(
    database: Database,
) -> None:
    """A snapshot is a moment, and the next one is a different moment."""
    await seed(database, FIRST)
    post = fresh(10, 5, views=100, reactions={"👍": 3})
    client = client_for({FIRST: [post]})

    await watch(client, database, max_cycles=1)
    post.views = 250
    post.reactions = {"👍": 9, "🤡": 1}
    # Due again immediately, as it would be after its next sample.
    async with database.session() as session:
        await session.execute(
            text("UPDATE tg_poll_state SET due_at = now() - interval '1 hour'")
        )
    await watch(client, database, max_cycles=1)

    rows = await snapshots(database)
    assert [(row[2], row[4]) for row in rows] == [
        (100, {"👍": 3}),
        (250, {"👍": 9, "🤡": 1}),
    ]


async def test_a_poll_that_finds_nothing_new_still_snapshots(
    database: Database,
) -> None:
    await seed(database, FIRST)
    post = fresh(10, 5, views=100)
    client = client_for({FIRST: [post]})

    await watch(client, database, max_cycles=1)
    async with database.session() as session:
        await session.execute(
            text("UPDATE tg_poll_state SET due_at = now() - interval '1 hour'")
        )
    post.views = 150
    stats = await watch(client, database, max_cycles=1)

    assert stats.stored == 0
    assert stats.snapshots == 1


async def test_a_post_past_the_late_horizon_is_not_snapshotted(
    database: Database,
) -> None:
    """Past 35 days a post is not read, late or otherwise; and between
    the tracking horizon and the first late age there is nothing to read
    yet."""
    await seed(database, FIRST)
    old = fresh(9, DAY * 40, views=5000)
    between = fresh(8, DAY * 2.5, views=700)
    client = client_for({FIRST: [fresh(10, 5, views=10), between, old]})

    await watch(client, database, max_cycles=1)

    assert [row[1] for row in await snapshots(database)] == [10]
    # ...but both are stored, because the request already returned them.
    async with database.session() as session:
        stored = await session.scalar(
            text("SELECT count(*) FROM tg_raw_messages")
        )
    assert stored == 3


# --- late readings ------------------------------------------------------


async def readings_of(database: Database, msg_id: int) -> list[datetime]:
    async with database.session() as session:
        result = await session.execute(
            text(
                "SELECT observed_at FROM tg_message_metrics "
                "WHERE msg_id = :m ORDER BY observed_at"
            ),
            {"m": msg_id},
        )
        return list(result.scalars())


async def read_at(
    database: Database, channel: int, message: Any, age: timedelta
) -> None:
    """Store a message and one reading of it at the given age."""
    async with database.session() as session:
        await store_messages(
            session,
            channel_id=channel,
            payloads={message.id: encode_payload(message)},
        )
        await store_metrics(
            session,
            channel_id=channel,
            observed_at=message.date + age,
            counters={message.id: Counters(views=1)},
        )


async def due_again(database: Database) -> None:
    async with database.session() as session:
        await session.execute(
            text("UPDATE tg_poll_state SET due_at = now() - interval '1 hour'")
        )


async def test_a_post_that_has_passed_a_late_age_is_read(
    database: Database,
) -> None:
    """From the window the poll fetched anyway, at the poll's moment."""
    await seed(database, FIRST)
    old = fresh(9, DAY * 3.5, views=4321, forwards=7)
    await read_at(database, FIRST, old, timedelta(hours=48))
    client = client_for({FIRST: [fresh(10, 5, views=10), old]})

    before = datetime.now(UTC)
    stats = await watch(client, database, max_cycles=1)

    assert stats.late == 1
    assert "1 late" in stats.line()
    taken = await readings_of(database, 9)
    assert len(taken) == 2
    assert taken[-1] >= before
    rows = [row for row in await snapshots(database) if row[1] == 9]
    assert (rows[-1][2], rows[-1][3]) == (4321, 7)
    assert len(client.windows) == 1


async def test_several_late_ages_passed_at_once_give_one_reading(
    database: Database,
) -> None:
    await seed(database, FIRST)
    old = fresh(9, DAY * 6, views=100)
    await read_at(database, FIRST, old, timedelta(hours=48))
    client = client_for({FIRST: [old]})

    await watch(client, database, max_cycles=1)
    await due_again(database)
    await watch(client, database, max_cycles=1)

    # The one at 48 hours, and one at six days; none before seven.
    assert len(await readings_of(database, 9)) == 2


async def test_a_post_already_read_at_its_latest_late_age_is_not_read_again(
    database: Database,
) -> None:
    await seed(database, FIRST)
    old = fresh(9, DAY * 3.8, views=100)
    await read_at(database, FIRST, old, timedelta(days=3.2))
    client = client_for({FIRST: [old]})

    stats = await watch(client, database, max_cycles=1)

    assert stats.late == 0
    assert len(await readings_of(database, 9)) == 1


async def test_a_post_the_window_does_not_reach_is_not_read(
    database: Database,
) -> None:
    """Ten messages a window for a channel with no rate yet; the eleventh
    is not asked for, however due it is."""
    await seed(database, FIRST)
    history = [fresh(100 - n, 5 + n, views=1) for n in range(10)]
    beyond = fresh(50, DAY * 10, views=9999)
    client = client_for({FIRST: [*history, beyond]})

    await watch(client, database, max_cycles=1)

    assert await readings_of(database, 50) == []
    assert len(client.windows) == 1


async def test_a_late_reading_does_not_replay_a_missed_sample(
    database: Database,
) -> None:
    """Nothing read at 24 or 48 hours; one reading now, and only one."""
    await seed(database, FIRST)
    old = fresh(9, DAY * 3.5, views=300)
    client = client_for({FIRST: [old]})

    await watch(client, database, max_cycles=1)

    assert len(await readings_of(database, 9)) == 1


async def test_late_readings_cost_no_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same window asked for, and the same next due moment, whether
    or not the window holds posts due a late reading."""

    def history() -> list[Any]:
        return [fresh(9, DAY * 3.5, views=1), fresh(8, DAY * 8, views=1)]

    async def interval(tg_id: int) -> timedelta:
        async with database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT due_at - last_polled_at FROM tg_poll_state "
                        "WHERE channel_id = :c"
                    ),
                    {"c": tg_id},
                )
            ).one()
        return row[0]  # type: ignore[no-any-return]

    await seed(database, FIRST)
    ages = settings.watch_late_offsets_days

    monkeypatch.setattr(settings, "watch_late_offsets_days", ())
    off = client_for({FIRST: history()})
    await watch(off, database, max_cycles=1)
    assert await snapshots(database) == []

    # Seeded only now, so the first run did not find it due and skip it.
    await seed(database, SECOND)
    monkeypatch.setattr(settings, "watch_late_offsets_days", ages)
    on = client_for({SECOND: history()})
    stats = await watch(on, database, max_cycles=1)
    assert stats.late == 2

    assert [limit for _, _, limit in off.windows] == [
        limit for _, _, limit in on.windows
    ]
    assert await interval(FIRST) == await interval(SECOND)


async def test_a_service_message_is_stored_but_not_measured(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for(
        {
            FIRST: [
                FakeServiceMessage(11, datetime.now(UTC)),
                fresh(10, 5, views=10),
            ]
        }
    )

    await watch(client, database, max_cycles=1)

    assert [row[1] for row in await snapshots(database)] == [10]
    async with database.session() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM tg_raw_messages"))
            == 2
        )


async def test_absent_and_zero_reach_the_table_intact(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for(
        {
            FIRST: [
                fresh(11, 5, views=10, reactions={}, comments=0),
                fresh(10, 6, views=10),
            ]
        }
    )

    await watch(client, database, max_cycles=1)

    rows = {row[1]: (row[4], row[5]) for row in await snapshots(database)}
    assert rows[10] == (None, None)
    assert rows[11] == ({}, 0)


async def test_the_forward_cursor_advances(database: Database) -> None:
    """`newest_fetched_id` is read in place, and this is what writes it."""
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(42, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        newest = await session.scalar(
            text("SELECT newest_fetched_id FROM tg_backfill_state")
        )
    assert newest == 42


async def test_the_cursor_never_moves_backwards(
    database: Database,
) -> None:
    """A short window must not rewind a mark a walk has already set."""
    await seed(database, FIRST, walked=False)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_backfill_state (channel_id, newest_fetched_id) "
                "VALUES (:id, 9999)"
            ),
            {"id": FIRST},
        )
    client = client_for({FIRST: [fresh(42, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        newest = await session.scalar(
            text("SELECT newest_fetched_id FROM tg_backfill_state")
        )
    assert newest == 9999


async def test_a_channel_is_rescheduled_after_a_poll(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        assert await due_channels(session, now=datetime.now(UTC)) == []


async def test_a_cold_cache_is_skipped_not_resolved(
    database: Database,
) -> None:
    """And skipped is not failed.

    Recorded as a failure it would be classified permanent and dropped
    from scope for good — silently, and in a loop, forever.
    """
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]}, cached_peers={})

    stats = await watch(client, database, max_cycles=1)

    assert stats.skipped == 1
    assert stats.failed == 0
    assert client.resolved == []
    # Still in scope: the next run may have a warm session.
    async with database.session() as session:
        assert await due_channels(
            session, now=datetime.now(UTC) + timedelta(days=2)
        )


async def test_one_broken_channel_does_not_stop_the_loop(
    database: Database,
) -> None:
    await seed(database, FIRST, SECOND)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=1)]},
        raises_for={FIRST: ChannelPrivateError(request=None)},
    )

    stats = await watch(client, database, max_cycles=1)

    assert stats.failed == 1
    assert stats.polled == 1
    assert [row[0] for row in await snapshots(database)] == [SECOND]


async def test_a_failure_is_recorded_against_the_channel(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]},
        raises_for={FIRST: ChannelPrivateError(request=None)},
    )

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        error, failures = (
            await session.execute(
                text(
                    "SELECT last_error, consecutive_failures FROM tg_poll_state"
                )
            )
        ).one()
    assert "ChannelPrivateError" in error
    assert failures == 1


async def test_a_short_flood_is_slept_off_and_retried(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]}, flood_on_window={0: 1}
    )

    stats = await watch(client, database, max_cycles=1)

    assert stats.polled == 1
    assert len(client.windows) == 2


async def test_a_long_flood_postpones_instead_of_exiting(
    database: Database,
) -> None:
    """A batch job halts here. A loop that halted would stop being the product."""
    await seed(database, FIRST, SECOND)
    seconds = int(settings.flood_abort_threshold) + 60
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=1)]},
        flood_on_window={0: seconds},
    )
    # Give both channels a row, so there is something to postpone.
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_poll_state (channel_id, due_at) "
                "SELECT tg_id, now() - interval '1 hour' FROM tg_channels"
            )
        )

    stats = await watch(client, database, max_cycles=1)

    assert stats.postponed == 1
    assert stats.failed == 0
    async with database.session() as session:
        soonest = await session.scalar(
            text("SELECT min(due_at) FROM tg_poll_state")
        )
    assert soonest > datetime.now(UTC) + timedelta(seconds=seconds - 120)


async def test_a_long_flood_is_not_a_channel_failure(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]},
        flood_on_window={0: int(settings.flood_abort_threshold) + 60},
    )

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        failures = await session.scalar(
            text(
                "SELECT coalesce(max(consecutive_failures), 0) FROM tg_poll_state"
            )
        )
    assert failures == 0


async def test_the_flood_is_recorded_against_the_watch_command(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]}, flood_on_window={0: 1}
    )

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        commands = (
            await session.scalars(text("SELECT command FROM tg_flood_events"))
        ).all()
    assert list(commands) == ["watch"]


async def test_the_loop_stops_when_asked(database: Database) -> None:
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})
    stop = asyncio.Event()
    stop.set()

    stats = await watch(client, database, stop=stop)

    assert stats.cycles == 0
    assert client.windows == []


async def test_an_empty_queue_costs_no_request(database: Database) -> None:
    client = client_for({})

    stats = await watch(client, database, max_cycles=2)

    assert client.windows == []
    assert stats.polled == 0


async def test_channels_are_polled_one_at_a_time(
    database: Database,
) -> None:
    """No concurrency, structurally.

    Parallel workers reach the same per-account ceiling faster and look
    worse doing it.
    """
    await seed(database, FIRST, SECOND)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=1)]}
    )

    await watch(client, database, max_cycles=1)

    assert [window[0] for window in client.windows] == [FIRST, SECOND]


async def test_the_loop_derives_nothing(database: Database) -> None:
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        assert await session.scalar(text("SELECT count(*) FROM tg_edges")) == 0
        assert (
            await session.scalar(
                text("SELECT count(*) FROM tg_pending_mentions")
            )
            == 0
        )


async def test_the_review_state_is_untouched(database: Database) -> None:
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        status, reviewed = (
            await session.execute(
                text(
                    "SELECT status, reviewed_at FROM tg_channels WHERE tg_id=:id"
                ),
                {"id": FIRST},
            )
        ).one()
    assert status == "seed"
    assert reviewed is None


async def test_a_capped_channel_is_still_polled_forward(
    database: Database,
) -> None:
    """The ceiling bounds history, not forward collection.

    The channels that reach it first are the most active in the
    inventory — precisely the ones a realtime product cannot be blind to.
    """
    await seed(database, FIRST, walked=False)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_backfill_state "
                "(channel_id, status, cutoff_at, oldest_fetched_id) "
                "VALUES (:id, 'complete', now() - interval '30 days', 1)"
            ),
            {"id": FIRST},
        )
    monkey = settings.backfill_max_messages
    assert monkey  # the ceiling is on; this test means nothing without it

    client = client_for({FIRST: [fresh(10, 5, views=1)]})
    stats = await watch(client, database, max_cycles=1)

    assert stats.stored == 1


async def test_a_flood_error_outside_the_window_is_a_failure(
    database: Database,
) -> None:
    """Sanity: the flood path is the window's, not a blanket catch."""
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    def explode(*args: Any, **kwargs: Any) -> None:
        raise FloodWaitError(request=None, capture=1)

    # Not reached through `waiting_out_floods`: encoding happens after.
    client.histories[FIRST][0].to_dict = explode  # type: ignore[method-assign]

    stats = await watch(client, database, max_cycles=1)

    assert stats.failed + stats.polled == 1


# --- a lost connection -------------------------------------------------
#
# The failure these are written from: Telegram closed the connection,
# Telethon accepted the loop's next request while reconnecting, the
# reconnect failed for good, and the request was left in a queue nothing
# drains. The `await` never returned. For 67 hours the process was a
# live PID holding a lease and collecting nothing, and `Restart=always`
# could not help because nothing had exited.


async def test_a_disconnected_client_is_not_polled_over(
    database: Database,
) -> None:
    """The check is before the queue is read, so no channel is reached.

    A request on a client that has given up fails instantly. Twenty-five
    of them is a whole batch marked failed, each pushed out by the
    failure backoff, for an outage that was nothing to do with any of
    those channels.
    """
    await seed(database, FIRST, SECOND)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=1)]},
        connected=False,
        connect_failures=5,
    )

    stats = await watch(client, database, max_cycles=2)

    assert client.windows == []
    assert stats.failed == 0
    async with database.session() as session:
        failures = await session.scalar(
            text(
                "SELECT coalesce(max(consecutive_failures), 0) FROM tg_poll_state"
            )
        )
    assert failures == 0


async def test_the_loop_reconnects_and_then_polls(
    database: Database,
) -> None:
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]}, connected=False)

    stats = await watch(client, database, max_cycles=2)

    assert client.connects == 1
    assert stats.reconnects == 1
    assert stats.polled == 1
    assert await snapshots(database) == [(FIRST, 10, 1, None, None, None)]


async def test_a_reconnect_that_fails_is_retried_next_cycle(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No escalating backoff: the stall check is what ends an outage."""
    monkeypatch.setattr(settings, "watch_reconnect_delay_seconds", 0.01)
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]}, connected=False, connect_failures=2
    )

    stats = await watch(client, database, max_cycles=4)

    assert client.connects == 3
    assert stats.reconnects == 1
    assert stats.polled == 1


async def test_a_connection_lost_mid_batch_stops_the_batch(
    database: Database,
) -> None:
    """Per channel, not per cycle — that is the whole point of the check.

    Checked once per batch, the 24 channels behind the one that lost the
    connection would each be asked, each fail instantly, and each be
    pushed out by the failure backoff.
    """
    await seed(database, FIRST, SECOND)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=1)]},
        disconnect_after=1,
    )

    stats = await watch(client, database, max_cycles=1)

    assert len(client.windows) == 1
    assert stats.failed == 0
    assert stats.polled == 1


async def test_a_request_that_never_answers_does_not_hang_the_loop(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug, in one test: the loop finishes rather than waiting forever."""
    monkeypatch.setattr(settings, "request_timeout_seconds", 0.05)
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]}, hangs=True)

    stats = await watch(client, database, max_cycles=1)

    assert stats.timed_out == 1
    assert stats.polled == 0


async def test_a_timed_out_request_discards_the_connection(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reusing it would ask the next channel over a connection that just
    proved it does not answer — a deadline each, all the way down the
    batch, with the connection check never firing because the client
    still calls itself connected."""
    monkeypatch.setattr(settings, "request_timeout_seconds", 0.05)
    await seed(database, FIRST, SECOND)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=1)]},
        hangs=True,
    )

    stats = await watch(client, database, max_cycles=1)

    assert client.disconnects == 1
    assert client.is_connected() is False
    # The batch stopped at the first one rather than spending a deadline
    # on the second.
    assert stats.timed_out == 1


async def test_the_loop_recovers_after_a_timeout(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deadline is survivable: the next cycle reconnects and polls.

    The channel that timed out is not the one re-polled — it was
    rescheduled with its failure recorded, deliberately, so that a
    channel which times out reliably cannot sit at the head of the queue
    forever. What recovers is the loop.
    """
    monkeypatch.setattr(settings, "request_timeout_seconds", 0.05)
    await seed(database, FIRST, SECOND)
    # Only the first request wedges, so the recovery is a fact about the
    # loop rather than a race against a timer.
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)], SECOND: [fresh(20, 5, views=7)]},
        hang_on_window={0},
    )

    stats = await watch(client, database, max_cycles=3)

    assert stats.timed_out == 1
    assert stats.polled == 1
    assert client.connects == 1
    assert await snapshots(database) == [(SECOND, 20, 7, None, None, None)]


# --- the stall check ---------------------------------------------------


async def test_a_loop_wedged_inside_one_await_stops(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incident itself, and the reason the check is a separate task.

    The loop is not slow here; it is *blocked*, inside a single await,
    with no cycle to run a check in — which is what 13 August looked
    like from inside the process. The deadline covers the one await we
    know can do this. This covers the next one, wherever it turns out to
    be: the guard is on a clock of its own, so it does not need the loop
    to be alive in order to notice that it isn't.
    """
    monkeypatch.setattr(settings, "watch_stall_minutes", STALL_MINUTES)

    async def never_returns(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()

    monkeypatch.setattr(watch_module, "poll_channel", never_returns)
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    with pytest.raises(WatchStalled):
        await watch(client, database, max_cycles=1)


async def test_an_idle_loop_is_not_stalled(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing due is not the same as nothing working.

    Run for longer than the stall window on purpose: a test that
    finished inside it would pass whether or not an idle cycle counts as
    progress, which is the only thing under test.
    """
    monkeypatch.setattr(settings, "watch_stall_minutes", STALL_MINUTES)
    client = client_for({})

    stats = await watch(client, database, max_cycles=CYCLES_PAST_A_STALL)

    assert stats.cycles == CYCLES_PAST_A_STALL


async def test_quiet_hours_are_not_a_stall(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "watch_stall_minutes", STALL_MINUTES)
    monkeypatch.setattr(watch_module, "in_quiet_hours", lambda _: True)
    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    stats = await watch(client, database, max_cycles=CYCLES_PAST_A_STALL)

    assert client.windows == []
    assert stats.cycles == CYCLES_PAST_A_STALL


async def test_a_postponed_schedule_is_not_a_stall(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rate limit stops the work, deliberately. It must not stop the loop."""
    monkeypatch.setattr(settings, "watch_stall_minutes", STALL_MINUTES)
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]},
        flood_on_window={0: int(settings.flood_abort_threshold) + 60},
    )

    stats = await watch(client, database, max_cycles=CYCLES_PAST_A_STALL)

    assert stats.postponed == 1


async def test_a_working_loop_is_not_stalled(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poll that concluded is progress, whatever it concluded.

    Including a failure: the channel answered, and what it said was no.
    """
    monkeypatch.setattr(settings, "watch_stall_minutes", STALL_MINUTES)
    await seed(database, FIRST)
    client = client_for(
        {FIRST: [fresh(10, 5, views=1)]},
        raises_for={FIRST: ChannelPrivateError(request=None)},
    )

    stats = await watch(client, database, max_cycles=CYCLES_PAST_A_STALL)

    assert stats.failed >= 1


async def test_a_lost_lease_still_arrives_as_itself(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure the stall check must not have reshaped on its way out.

    Running the loop beside a watchdog is what makes an unanticipated
    wedge survivable, and it is also how a perfectly good error path
    gets broken: under a `TaskGroup` this arrives as an `ExceptionGroup`,
    misses the tuple `cli.py` catches, and prints a traceback where a
    sentence used to be.
    """
    from itgraph.db.session_lease import LeaseLostError

    class LostLease:
        async def verify(self) -> None:
            raise LeaseLostError("the session lease is no longer held")

    await seed(database, FIRST)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})
    # The loop checks its lease once its clock, the host's monotonic one, is
    # past the interval: on a machine up for less than that, the one cycle
    # here would check nothing. A CI runner can be.
    monkeypatch.setattr(settings, "watch_lease_check_seconds", 1e-9)

    with pytest.raises(LeaseLostError):
        await watch(client, database, lease=LostLease(), max_cycles=1)  # type: ignore[arg-type]


# --- the walk of new seeds ---------------------------------------------
#
# A seed whose walk is not complete is walked by the loop, a page at a
# time, when nothing is due. A poll asks for `watch_window_min` messages
# and a page for `backfill_batch_size`, so a window's limit says which of
# the two asked for it.

PAGE = 3
POLL = 10
DEPTH = datetime(2026, 5, 1, tzinfo=UTC)


@pytest.fixture
def walking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "backfill_batch_size", PAGE)
    monkeypatch.setattr(settings, "watch_window_min", POLL)
    monkeypatch.setattr(settings, "history_walk_since", DEPTH)


def pages(client: FakeTelegramClient) -> list[tuple[int, int, int]]:
    return [window for window in client.windows if window[2] <= PAGE]


def polls(client: FakeTelegramClient) -> list[tuple[int, int, int]]:
    return [window for window in client.windows if window[2] > PAGE]


async def walk_state(database: Database, tg_id: int) -> tuple[Any, ...]:
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, cutoff_at, failure_kind, failure_detail "
                    "FROM tg_backfill_state WHERE channel_id = :id"
                ),
                {"id": tg_id},
            )
        ).one()
    return tuple(row)


async def held(database: Database, tg_id: int) -> int:
    async with database.session() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM tg_raw_messages WHERE channel_id = :id"
            ),
            {"id": tg_id},
        )
    return int(count or 0)


def old_history(count: int) -> list[Any]:
    """Posts a day apart from a month ago, all above `DEPTH`'s cutoff."""
    newest = datetime.now(UTC) - timedelta(days=30)
    return [
        FakeHistoryMessage(1000 - i, newest - timedelta(days=i), f"post {i}")
        for i in range(count)
    ]


async def test_a_new_seed_is_walked_to_its_depth(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    client = client_for({FIRST: old_history(7)})

    stats = await watch(client, database, max_cycles=10)

    assert await walk_state(database, FIRST) == ("complete", DEPTH, None, None)
    assert await held(database, FIRST) == 7
    assert stats.walks_done == 1
    assert client.resolved == []


async def test_a_seed_marked_from_the_command_line_is_walked(
    database: Database, walking: None
) -> None:
    """Through what `itgraph mark --seed` calls, not a hand-written status."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(FIRST, f"example_{FIRST}", "Example", False)],
            discovered_via=DiscoverySource.FORWARD,
        )
        await mark_channel(
            session,
            FIRST,
            status=ChannelStatus.SEED,
            kind=ChannelKind.PERSONAL,
        )
    client = client_for({FIRST: old_history(7)})

    await watch(client, database, max_cycles=10)

    assert await walk_state(database, FIRST) == ("complete", DEPTH, None, None)
    assert await held(database, FIRST) == 7


async def test_a_seed_added_with_its_review_is_walked(
    database: Database, walking: None
) -> None:
    """Through what `itgraph add --seed` calls: its own review, not `mark`."""
    username = f"example_{FIRST}"
    client = FakeTelegramClient(
        entities={username: tl_channel(FIRST, username=username)},
        histories={FIRST: old_history(7)},
    )
    await add_channels(
        client,
        database,
        usernames=[username],
        review=Review(status=ChannelStatus.SEED, kind=ChannelKind.PERSONAL),
        delay=0,
    )
    resolved_by_add = list(client.resolved)

    await watch(client, database, max_cycles=10)

    assert await walk_state(database, FIRST) == ("complete", DEPTH, None, None)
    assert await held(database, FIRST) == 7
    # The loop resolved nothing of its own.
    assert client.resolved == resolved_by_add


async def test_due_polls_come_before_any_page(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    await seed(database, SECOND)
    client = client_for(
        {FIRST: old_history(7), SECOND: [fresh(20, 5, views=1)]}
    )

    await watch(client, database, max_cycles=10)

    first_page = client.windows.index(pages(client)[0])
    assert all(window[2] == POLL for window in client.windows[:first_page])
    assert {window[0] for window in client.windows[:first_page]} == {
        FIRST,
        SECOND,
    }


async def test_one_page_a_cycle_then_the_polls_again(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    client = client_for({FIRST: old_history(9)})

    # The first cycle polls; each after it asks for one page at most.
    stats = await watch(client, database, max_cycles=3)

    assert len(polls(client)) == 1
    assert len(pages(client)) == 2
    assert stats.walked == 2


async def test_an_empty_queue_and_a_seed_to_walk(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_poll_state (channel_id, due_at) "
                "VALUES (:id, now() + interval '1 hour')"
            ),
            {"id": FIRST},
        )
    client = client_for({FIRST: old_history(9)})

    await watch(client, database, max_cycles=1)

    assert client.windows == [(FIRST, 0, PAGE)]


async def test_no_page_in_quiet_hours(
    database: Database, walking: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(database, FIRST, walked=False)
    monkeypatch.setattr(watch_module, "in_quiet_hours", lambda moment: True)
    client = client_for({FIRST: old_history(7)})

    await watch(client, database, max_cycles=5)

    assert client.windows == []


async def test_a_completed_walk_is_not_repeated(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST)  # walked, to a later depth than DEPTH
    async with database.session() as session:
        await record_complete(session, FIRST, datetime(2026, 6, 1, tzinfo=UTC))
    client = client_for({FIRST: old_history(7)})

    await watch(client, database, max_cycles=5)

    assert pages(client) == []


async def test_the_ceiling_ends_a_walk_at_its_oldest_post(
    database: Database, walking: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "backfill_max_messages", 4)
    monkeypatch.setattr(settings, "watch_window_min", 1)
    monkeypatch.setattr(settings, "watch_window_max", 1)
    await seed(database, FIRST, walked=False)
    history_ = old_history(10)
    client = client_for({FIRST: history_})

    await watch(client, database, max_cycles=10)

    assert await held(database, FIRST) == 4
    status, cutoff, _, _ = await walk_state(database, FIRST)
    assert status == "complete"
    assert cutoff == history_[3].date


async def test_a_seed_already_at_its_ceiling_is_closed_where_its_rows_end(
    database: Database, walking: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poll filled the ceiling first: nothing to ask, nothing to claim."""
    monkeypatch.setattr(settings, "backfill_max_messages", 3)
    await seed(database, FIRST, walked=False)
    history_ = old_history(5)
    client = client_for({FIRST: history_})

    await watch(client, database, max_cycles=5)

    assert pages(client) == []
    status, cutoff, _, _ = await walk_state(database, FIRST)
    assert status == "complete"
    assert cutoff == history_[-1].date


async def test_a_restart_resumes_the_walk_from_its_cursor(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    before = client_for({FIRST: old_history(9)})
    await watch(before, database, max_cycles=2)  # a poll, then one page
    [(_, _, _)] = pages(before)

    after = client_for({FIRST: old_history(9)})
    await watch(after, database, max_cycles=3)

    # The poll took 1000..991 and the first page re-read 1000..998; the
    # restarted walk carries on below 998 rather than from the top.
    assert pages(after)[0][1] == 998


async def test_a_seed_rejected_mid_walk_gets_no_further_page(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    client = client_for({FIRST: old_history(9)})
    await watch(client, database, max_cycles=2)
    walked = len(pages(client))
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE tg_channels SET status = 'rejected', "
                "reject_reason = 'not_it' WHERE tg_id = :id"
            ),
            {"id": FIRST},
        )

    await watch(client, database, max_cycles=5)

    assert len(pages(client)) == walked


# --- a walk that could not run ------------------------------------------


class Clock:
    """The walk's clock, moved by hand."""

    def __init__(self) -> None:
        self.at = 0.0

    def __call__(self) -> float:
        return self.at


async def walk_once(
    client: FakeTelegramClient,
    database: Database,
    walker: Any,
    stats: Any,
) -> Any:
    from itgraph.db.models import CollectionCommand
    from itgraph.tg.floods import FloodRecorder

    return await watch_module._walk_once(
        client,
        database,
        walker,
        stats=stats,
        recorder=FloodRecorder(database, CollectionCommand.WATCH),
        now=datetime.now(UTC),
    )


async def test_a_seed_the_session_does_not_know_waits_an_hour(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, SECOND, walked=False)
    # The session knows SECOND only.
    client = client_for({SECOND: old_history(5)})
    clock = Clock()
    walker = watch_module._Walker(clock=clock)
    stats = watch_module.WatchStats()

    await walk_once(client, database, walker, stats)
    assert (await walk_state(database, FIRST))[0] == "skipped"
    assert (await walk_state(database, FIRST))[3] == "no cached peer"

    # Within the hour FIRST is left alone and SECOND is walked meanwhile.
    clock.at = 3599
    for _ in range(4):
        await walk_once(client, database, walker, stats)
    assert {window[0] for window in client.windows} == {SECOND}
    assert stats.walks_set_aside == 1

    clock.at = 3601
    for _ in range(3):
        await walk_once(client, database, walker, stats)
    assert stats.walks_set_aside == 2
    assert client.resolved == []


async def test_a_transient_failure_is_tried_again_after_an_hour(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    client = client_for(
        {FIRST: old_history(5)},
        raises_for={FIRST: ConnectionError("connection reset")},
    )
    clock = Clock()
    walker = watch_module._Walker(clock=clock)
    stats = watch_module.WatchStats()

    await walk_once(client, database, walker, stats)
    status, _, kind, _ = await walk_state(database, FIRST)
    assert (status, kind) == ("failed", "transient")

    clock.at = 1800
    await walk_once(client, database, walker, stats)
    assert len(client.windows) == 1

    clock.at = 3601
    client.raises_for.pop(FIRST)
    await walk_once(client, database, walker, stats)
    assert len(client.windows) == 2
    assert (await walk_state(database, FIRST))[0] == "running"


async def test_a_permanent_failure_ends_walk_and_polls(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    client = client_for(
        {FIRST: old_history(5)},
        raises_for={FIRST: ChannelPrivateError(request=None)},
    )
    walker = watch_module._Walker(clock=Clock())

    await walk_once(client, database, walker, watch_module.WatchStats())

    status, _, kind, _ = await walk_state(database, FIRST)
    assert (status, kind) == ("failed", "permanent")
    async with database.session() as session:
        due = await due_channels(session, now=datetime.now(UTC))
    assert FIRST not in {channel.tg_id for channel in due}


# --- a rate limit -------------------------------------------------------


async def test_a_long_rate_limit_stops_the_walk_with_the_polls(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, walked=False)
    seconds = int(settings.flood_abort_threshold) + 60
    # Window 0 is FIRST's poll; window 1 is the walk's first page.
    client = client_for({FIRST: old_history(9)}, flood_on_window={1: seconds})

    stats = await watch(client, database, max_cycles=6)

    assert stats.postponed == 1
    assert len(client.windows) == 2
    async with database.session() as session:
        soonest = await session.scalar(
            text("SELECT min(due_at) FROM tg_poll_state")
        )
        commands = (
            await session.scalars(text("SELECT command FROM tg_flood_events"))
        ).all()
    assert soonest > datetime.now(UTC) + timedelta(seconds=seconds - 120)
    assert list(commands) == ["watch"]


# --- the turn at resolution -------------------------------------------------

RISING = 2000000001
OTHER_RISING = 2000000002
THIRD = 1000000003


async def rising_names(
    database: Database, *names: str, sources: tuple[int, ...] = (FIRST, SECOND)
) -> None:
    """Pending names each mentioned yesterday by the given seeds."""
    yesterday = datetime.now(UTC) - timedelta(days=1)
    async with database.session() as session:
        await add_pending_mentions(
            session,
            [
                MentionSource(seed_id, name, yesterday)
                for name in names
                for seed_id in sources
            ],
        )
        await session.commit()


def rising_client(**kwargs: Any) -> FakeTelegramClient:
    """Three quiet seeds, and the channels the names resolve to."""
    return FakeTelegramClient(
        entities={
            **{
                f"example_{tg_id}": FakeChannel(tg_id, f"example_{tg_id}")
                for tg_id in (FIRST, SECOND, THIRD)
            },
            "fake_head": tl_channel(RISING, username="fake_head"),
            "fake_next": tl_channel(OTHER_RISING, username="fake_next"),
        },
        histories={FIRST: [], SECOND: [], THIRD: []},
        **kwargs,
    )


async def journal(database: Database) -> list[tuple[str, str, str]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT command::text, kind::text, outcome::text "
                "FROM tg_resolutions ORDER BY id"
            )
        )
        return [(row[0], row[1], row[2]) for row in rows]


async def journal_rows(
    database: Database,
    command: CollectionCommand,
    count: int,
    *,
    ago: timedelta,
) -> None:
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_resolutions (requested_at, command, kind, outcome) "
                "SELECT now() - CAST(:ago AS interval), "
                "CAST(:command AS tg_collection_command), "
                "'id', 'channel' FROM generate_series(1, :count)"
            ),
            {"ago": ago, "command": command.value, "count": count},
        )
        await session.commit()


@pytest.fixture
def no_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap off, so a test reaches the guard it is about."""
    monkeypatch.setattr(settings, "rising_resolve_gap_minutes", 0.0)


async def test_the_head_is_resolved_and_the_second_waits_for_the_gap(
    database: Database,
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")
    await rising_names(database, "fake_next", sources=(FIRST, SECOND))
    # One more family for the head, so the order is not a tie.
    await seed(database, THIRD)
    await rising_names(database, "fake_head", sources=(THIRD,))
    client = rising_client()

    stats = await watch(client, database, max_cycles=10)

    assert client.resolved == ["fake_head"]
    assert stats.resolutions == 1
    assert await journal(database) == [("watch", "username", "channel")]
    assert client.requests == []


async def test_a_name_that_is_a_channel_becomes_a_candidate(
    database: Database,
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")

    await watch(rising_client(), database, max_cycles=5)

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status::text, discovered_via::text, username "
                    "FROM tg_channels WHERE tg_id = :id"
                ),
                {"id": RISING},
            )
        ).one()
        pending = await session.scalar(
            text("SELECT count(*) FROM tg_pending_mentions")
        )
    assert tuple(row) == ("candidate", "mention", "fake_head")
    assert pending == 0


async def test_polls_and_walks_come_before_any_resolution(
    database: Database, walking: None
) -> None:
    await seed(database, FIRST, SECOND)
    await seed(database, THIRD, walked=False)
    await rising_names(database, "fake_head")
    client = rising_client()
    client.histories[THIRD] = old_history(4)

    # A poll cycle, then the walk's two pages: nothing resolved yet.
    await watch(client, database, max_cycles=3)
    assert client.resolved == []
    assert len(pages(client)) == 2

    # The walk is complete; the next idle turn is resolution's.
    await watch(client, database, max_cycles=3)
    assert client.resolved == ["fake_head"]


async def test_nothing_that_is_not_rising_is_resolved(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    # One family only, and one mentioned before the window.
    await rising_names(database, "fake_head", sources=(FIRST,))
    async with database.session() as session:
        await add_pending_mentions(
            session,
            [
                MentionSource(seed_id, "fake_next", NOW - timedelta(days=90))
                for seed_id in (FIRST, SECOND)
            ],
        )
        await session.commit()
    client = rising_client()

    stats = await watch(client, database, max_cycles=20)

    assert client.resolved == []
    assert stats.resolutions == 0


async def test_a_failed_resolution_is_not_tried_again(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_missing")
    client = rising_client()

    await watch(client, database, max_cycles=10)

    assert client.resolved == ["fake_missing"]
    assert await journal(database) == [("watch", "username", "failed")]


async def test_a_rising_channel_is_resolved_by_id(
    database: Database,
) -> None:
    await seed(database, FIRST, SECOND)
    yesterday = datetime.now(UTC) - timedelta(days=1)
    async with database.session() as session:
        await create_discovered_channels(
            session, tg_ids=[RISING], discovered_via=DiscoverySource.FORWARD
        )
        await insert_edges(
            session,
            [
                EdgeRow(src, RISING, EdgeKind.FORWARD, msg_id, yesterday)
                for msg_id, src in enumerate((FIRST, SECOND), start=1)
            ],
        )
        await session.commit()
    client = rising_client(
        entities_by_id={RISING: tl_channel(RISING, username="fake_head")}
    )

    await watch(client, database, max_cycles=5)

    assert client.resolved == [RISING]
    assert await journal(database) == [("watch", "id", "channel")]


# --- the cap -------------------------------------------------------------


async def test_the_loop_stops_at_the_cap(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")
    await journal_rows(
        database, CollectionCommand.WATCH, 10, ago=timedelta(hours=23)
    )
    client = rising_client()

    await watch(client, database, max_cycles=10)
    assert client.resolved == []

    # Once the oldest is more than a day old, there is room again.
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE tg_resolutions SET requested_at = "
                "requested_at - interval '2 hours'"
            )
        )
        await session.commit()
    await watch(client, database, max_cycles=10)
    assert client.resolved == ["fake_head"]


async def test_a_manual_run_spends_the_loop_s_share(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")
    await journal_rows(
        database, CollectionCommand.RESOLVE, 50, ago=timedelta(hours=1)
    )
    client = rising_client()

    await watch(client, database, max_cycles=10)

    assert client.resolved == []


async def test_the_operator_is_not_stopped_by_the_cap(
    database: Database,
) -> None:
    await journal_rows(
        database, CollectionCommand.WATCH, 10, ago=timedelta(hours=1)
    )
    client = rising_client()

    summary = await add_channels(
        client, database, usernames=["fake_head"], delay=0
    )

    assert summary.added == 1
    assert client.resolved == ["fake_head"]


async def test_a_loop_with_rising_candidates_stays_within_the_cap(
    database: Database, no_gap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "resolution_cap", 2)
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head", "fake_next", "fake_third")
    client = rising_client()

    await watch(client, database, max_cycles=30)

    assert sorted(client.resolved) == ["fake_head", "fake_next"]
    assert client.requests == []


# --- a rate limit on resolution ---------------------------------------


async def test_a_day_s_wait_holds_resolution_alone(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head", "fake_next")
    client = rising_client(
        resolve_floods={"fake_head": 86400},
        flood_request=ResolveUsernameRequest(username="fake_head"),
    )

    stats = await watch(client, database, max_cycles=20)

    # Asked once, never slept off, never asked again.
    assert client.resolved == ["fake_head"]
    assert stats.resolution_holds == 1
    assert stats.postponed == 0
    async with database.session() as session:
        event = (
            await session.execute(
                text(
                    "SELECT method, seconds, command::text, halted "
                    "FROM tg_flood_events"
                )
            )
        ).one()
        # Polls were not pushed past the wait.
        latest_due = await session.scalar(
            text("SELECT max(due_at) FROM tg_poll_state")
        )
    assert tuple(event) == ("ResolveUsernameRequest", 86400, "watch", True)
    assert latest_due < datetime.now(UTC) + timedelta(hours=12)
    # The name stays as it was, for after the wait.
    assert await journal(database) == [("watch", "username", "failed")]
    async with database.session() as session:
        attempts = await session.scalar(
            text(
                "SELECT attempts FROM tg_pending_mentions "
                "WHERE username = 'fake_head'"
            )
        )
    assert attempts == 0


async def test_polls_go_on_through_the_wait(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")
    client = rising_client(
        resolve_floods={"fake_head": 86400},
        flood_request=ResolveUsernameRequest(username="fake_head"),
    )
    await watch(client, database, max_cycles=5)
    polled = len(client.windows)

    await due_again(database)
    await watch(client, database, max_cycles=5)

    assert len(client.windows) > polled
    assert client.resolved == ["fake_head"]


async def test_a_restart_inside_the_wait_resolves_nothing(
    database: Database, no_gap: None
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")
    async with database.session() as session:
        # Met by the operator's run: the limit is the account's.
        await store_flood_event(
            session,
            method="GetChannelsRequest",
            seconds=86400,
            command=CollectionCommand.RESOLVE,
            channel_id=None,
            halted=True,
        )
        await session.commit()
    client = rising_client()

    await watch(client, database, max_cycles=10)

    assert client.resolved == []


async def test_a_resolution_that_never_answers_drops_the_connection(
    database: Database, no_gap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head")
    client = rising_client()

    async def never_answers(ref: Any) -> Any:
        client.resolved.append(ref)
        raise RequestTimedOut(settings.request_timeout_seconds)

    monkeypatch.setattr(client, "get_entity", never_answers)

    stats = await watch(client, database, max_cycles=5)

    assert client.resolved == ["fake_head"]
    assert client.disconnects >= 1
    assert stats.timed_out == 1


async def test_a_name_is_never_logged_by_the_loop(
    database: Database, no_gap: None, caplog: pytest.LogCaptureFixture
) -> None:
    await seed(database, FIRST, SECOND, THIRD)
    # The failure first, with a family more; then the wait.
    await rising_names(
        database, "fake_missing", sources=(FIRST, SECOND, THIRD)
    )
    await rising_names(database, "fake_head")
    client = rising_client(
        resolve_floods={"fake_head": 86400},
        flood_request=ResolveUsernameRequest(username="fake_head"),
    )

    with caplog.at_level(logging.DEBUG):
        await watch(client, database, max_cycles=10)

    assert sorted(client.resolved) == ["fake_head", "fake_missing"]
    assert "fake_missing" not in caplog.text
    assert "fake_head" not in caplog.text


async def test_the_loop_adds_no_wait_of_its_own_after_a_resolution(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap is the journal's alone: with it answered, the next head is
    asked for at the next idle tick, not a gap later again."""
    from itgraph.tg import rising as rising_module

    async def never_resolved(*args: Any) -> None:
        return None

    monkeypatch.setattr(rising_module, "newest_resolution", never_resolved)
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head", "fake_next")
    client = rising_client()

    await watch(client, database, max_cycles=10, walk_clock=lambda: 0.0)

    assert sorted(client.resolved) == ["fake_head", "fake_next"]


async def test_a_ranking_with_nothing_awaiting_is_not_read_every_tick(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.tg import rising as rising_module

    loads = 0
    original = rising_module.load_rising_rows

    async def counted(session: Any) -> Any:
        nonlocal loads
        loads += 1
        return await original(session)

    monkeypatch.setattr(rising_module, "load_rising_rows", counted)
    await seed(database, FIRST, SECOND)

    await watch(
        rising_client(), database, max_cycles=10, walk_clock=lambda: 0.0
    )

    assert loads == 1


async def test_a_wait_naming_no_request_still_holds_resolution(
    database: Database, no_gap: None
) -> None:
    """Recorded as `unknown`, which the record's question does not ask
    about: the loop holds it in memory to its end all the same."""
    await seed(database, FIRST, SECOND)
    await rising_names(database, "fake_head", "fake_next")
    client = rising_client(resolve_floods={"fake_head": 86400})

    stats = await watch(client, database, max_cycles=20)

    assert client.resolved == ["fake_head"]
    assert stats.resolution_holds == 1
    async with database.session() as session:
        method = await session.scalar(
            text("SELECT method FROM tg_flood_events")
        )
    assert method == "unknown"
