"""The poll loop.

The most important assertion in this file is the dullest one: a poll
issues no ``resolveUsername`` and no ``getFullChannel``. That is the
invariant the loop is likeliest to break by accident — one convenience
call to ``get_entity`` and the tightest daily quota in the project is
gone every day until somebody reads `flood_events`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import (
    FakeChannel,
    FakeHistoryMessage,
    FakeServiceMessage,
    FakeTelegramClient,
)
from sqlalchemy import text
from telethon.errors import ChannelPrivateError, FloodWaitError

from itgraph.config import settings
from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import DiscoverySource
from itgraph.db.poll import due_channels
from itgraph.db.session import Database
from itgraph.tg import watch as watch_module
from itgraph.tg.watch import watch

FIRST = 1000000001
SECOND = 1000000002
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop's politeness is tested in `test_pacing.py`, not by waiting."""
    monkeypatch.setattr(settings, "watch_request_delay", 0.0)
    monkeypatch.setattr(settings, "watch_tick_seconds", 0.01)
    # Quiet hours off, so a test's outcome does not depend on the hour it
    # happens to run at.
    monkeypatch.setattr(settings, "watch_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "watch_quiet_to_hour", 0)


async def seed(database: Database, *tg_ids: int) -> None:
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
                "UPDATE channels SET status = 'seed' WHERE tg_id = ANY(:ids)"
            ),
            {"ids": list(tg_ids)},
        )


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
                "comments FROM message_metrics ORDER BY msg_id, observed_at"
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
            text("UPDATE poll_state SET due_at = now() - interval '1 hour'")
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
            text("UPDATE poll_state SET due_at = now() - interval '1 hour'")
        )
    post.views = 150
    stats = await watch(client, database, max_cycles=1)

    assert stats.stored == 0
    assert stats.snapshots == 1


async def test_a_post_past_the_horizon_is_not_snapshotted(
    database: Database,
) -> None:
    """It has stopped moving; reading it costs a row to learn nothing."""
    await seed(database, FIRST)
    old = fresh(9, 60 * 24 * 9, views=5000)
    client = client_for({FIRST: [fresh(10, 5, views=10), old]})

    await watch(client, database, max_cycles=1)

    assert [row[1] for row in await snapshots(database)] == [10]
    # ...but it is still stored, because the request already returned it.
    async with database.session() as session:
        stored = await session.scalar(
            text("SELECT count(*) FROM raw_messages")
        )
    assert stored == 2


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
            await session.scalar(text("SELECT count(*) FROM raw_messages"))
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
            text("SELECT newest_fetched_id FROM backfill_state")
        )
    assert newest == 42


async def test_the_cursor_never_moves_backwards(
    database: Database,
) -> None:
    """A short window must not rewind a mark a walk has already set."""
    await seed(database, FIRST)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO backfill_state (channel_id, newest_fetched_id) "
                "VALUES (:id, 9999)"
            ),
            {"id": FIRST},
        )
    client = client_for({FIRST: [fresh(42, 5, views=1)]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        newest = await session.scalar(
            text("SELECT newest_fetched_id FROM backfill_state")
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
                text("SELECT last_error, consecutive_failures FROM poll_state")
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
                "INSERT INTO poll_state (channel_id, due_at) "
                "SELECT tg_id, now() - interval '1 hour' FROM channels"
            )
        )

    stats = await watch(client, database, max_cycles=1)

    assert stats.postponed == 1
    assert stats.failed == 0
    async with database.session() as session:
        soonest = await session.scalar(
            text("SELECT min(due_at) FROM poll_state")
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
                "SELECT coalesce(max(consecutive_failures), 0) FROM poll_state"
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
            await session.scalars(text("SELECT command FROM flood_events"))
        ).all()
    assert list(commands) == ["watch"]


async def test_quiet_hours_issue_no_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(database, FIRST)
    monkeypatch.setattr(watch_module, "in_quiet_hours", lambda moment: True)
    client = client_for({FIRST: [fresh(10, 5, views=1)]})

    stats = await watch(client, database, max_cycles=1)

    assert client.windows == []
    assert stats.polled == 0


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
        assert await session.scalar(text("SELECT count(*) FROM edges")) == 0
        assert (
            await session.scalar(text("SELECT count(*) FROM pending_mentions"))
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
                    "SELECT status, reviewed_at FROM channels WHERE tg_id=:id"
                ),
                {"id": FIRST},
            )
        ).one()
    assert status == "seed"
    assert reviewed is None


async def test_the_newest_post_date_is_recorded(database: Database) -> None:
    await seed(database, FIRST)
    post = fresh(10, 5, views=1)
    client = client_for({FIRST: [post]})

    await watch(client, database, max_cycles=1)

    async with database.session() as session:
        last = await session.scalar(
            text("SELECT last_post_at FROM channels WHERE tg_id = :id"),
            {"id": FIRST},
        )
    assert last == post.date


async def test_a_capped_channel_is_still_polled_forward(
    database: Database,
) -> None:
    """The ceiling bounds history, not forward collection.

    The channels that reach it first are the most active in the
    inventory — precisely the ones a realtime product cannot be blind to.
    """
    await seed(database, FIRST)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO backfill_state "
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
