"""The poll queue against a real Postgres.

The arithmetic is tested in ``test_schedule.py``; what is tested here is
the part only a database can answer — who is due, what counts as live,
and that a channel nothing has ever polled is not invisible.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import (
    BackfillStatus,
    ChannelStatus,
    DiscoverySource,
    FailureKind,
)
from itgraph.db.poll import (
    count_overdue,
    due_channels,
    live_post_dates,
    measure_posts_per_day,
    queue_lag,
    record_poll,
)
from itgraph.db.raw import store_messages
from itgraph.db.session import Database

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
FIRST = 1000000001
SECOND = 1000000002


async def seed_channels(database: Database, *tg_ids: int) -> None:
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


async def seed_posts(
    database: Database, channel_id: int, *published: datetime
) -> None:
    async with database.session() as session:
        await store_messages(
            session,
            channel_id=channel_id,
            payloads={
                index: {
                    "_": "Message",
                    "id": index,
                    "date": moment.isoformat(),
                }
                for index, moment in enumerate(published, start=1)
            },
        )


async def test_a_channel_with_no_state_is_due(database: Database) -> None:
    """This is what makes the queue seed itself.

    The first pass over the inventory is the seeding pass; nothing has to
    backfill `poll_state`.
    """
    await seed_channels(database, FIRST)

    async with database.session() as session:
        due = await due_channels(session, now=NOW)

    assert [channel.tg_id for channel in due] == [FIRST]
    assert due[0].username == "example_1000000001"


async def test_a_channel_not_yet_due_is_left_alone(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)

    async with database.session() as session:
        await record_poll(
            session,
            FIRST,
            due_at=NOW + timedelta(hours=1),
            polled_at=NOW,
        )
    async with database.session() as session:
        assert await due_channels(session, now=NOW) == []
        assert await due_channels(session, now=NOW + timedelta(hours=2))


async def test_the_most_overdue_channel_comes_first(
    database: Database,
) -> None:
    """A loop that cannot keep up should work on what has waited longest."""
    await seed_channels(database, FIRST, SECOND)

    async with database.session() as session:
        await record_poll(
            session,
            FIRST,
            due_at=NOW - timedelta(minutes=5),
            polled_at=NOW - timedelta(hours=1),
        )
        await record_poll(
            session,
            SECOND,
            due_at=NOW - timedelta(hours=3),
            polled_at=NOW - timedelta(hours=4),
        )

    async with database.session() as session:
        due = await due_channels(session, now=NOW)

    assert [channel.tg_id for channel in due] == [SECOND, FIRST]


async def test_only_channels_in_scope_are_polled(
    database: Database,
) -> None:
    """The same predicate the history walk uses, not a second copy of it."""
    await seed_channels(database, FIRST, SECOND)
    async with database.session() as session:
        await session.execute(
            text("UPDATE channels SET status = 'candidate' WHERE tg_id = :id"),
            {"id": SECOND},
        )

    async with database.session() as session:
        due = await due_channels(session, now=NOW)

    assert [channel.tg_id for channel in due] == [FIRST]


async def test_a_permanently_failed_channel_is_not_polled(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO backfill_state "
                "(channel_id, status, failure_kind) "
                "VALUES (:id, :status, :kind)"
            ),
            {
                "id": FIRST,
                "status": BackfillStatus.FAILED.value,
                "kind": FailureKind.PERMANENT.value,
            },
        )

    async with database.session() as session:
        assert await due_channels(session, now=NOW) == []


async def test_live_posts_are_the_ones_inside_the_horizon(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)
    await seed_posts(
        database,
        FIRST,
        NOW - timedelta(hours=1),
        NOW - timedelta(hours=20),
        NOW - timedelta(days=9),
    )

    async with database.session() as session:
        live = await live_post_dates(session, channel_id=FIRST, now=NOW)

    assert live == [NOW - timedelta(hours=1), NOW - timedelta(hours=20)]


async def test_a_service_message_is_not_a_live_post(
    database: Database,
) -> None:
    """It measures nothing, so it must not keep a channel on the dense path."""
    await seed_channels(database, FIRST)
    async with database.session() as session:
        await store_messages(
            session,
            channel_id=FIRST,
            payloads={
                1: {
                    "_": "MessageService",
                    "id": 1,
                    "date": (NOW - timedelta(hours=1)).isoformat(),
                }
            },
        )

    async with database.session() as session:
        assert await live_post_dates(session, channel_id=FIRST, now=NOW) == []


async def test_the_posting_rate_is_measured_from_the_rows(
    database: Database,
) -> None:
    """Counted, never accumulated — the rows are the only honest answer."""
    await seed_channels(database, FIRST)
    await seed_posts(
        database,
        FIRST,
        *[NOW - timedelta(days=day) for day in range(15)],
    )

    async with database.session() as session:
        rate = await measure_posts_per_day(session, channel_id=FIRST, now=NOW)

    assert rate == 15 / 30


async def test_posts_outside_the_window_do_not_count(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)
    await seed_posts(database, FIRST, NOW - timedelta(days=100))

    async with database.session() as session:
        assert (
            await measure_posts_per_day(session, channel_id=FIRST, now=NOW)
            == 0
        )


async def test_an_empty_poll_raises_the_empty_count(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)

    async with database.session() as session:
        for _ in range(3):
            await record_poll(
                session,
                FIRST,
                due_at=NOW,
                polled_at=NOW,
                found_nothing=True,
            )
    async with database.session() as session:
        due = await due_channels(session, now=NOW)
    assert due[0].consecutive_empty == 3


async def test_finding_something_clears_the_empty_count(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)

    async with database.session() as session:
        await record_poll(
            session, FIRST, due_at=NOW, polled_at=NOW, found_nothing=True
        )
        await record_poll(
            session, FIRST, due_at=NOW, polled_at=NOW, found_nothing=False
        )

    async with database.session() as session:
        due = await due_channels(session, now=NOW)
    assert due[0].consecutive_empty == 0


async def test_a_failure_is_counted_separately_from_silence(
    database: Database,
) -> None:
    """Reachable-and-quiet is a different fact from unreachable."""
    await seed_channels(database, FIRST)

    async with database.session() as session:
        await record_poll(
            session, FIRST, due_at=NOW, polled_at=NOW, error="timeout"
        )
        await record_poll(
            session, FIRST, due_at=NOW, polled_at=NOW, error="timeout"
        )

    async with database.session() as session:
        due = await due_channels(session, now=NOW)
    assert due[0].consecutive_failures == 2
    assert due[0].consecutive_empty == 0


async def test_a_successful_poll_clears_the_failures(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)

    async with database.session() as session:
        await record_poll(
            session, FIRST, due_at=NOW, polled_at=NOW, error="timeout"
        )
        await record_poll(session, FIRST, due_at=NOW, polled_at=NOW)

    async with database.session() as session:
        due = await due_channels(session, now=NOW)
    assert due[0].consecutive_failures == 0
    assert due[0].last_error is None


async def test_the_cursor_is_read_in_place(database: Database) -> None:
    """`newest_fetched_id` stays on `backfill_state`, uncopied.

    One fact, one table — a second copy on `poll_state` is a thing that
    could disagree.
    """
    await seed_channels(database, FIRST)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO backfill_state (channel_id, newest_fetched_id) "
                "VALUES (:id, 4242)"
            ),
            {"id": FIRST},
        )

    async with database.session() as session:
        due = await due_channels(session, now=NOW)

    assert due[0].newest_fetched_id == 4242


async def test_the_queue_reports_its_lag(database: Database) -> None:
    await seed_channels(database, FIRST, SECOND)

    async with database.session() as session:
        await record_poll(
            session,
            FIRST,
            due_at=NOW - timedelta(hours=2),
            polled_at=NOW - timedelta(hours=3),
        )
        await record_poll(
            session,
            SECOND,
            due_at=NOW + timedelta(hours=1),
            polled_at=NOW,
        )

    async with database.session() as session:
        lag = await queue_lag(session, now=NOW)
        assert await count_overdue(session, now=NOW) == 1

    assert lag.overdue == 1
    assert lag.oldest_due_at == NOW - timedelta(hours=2)
    assert lag.tracked == 2


async def test_a_never_polled_channel_supplies_no_lag(
    database: Database,
) -> None:
    """Overdue, but not *late* — the two are different quantities.

    Reporting them as one would make a fresh install look catastrophically
    behind on its first tick.
    """
    await seed_channels(database, FIRST)

    async with database.session() as session:
        lag = await queue_lag(session, now=NOW)

    assert lag.overdue == 1
    assert lag.oldest_due_at is None
    assert lag.tracked == 0


async def test_a_limit_bounds_the_batch(database: Database) -> None:
    await seed_channels(database, FIRST, SECOND)

    async with database.session() as session:
        assert len(await due_channels(session, now=NOW, limit=1)) == 1


async def test_the_rate_is_cached_on_the_row(database: Database) -> None:
    await seed_channels(database, FIRST)

    async with database.session() as session:
        await record_poll(
            session,
            FIRST,
            due_at=NOW,
            polled_at=NOW,
            posts_per_day=2.5,
        )

    async with database.session() as session:
        due = await due_channels(session, now=NOW)
    assert due[0].posts_per_day == 2.5
    assert due[0].posts_per_day_at == NOW


async def test_a_poll_without_a_new_rate_keeps_the_cached_one(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)

    async with database.session() as session:
        await record_poll(
            session, FIRST, due_at=NOW, polled_at=NOW, posts_per_day=2.5
        )
        await record_poll(session, FIRST, due_at=NOW, polled_at=NOW)

    async with database.session() as session:
        due = await due_channels(session, now=NOW)
    assert due[0].posts_per_day == 2.5


async def test_a_discussion_chat_is_never_polled(
    database: Database,
) -> None:
    await seed_channels(database, FIRST)
    async with database.session() as session:
        await session.execute(
            text("UPDATE channels SET is_chat = true WHERE tg_id = :id"),
            {"id": FIRST},
        )

    async with database.session() as session:
        assert await due_channels(session, now=NOW) == []


async def test_status_is_the_seed_status(database: Database) -> None:
    """Guards the fixture as much as the query.

    If `upsert_channels` ever stopped landing on `candidate`, the scope
    tests above would pass for the wrong reason.
    """
    await seed_channels(database, FIRST)
    async with database.session() as session:
        status = await session.scalar(
            text("SELECT status FROM channels WHERE tg_id = :id"),
            {"id": FIRST},
        )
    assert status == ChannelStatus.SEED.value
