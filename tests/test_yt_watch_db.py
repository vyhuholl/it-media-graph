"""The poll loop's own records: what is owed, what failed, and the lock.

The lock tests run against the real test database, because what is
being tested is Postgres's behaviour — that a held lock refuses, that a
dropped connection releases, and that the poll loop's key and the
Telegram session lease's live in spaces that never meet.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from ytfakes import channel_id, channel_resource

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtChannelPoll,
    YtDiscoveryPath,
    YtLang,
    YtPassOutcome,
)
from itgraph.db.session import Database
from itgraph.db.session_lease import SessionLease
from itgraph.db.yt_channels import create_channels, mark_gone, store_payloads
from itgraph.db.yt_watch import (
    WATCH_LOCK_KEY,
    PassRecord,
    WatchBusyError,
    channels_to_list,
    last_pass,
    owe_listings,
    record_listing_failure,
    record_listings,
    record_pass,
    stored_video_counts,
    watch_lock,
    watched_channels,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


async def _seeds(database: Database, *numbers: int) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in numbers])
        await store_payloads(
            session,
            {channel_id(n): channel_resource(n) for n in numbers},
            fetched_at=NOW,
        )
        for n in numbers:
            record = await session.get(YtChannel, channel_id(n))
            assert record is not None
            record.status = ChannelStatus.SEED
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN


async def _to_list(
    database: Database, *, now: datetime = NOW, checks: bool = True
) -> tuple[dict[str, YtDiscoveryPath], int]:
    async with database.session() as session:
        listing, deferred = await channels_to_list(
            session, now=now, checks_allowed=checks
        )
    return {item.channel_id: item.reason for item in listing}, deferred


async def test_a_held_lock_refuses_a_second_pass(database: Database) -> None:
    async with watch_lock(database):
        with pytest.raises(WatchBusyError):
            async with watch_lock(database):
                pass

    # Released on the way out.
    async with watch_lock(database):
        pass


async def test_a_dropped_connection_releases_the_lock(
    database: Database, database_url: str
) -> None:
    """A pass killed part way takes its lock down with its connection."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        connection = await engine.connect()
        taken = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": WATCH_LOCK_KEY}
        )
        assert taken
        await connection.close()
    finally:
        await engine.dispose()

    async with watch_lock(database):
        pass


async def test_the_lock_and_the_telegram_lease_never_meet(
    database: Database, database_url: str, tmp_path: Path
) -> None:
    lease = SessionLease(
        "test", url=database_url, session_path=tmp_path / "fake.session"
    )
    await lease.acquire()
    try:
        async with watch_lock(database):
            pass
    finally:
        await lease.release()

    async with watch_lock(database):
        other = SessionLease(
            "test", url=database_url, session_path=tmp_path / "fake.session"
        )
        await other.acquire()
        await other.release()


async def test_only_seeds_are_watched(database: Database) -> None:
    await _seeds(database, 1, 2)
    async with database.session() as session:
        await create_channels(session, [channel_id(3)])
        await mark_gone(session, [channel_id(2)], now=NOW)
        watched = await watched_channels(session)

    assert [(w.channel_id, w.gone) for w in watched] == [
        (channel_id(1), False),
        (channel_id(2), True),
    ]


async def test_stored_counts_are_read_before_they_are_replaced(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        counts = await stored_video_counts(
            session, [channel_id(1), channel_id(9)]
        )

    assert counts == {channel_id(1): "42"}


async def test_a_never_listed_channel_is_listed_first(
    database: Database,
) -> None:
    await _seeds(database, 1)

    listing, deferred = await _to_list(database)

    assert listing == {channel_id(1): YtDiscoveryPath.FIRST}
    assert deferred == 0


async def test_an_owed_listing_is_listed_and_cleared_by_success(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await record_listings(session, [channel_id(1)], now=NOW)
        await owe_listings(session, [channel_id(1)])

    later = NOW + timedelta(hours=1)
    assert (await _to_list(database, now=later))[0] == {
        channel_id(1): YtDiscoveryPath.COUNT
    }

    async with database.session() as session:
        await record_listings(session, [channel_id(1)], now=later)
    assert (await _to_list(database, now=later))[0] == {}


async def test_the_daily_check_is_due_a_day_after_the_last_listing(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await record_listings(session, [channel_id(1)], now=NOW)

    assert (await _to_list(database, now=NOW + timedelta(hours=23)))[0] == {}
    assert (await _to_list(database, now=NOW + timedelta(hours=24)))[0] == {
        channel_id(1): YtDiscoveryPath.CHECK
    }


async def test_a_check_ahead_of_pace_is_deferred_and_counted(
    database: Database,
) -> None:
    await _seeds(database, 1, 2)
    async with database.session() as session:
        await record_listings(session, [channel_id(1)], now=NOW)

    listing, deferred = await _to_list(
        database, now=NOW + timedelta(days=1), checks=False
    )

    # The first listing of channel 2 is never deferred.
    assert listing == {channel_id(2): YtDiscoveryPath.FIRST}
    assert deferred == 1


async def test_a_failing_channel_waits_out_its_backoff(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await record_listing_failure(
            session, channel_id(1), now=NOW, error="404 playlistNotFound"
        )

    assert (await _to_list(database, now=NOW + timedelta(hours=1)))[0] == {}
    assert (await _to_list(database, now=NOW + timedelta(hours=2)))[0] == {
        channel_id(1): YtDiscoveryPath.FIRST
    }

    async with database.session() as session:
        await record_listing_failure(
            session,
            channel_id(1),
            now=NOW + timedelta(hours=2),
            error="404 playlistNotFound",
        )
        poll = await session.get(YtChannelPoll, channel_id(1))
    assert poll is not None and poll.consecutive_failures == 2


async def test_a_gone_channel_is_not_listed(database: Database) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await mark_gone(session, [channel_id(1)], now=NOW)

    assert (await _to_list(database))[0] == {}


async def test_the_last_pass_is_the_one_reported(database: Database) -> None:
    async with database.session() as session:
        await record_pass(session, PassRecord(started_at=NOW), finished_at=NOW)
        later = PassRecord(
            started_at=NOW + timedelta(minutes=15),
            outcome=YtPassOutcome.POOL_SPENT,
            deferred_readings=3,
        )
        await record_pass(session, later, finished_at=later.started_at)

    async with database.session() as session:
        found = await last_pass(session)
    assert found is not None
    assert found.outcome is YtPassOutcome.POOL_SPENT
    assert found.deferred_readings == 3
