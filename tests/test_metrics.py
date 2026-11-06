"""The series: what each says, on rows and on none, and how it is served."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph import yt_policy
from itgraph.db.models import (
    Alert,
    AlertKind,
    Channel,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    FloodEvent,
    PollState,
    RawMessage,
    YtChannel,
    YtCommand,
    YtDiscoveryPath,
    YtDiscoverySource,
    YtPassOutcome,
    YtQuotaBucket,
    YtQuotaDay,
    YtQuotaExhausted,
    YtQuotaPool,
    YtRawChannel,
    YtRawVideo,
    YtRawVideoStat,
    YtWatchPass,
)
from itgraph.db.session import Database
from itgraph.metrics.series import Family, read_families, render
from itgraph.metrics.serve import CONTENT_TYPE, serve
from itgraph.yt_quota import quota_day

FAKE_CHANNEL = "UCfake" + "0" * 18

# Noon in Moscow: outside the quiet hours.
NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
# Three in the morning in Moscow: inside them.
QUIET = datetime(2026, 11, 6, 3, 0, tzinfo=ZoneInfo("Europe/Moscow"))

EXPECTED = {
    "itgraph_telegram_last_poll_timestamp_seconds",
    "itgraph_telegram_oldest_overdue_seconds",
    "itgraph_telegram_failing_channels",
    "itgraph_telegram_quiet_hours",
    "itgraph_telegram_last_flood_timestamp_seconds",
    "itgraph_youtube_quota_spent_units",
    "itgraph_youtube_quota_cap_units",
    "itgraph_youtube_quota_exhausted",
    "itgraph_youtube_last_complete_pass_timestamp_seconds",
    "itgraph_youtube_oldest_payload_age_seconds",
    "itgraph_alerts_oldest_undelivered_seconds",
    "itgraph_alerts_failing",
}


async def families(database: Database, *, now: datetime = NOW) -> list[Family]:
    async with database.session() as session:
        return await read_families(session, now=now)


def value(read: Sequence[Family], name: str, **labels: str) -> float | None:
    for family in read:
        if family.name == name:
            for sample_labels, sample in family.samples:
                if dict(sample_labels) == labels:
                    return sample
    return None


def channel(
    tg_id: int, *, status: ChannelStatus = ChannelStatus.SEED
) -> Channel:
    return Channel(
        tg_id=tg_id,
        username=f"fake_channel_{tg_id}",
        status=status,
        discovered_via=DiscoverySource.MANUAL,
    )


async def seed_telegram(session: AsyncSession) -> None:
    session.add_all([channel(1), channel(2)])
    await session.flush()
    session.add_all(
        [
            PollState(
                channel_id=1,
                due_at=NOW - timedelta(minutes=7),
                last_polled_at=NOW - timedelta(hours=2),
                consecutive_failures=0,
            ),
            PollState(
                channel_id=2,
                due_at=NOW + timedelta(minutes=30),
                last_polled_at=NOW - timedelta(hours=3),
                consecutive_failures=2,
            ),
            FloodEvent(
                occurred_at=NOW - timedelta(minutes=40),
                method="GetHistoryRequest",
                seconds=31,
                command=CollectionCommand.WATCH,
            ),
        ]
    )


async def seed_youtube(session: AsyncSession) -> None:
    session.add(
        YtChannel(
            channel_id=FAKE_CHANNEL,
            status=ChannelStatus.CANDIDATE,
            discovered_via=YtDiscoverySource.MANUAL,
        )
    )
    await session.flush()
    session.add_all(
        [
            YtRawChannel(
                channel_id=FAKE_CHANNEL,
                payload={"title": "a fake channel's title"},
                fetched_at=NOW - timedelta(days=4),
            ),
            YtRawVideo(
                video_id="fakevideo01",
                payload={"snippet": {"channelId": FAKE_CHANNEL}},
                fetched_at=NOW - timedelta(days=9),
                discovered_at=NOW - timedelta(days=9),
                discovered_via=YtDiscoveryPath.COUNT,
            ),
        ]
    )
    await session.flush()
    session.add_all(
        [
            YtRawVideoStat(
                video_id="fakevideo01",
                observed_at=NOW - timedelta(days=2),
                views=1234567,
            ),
            YtQuotaDay(day=quota_day(NOW), pool=YtQuotaPool.WATCH, spent=2190),
            YtQuotaDay(
                day=quota_day(NOW) - timedelta(days=1),
                pool=YtQuotaPool.GENERAL,
                spent=77,
            ),
            YtQuotaExhausted(
                day=quota_day(NOW),
                bucket=YtQuotaBucket.SEARCH,
                command=YtCommand.WATCH,
            ),
            YtWatchPass(
                started_at=NOW - timedelta(minutes=16),
                finished_at=NOW - timedelta(minutes=15),
                outcome=YtPassOutcome.COMPLETE,
            ),
            YtWatchPass(
                started_at=NOW - timedelta(minutes=2),
                finished_at=NOW - timedelta(minutes=1),
                outcome=YtPassOutcome.FAILED,
            ),
        ]
    )


async def seed_alerts(session: AsyncSession) -> None:
    session.add(channel(3))
    await session.flush()
    session.add(RawMessage(channel_id=3, msg_id=10, payload={"id": 10}))
    await session.flush()
    session.add_all(
        [
            Alert(
                kind=AlertKind.VIEWS_SPIKE,
                channel_id=3,
                msg_id=10,
                band=1,
                value=4.2,
                raised_at=NOW - timedelta(hours=5),
            ),
            Alert(
                kind=AlertKind.FORWARD_SPIKE,
                channel_id=3,
                msg_id=10,
                band=1,
                value=4.2,
                raised_at=NOW - timedelta(hours=1),
                attempts=3,
            ),
            Alert(
                kind=AlertKind.REACTION_SPIKE,
                channel_id=3,
                msg_id=10,
                band=1,
                value=4.2,
                raised_at=NOW - timedelta(days=20),
                delivered_at=NOW - timedelta(days=20),
            ),
        ]
    )


async def test_every_series_is_declared_on_an_empty_database(
    database: Database,
) -> None:
    read = await families(database)

    assert {family.name for family in read} == EXPECTED
    assert value(read, "itgraph_telegram_last_poll_timestamp_seconds") == 0
    assert value(read, "itgraph_telegram_oldest_overdue_seconds") == 0
    assert value(read, "itgraph_telegram_failing_channels") == 0
    assert value(read, "itgraph_telegram_last_flood_timestamp_seconds") == 0
    assert value(read, "itgraph_alerts_oldest_undelivered_seconds") == 0
    assert value(read, "itgraph_alerts_failing") == 0
    assert (
        value(read, "itgraph_youtube_last_complete_pass_timestamp_seconds")
        == 0
    )


async def test_no_age_series_without_api_data(database: Database) -> None:
    """A restored copy holds none, and an absent age must not read as 0."""
    read = await families(database)

    ages = next(
        family
        for family in read
        if family.name == "itgraph_youtube_oldest_payload_age_seconds"
    )
    assert list(ages.samples) == []


async def test_a_collector_that_stopped_is_measured(
    database: Database,
) -> None:
    async with database.session() as session:
        await seed_telegram(session)

    read = await families(database)

    assert (
        value(read, "itgraph_telegram_last_poll_timestamp_seconds")
        == (NOW - timedelta(hours=2)).timestamp()
    )
    assert value(read, "itgraph_telegram_oldest_overdue_seconds") == 420
    assert value(read, "itgraph_telegram_failing_channels") == 1
    assert (
        value(read, "itgraph_telegram_last_flood_timestamp_seconds")
        == (NOW - timedelta(minutes=40)).timestamp()
    )


async def test_quiet_hours_are_said(database: Database) -> None:
    assert value(await families(database), "itgraph_telegram_quiet_hours") == 0
    assert (
        value(
            await families(database, now=QUIET), "itgraph_telegram_quiet_hours"
        )
        == 1
    )


async def test_the_quota_day_alone_is_read(database: Database) -> None:
    async with database.session() as session:
        await seed_youtube(session)

    read = await families(database)

    spent = "itgraph_youtube_quota_spent_units"
    assert value(read, spent, pool="watch") == 2190
    # Yesterday's 77 does not count.
    assert value(read, spent, pool="general") == 0
    assert value(read, spent, pool="search") == 0
    assert value(read, "itgraph_youtube_quota_cap_units", pool="watch") == 3000
    assert value(read, "itgraph_youtube_quota_exhausted", bucket="search") == 1
    assert value(read, "itgraph_youtube_quota_exhausted", bucket="main") == 0


async def test_only_a_complete_pass_counts(database: Database) -> None:
    async with database.session() as session:
        await seed_youtube(session)

    read = await families(database)

    assert (
        value(read, "itgraph_youtube_last_complete_pass_timestamp_seconds")
        == (NOW - timedelta(minutes=15)).timestamp()
    )


async def test_ages_by_kind(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with database.session() as session:
        await seed_youtube(session)

    name = "itgraph_youtube_oldest_payload_age_seconds"
    read = await families(database)
    assert (
        value(read, name, kind="channel") == timedelta(days=4).total_seconds()
    )
    assert value(read, name, kind="video") == timedelta(days=9).total_seconds()
    assert (
        value(read, name, kind="reading") == timedelta(days=2).total_seconds()
    )
    assert value(read, name, kind="lookup") is None

    # Once acceptance is recorded a reading may lawfully be years old.
    monkeypatch.setattr(yt_policy, "DERIVED_METRICS_ACCEPTED_ON", NOW.date())
    assert value(await families(database), name, kind="reading") is None


async def test_alerts(database: Database) -> None:
    async with database.session() as session:
        await seed_alerts(session)

    read = await families(database)

    assert value(read, "itgraph_alerts_oldest_undelivered_seconds") == 18000
    assert value(read, "itgraph_alerts_failing") == 1


async def test_a_scrape_changes_nothing(database: Database) -> None:
    async with database.session() as session:
        await seed_telegram(session)
        await seed_youtube(session)
        await seed_alerts(session)

    async def snapshot() -> tuple[Any, ...]:
        async with database.session() as session:
            return (
                await session.scalar(
                    text(
                        "SELECT sum(n_tup_upd + n_tup_del + n_tup_ins) "
                        "FROM pg_stat_xact_user_tables"
                    )
                ),
                (await session.execute(select(PollState.due_at))).all(),
                (await session.execute(select(Alert.attempts))).all(),
            )

    before = await snapshot()
    await families(database)
    assert await snapshot() == before


async def test_no_series_reads_the_big_tables_and_no_label_comes_from_data(
    database: Database,
) -> None:
    async with database.session() as session:
        await seed_telegram(session)
        await seed_youtube(session)
        await seed_alerts(session)

    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    sync_engine = database.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        read = await families(database)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)

    assert statements
    for statement in statements:
        assert "raw_messages" not in statement
        assert "message_metrics" not in statement
        assert "payload" not in statement

    allowed = {"watch", "general", "search", "main"} | {
        "channel",
        "video",
        "reading",
        "lookup",
        "featured",
    }
    body = render(read)
    for family in read:
        for labels, _ in family.samples:
            assert set(labels.values()) <= allowed
    assert "fake" not in body
    assert "1234567" not in body


def test_the_text_format() -> None:
    body = render(
        [
            Family("itgraph_a", "A thing.", [({}, 1.5)]),
            Family("itgraph_b", "B thing.", [({"pool": "watch"}, 2.0)]),
            Family("itgraph_c", "C thing.", []),
        ]
    )

    assert body == (
        "# HELP itgraph_a A thing.\n"
        "# TYPE itgraph_a gauge\n"
        "itgraph_a 1.5\n"
        "# HELP itgraph_b B thing.\n"
        "# TYPE itgraph_b gauge\n"
        'itgraph_b{pool="watch"} 2.0\n'
        "# HELP itgraph_c C thing.\n"
        "# TYPE itgraph_c gauge\n"
    )


@asynccontextmanager
async def listening(database: Database) -> AsyncIterator[int]:
    stop = asyncio.Event()
    bound: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        serve(database, stop=stop, host="127.0.0.1", port=0, bound=bound)
    )
    try:
        yield await bound
    finally:
        stop.set()
        await task


async def ask(port: int, request: bytes) -> tuple[str, str, str]:
    """The status line, the headers and the body of one response."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = (await reader.read()).decode()
    writer.close()
    head, _, body = raw.partition("\r\n\r\n")
    status, _, headers = head.partition("\r\n")
    return status, headers, body


def get(path: str, method: str = "GET") -> bytes:
    return f"{method} {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode()


async def test_a_scrape_is_served(database: Database) -> None:
    async with listening(database) as port:
        status, headers, body = await ask(port, get("/metrics"))

    assert status == "HTTP/1.1 200 OK"
    assert f"Content-Type: {CONTENT_TYPE}" in headers
    for name in EXPECTED:
        assert f"# TYPE {name} gauge" in body


@pytest.mark.parametrize(
    ("request_bytes", "expected"),
    [
        (get("/"), "404"),
        (get("/metrics/"), "404"),
        (get("/metrics", "POST"), "405"),
        (get("/metrics", "HEAD"), "405"),
        (b"nonsense\r\n\r\n", "400"),
    ],
)
async def test_anything_else_makes_no_query(
    database: Database, request_bytes: bytes, expected: str
) -> None:
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    sync_engine = database.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        async with listening(database) as port:
            status, _, _ = await ask(port, request_bytes)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)

    assert status.split()[1] == expected
    assert statements == []


async def test_a_database_that_refuses_is_a_503_and_the_process_lives() -> (
    None
):
    # Port 1 refuses at once, and no password is ever offered to anything.
    unreachable = Database(
        "postgresql+asyncpg://nobody:test-password@127.0.0.1:1/nothing_test"
    )
    try:
        async with listening(unreachable) as port:
            first, _, _ = await ask(port, get("/metrics"))
            second, _, _ = await ask(port, get("/metrics"))
    finally:
        await unreachable.dispose()

    assert first.split()[1] == "503"
    assert second.split()[1] == "503"
