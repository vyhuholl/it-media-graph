"""Writing metric snapshots, against a real Postgres.

The database is part of the subject here rather than scenery: two of the
guarantees — that a snapshot cannot exist without its message, and that
an earlier reading is never rewritten — are enforced by the schema, and a
test against a fake would prove nothing about either.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.metrics import (
    count_snapshots,
    latest_observation,
    store_metrics,
)
from itgraph.db.models import DiscoverySource
from itgraph.db.raw import store_messages
from itgraph.db.session import Database
from itgraph.derive.metrics import Counters

CHANNEL = 1000000001
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


async def seed(database: Database, *msg_ids: int) -> None:
    """A channel and some collected messages to hang snapshots on."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=CHANNEL,
                    username="example",
                    title="Example",
                    is_chat=False,
                )
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await store_messages(
            session,
            channel_id=CHANNEL,
            payloads={
                msg_id: {"_": "Message", "id": msg_id} for msg_id in msg_ids
            },
        )


async def rows(database: Database) -> list[tuple[int, int, int, int]]:
    async with database.session() as session:
        result = await session.execute(
            text(
                "SELECT msg_id, views, forwards, comments "
                "FROM message_metrics ORDER BY msg_id, observed_at"
            )
        )
        return [tuple(row) for row in result.all()]  # type: ignore[misc]


async def test_a_batch_is_stored_at_one_moment(database: Database) -> None:
    """One history response was read at one moment, so one timestamp.

    Stamping the rows individually would invent a spread the measurement
    does not have.
    """
    await seed(database, 10, 11)

    async with database.session() as session:
        written = await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={
                10: Counters(views=100, forwards=2, comments=0),
                11: Counters(views=50, forwards=0, comments=3),
            },
        )

    assert written == 2
    async with database.session() as session:
        moments = await session.execute(
            text("SELECT DISTINCT observed_at FROM message_metrics")
        )
        assert len(moments.all()) == 1


async def test_a_second_observation_is_a_second_row(
    database: Database,
) -> None:
    await seed(database, 10)

    async with database.session() as session:
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={10: Counters(views=100)},
        )
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW + timedelta(minutes=15),
            counters={10: Counters(views=180)},
        )

    assert await rows(database) == [
        (10, 100, None, None),
        (10, 180, None, None),
    ]


async def test_an_earlier_snapshot_is_never_rewritten(
    database: Database,
) -> None:
    """Append-only. A correction would be inventing a different moment."""
    await seed(database, 10)

    async with database.session() as session:
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={10: Counters(views=100)},
        )
        # The same moment again, with a different number.
        written = await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={10: Counters(views=999)},
        )

    assert written == 0
    assert await rows(database) == [(10, 100, None, None)]


async def test_a_snapshot_needs_the_message_it_describes(
    database: Database,
) -> None:
    """The foreign key is what pins payload-first, snapshot-second.

    Without it, a poll that died between the two writes would leave a
    reading of a post nothing else in the database has ever seen.
    """
    await seed(database)

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await store_metrics(
                session,
                channel_id=CHANNEL,
                observed_at=NOW,
                counters={404: Counters(views=1)},
            )


async def test_absent_and_zero_survive_the_round_trip(
    database: Database,
) -> None:
    """The distinction the baselines depend on has to reach the table.

    A channel with reactions switched off and a post nobody reacted to
    are different facts, and a column that stored both as 0 would lose
    the one that matters.
    """
    await seed(database, 10, 11)

    async with database.session() as session:
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={
                10: Counters(views=1, reactions=None, comments=None),
                11: Counters(views=1, reactions={}, comments=0),
            },
        )

    async with database.session() as session:
        result = await session.execute(
            text(
                "SELECT msg_id, reactions, comments "
                "FROM message_metrics ORDER BY msg_id"
            )
        )
        assert [tuple(row) for row in result.all()] == [
            (10, None, None),
            (11, {}, 0),
        ]


async def test_reactions_keep_their_emoji(database: Database) -> None:
    await seed(database, 10)

    async with database.session() as session:
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={10: Counters(reactions={"👍": 30, "🤡": 9})},
        )

    async with database.session() as session:
        stored = await session.scalar(
            text("SELECT reactions FROM message_metrics")
        )
    assert stored == {"👍": 30, "🤡": 9}


async def test_an_empty_batch_writes_nothing(database: Database) -> None:
    async with database.session() as session:
        assert (
            await store_metrics(
                session, channel_id=CHANNEL, observed_at=NOW, counters={}
            )
            == 0
        )


async def test_snapshots_can_be_counted_since_a_moment(
    database: Database,
) -> None:
    await seed(database, 10)

    async with database.session() as session:
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={10: Counters(views=1)},
        )
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW + timedelta(hours=2),
            counters={10: Counters(views=2)},
        )

    async with database.session() as session:
        assert await count_snapshots(session) == 2
        assert (
            await count_snapshots(session, since=NOW + timedelta(hours=1)) == 1
        )


async def test_the_last_reading_of_a_message_is_findable(
    database: Database,
) -> None:
    await seed(database, 10)
    later = NOW + timedelta(hours=1)

    async with database.session() as session:
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=NOW,
            counters={10: Counters(views=1)},
        )
        await store_metrics(
            session,
            channel_id=CHANNEL,
            observed_at=later,
            counters={10: Counters(views=2)},
        )

    async with database.session() as session:
        assert (
            await latest_observation(session, channel_id=CHANNEL, msg_id=10)
            == later
        )
        assert (
            await latest_observation(session, channel_id=CHANNEL, msg_id=99)
            is None
        )
