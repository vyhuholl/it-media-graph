"""The history walk's own records: who is due, what a walk counted, the lock.

The lock tests run against the real test database, because what is
being tested is Postgres's behaviour — that a held lock refuses, that a
dropped connection releases, and that the walk's key, the poll loop's
and the Telegram session lease's never meet.
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
    YtBackfillWalk,
    YtChannel,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.session_lease import SessionLease
from itgraph.db.yt_backfill import (
    BACKFILL_LOCK_KEY,
    BackfillBusyError,
    PageCounts,
    backfill_lock,
    channels_to_walk,
    finish_walk,
    forget_walks,
    record_page,
    record_walk_failure,
    start_walk,
    walk_summary,
)
from itgraph.db.yt_channels import create_channels, mark_gone, store_payloads
from itgraph.db.yt_watch import WATCH_LOCK_KEY, watch_lock

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
YEAR = timedelta(days=365)


async def _seeds(
    database: Database, *numbers: int, lang: YtLang = YtLang.EN
) -> None:
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
            record.lang = lang


async def _due(
    database: Database, *, named: list[str] | None = None
) -> list[str]:
    async with database.session() as session:
        walks = await channels_to_walk(session, named=named)
    return [walk.channel_id for walk in walks]


async def _walked(database: Database, number: int, *, at: datetime) -> None:
    async with database.session() as session:
        await start_walk(
            session, channel_id(number), now=at, cutoff=at - YEAR, whole=False
        )
        await finish_walk(session, channel_id(number), now=at, capped=False)


# --- the lock -----------------------------------------------------------


async def test_a_held_lock_refuses_a_second_walk(database: Database) -> None:
    async with backfill_lock(database):
        with pytest.raises(BackfillBusyError):
            async with backfill_lock(database):
                pass

    async with backfill_lock(database):
        pass


async def test_a_dropped_connection_releases_the_lock(
    database: Database, database_url: str
) -> None:
    """A walk killed part way takes its lock down with its connection."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        connection = await engine.connect()
        taken = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": BACKFILL_LOCK_KEY},
        )
        assert taken
        await connection.close()
    finally:
        await engine.dispose()

    async with backfill_lock(database):
        pass


async def test_the_walk_and_the_poll_loop_never_refuse_each_other(
    database: Database,
) -> None:
    assert BACKFILL_LOCK_KEY != WATCH_LOCK_KEY
    async with backfill_lock(database), watch_lock(database):
        pass
    async with watch_lock(database), backfill_lock(database):
        pass


async def test_the_walk_and_the_telegram_lease_never_meet(
    database: Database, database_url: str, tmp_path: Path
) -> None:
    lease = SessionLease(
        "test", url=database_url, session_path=tmp_path / "fake.session"
    )
    await lease.acquire()
    try:
        async with backfill_lock(database):
            pass
    finally:
        await lease.release()

    async with backfill_lock(database):
        other = SessionLease(
            "test", url=database_url, session_path=tmp_path / "fake.session"
        )
        await other.acquire()
        await other.release()


# --- Walked Channels ----------------------------------------------------


async def test_seeds_of_every_language_are_due(database: Database) -> None:
    await _seeds(database, 1, lang=YtLang.EN)
    await _seeds(database, 2, lang=YtLang.RU)
    await _seeds(database, 3, lang=YtLang.OTHER)

    assert await _due(database) == [
        channel_id(1),
        channel_id(2),
        channel_id(3),
    ]


async def test_channels_outside_the_seeds_are_not_due(
    database: Database,
) -> None:
    await _seeds(database, 1, 2, 3, 4)
    async with database.session() as session:
        await create_channels(session, [channel_id(5)])
        maybe = await session.get(YtChannel, channel_id(2))
        rejected = await session.get(YtChannel, channel_id(3))
        assert maybe is not None and rejected is not None
        maybe.status = ChannelStatus.MAYBE
        rejected.status = ChannelStatus.REJECTED
        rejected.reject_reason = YtRejectReason.NOT_IT
        await mark_gone(session, [channel_id(4)], now=NOW)

    assert await _due(database) == [channel_id(1)]


async def test_the_playlist_comes_from_the_stored_payload(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await create_channels(session, [channel_id(2)])
        record = await session.get(YtChannel, channel_id(2))
        assert record is not None
        record.status = ChannelStatus.SEED
        record.kind = YtChannelKind.PERSONAL
        record.lang = YtLang.EN
        walks = await channels_to_walk(session)

    playlists = {walk.channel_id: walk.uploads for walk in walks}
    assert playlists[channel_id(1)] is not None
    assert playlists[channel_id(2)] is None


async def test_a_finished_walk_is_not_due_and_an_unfinished_one_is(
    database: Database,
) -> None:
    await _seeds(database, 1, 2)
    await _walked(database, 1, at=NOW)
    async with database.session() as session:
        await start_walk(
            session, channel_id(2), now=NOW, cutoff=NOW - YEAR, whole=False
        )

    assert await _due(database) == [channel_id(2)]


async def test_a_named_seed_is_walked_again(database: Database) -> None:
    await _seeds(database, 1, 2)
    await _walked(database, 1, at=NOW)
    async with database.session() as session:
        await create_channels(session, [channel_id(3)])

    named = [channel_id(1), channel_id(3)]
    assert await _due(database, named=named) == [channel_id(1)]


# --- what a walk records ------------------------------------------------


async def test_pages_add_up_and_a_restart_begins_at_zero(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await start_walk(
            session, channel_id(1), now=NOW, cutoff=NOW - YEAR, whole=True
        )
        await record_page(
            session, channel_id(1), PageCounts(videos=50, undated=1)
        )
        await record_page(
            session,
            channel_id(1),
            PageCounts(videos=12, out_of_order=2, unreturned=3),
        )

    async with database.session() as session:
        walk = await session.get(YtBackfillWalk, channel_id(1))
        assert walk is not None
        assert (walk.pages, walk.videos) == (2, 62)
        assert (walk.out_of_order, walk.undated, walk.unreturned) == (2, 1, 3)
        assert walk.whole and walk.finished_at is None

    later = NOW + timedelta(days=1)
    async with database.session() as session:
        await start_walk(
            session, channel_id(1), now=later, cutoff=later - YEAR, whole=False
        )

    async with database.session() as session:
        walk = await session.get(YtBackfillWalk, channel_id(1))
        assert walk is not None
        assert (walk.pages, walk.videos, walk.undated) == (0, 0, 0)
        assert walk.started_at == later and not walk.whole


async def test_a_failure_is_counted_until_a_walk_ends(
    database: Database,
) -> None:
    await _seeds(database, 1)
    async with database.session() as session:
        await start_walk(
            session, channel_id(1), now=NOW, cutoff=NOW - YEAR, whole=False
        )
        await record_walk_failure(
            session, channel_id(1), error="404 not found"
        )
        await start_walk(
            session, channel_id(1), now=NOW, cutoff=NOW - YEAR, whole=False
        )
        await record_walk_failure(session, channel_id(1), error="x" * 500)

    async with database.session() as session:
        walk = await session.get(YtBackfillWalk, channel_id(1))
        assert walk is not None
        assert walk.consecutive_failures == 2
        assert walk.last_error is not None and len(walk.last_error) == 200
        await finish_walk(session, channel_id(1), now=NOW, capped=True)

    async with database.session() as session:
        walk = await session.get(YtBackfillWalk, channel_id(1))
        assert walk is not None
        assert walk.consecutive_failures == 0 and walk.last_error is None
        assert walk.capped and walk.finished_at == NOW


# --- forgetting ---------------------------------------------------------


async def test_forgotten_walks_are_due_again(database: Database) -> None:
    await _seeds(database, 1, 2)
    await _walked(database, 1, at=NOW)
    await _walked(database, 2, at=NOW)
    async with database.session() as session:
        await forget_walks(session, [channel_id(1)])

    assert await _due(database) == [channel_id(1)]


# --- the status ---------------------------------------------------------


async def test_the_summary_counts_over_seeds(database: Database) -> None:
    await _seeds(database, 1, 2, 3, 4)
    await _walked(database, 1, at=NOW)
    async with database.session() as session:
        await record_page(session, channel_id(1), PageCounts(out_of_order=2))
        await start_walk(
            session, channel_id(2), now=NOW, cutoff=NOW - YEAR, whole=False
        )
        await record_page(
            session, channel_id(2), PageCounts(undated=1, unreturned=4)
        )
        await record_walk_failure(
            session, channel_id(2), error="403 forbidden"
        )
        await start_walk(
            session, channel_id(3), now=NOW, cutoff=NOW - YEAR, whole=False
        )
        await finish_walk(session, channel_id(3), now=NOW, capped=True)
        summary = await walk_summary(session)

    assert (summary.seeds, summary.walked, summary.due) == (4, 2, 2)
    assert (summary.interrupted, summary.failing, summary.capped) == (1, 1, 1)
    assert (summary.out_of_order, summary.undated, summary.unreturned) == (
        2,
        1,
        4,
    )
