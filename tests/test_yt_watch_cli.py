"""`itgraph yt watch` end to end: how a pass ends, and what it says.

A spent pool, an exhausted day and a pass already running are expected
states, and end with status 0 and a sentence; a failed request is a
failure. In every case expiry ran first and what was done is kept.
"""

import asyncio
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    upload_item,
    uploads_id,
    use_key,
    video_id,
    video_resource,
)

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtCommand,
    YtDiscoveryPath,
    YtLang,
    YtPassOutcome,
    YtRawChannel,
    YtRawVideoStat,
    YtRequest,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_quota import record_exhaustion
from itgraph.db.yt_videos import VideoWrite, store_videos
from itgraph.db.yt_watch import WATCH_LOCK_KEY, last_pass, record_listings
from itgraph.derive.yt_videos import Counters
from itgraph.yt import client as yt_client
from itgraph.yt.client import youtube
from itgraph.yt.watch import watch_pass

runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    original_youtube = yt_client.youtube

    def faked(
        database: Database, command: YtCommand, **kwargs: object
    ) -> object:
        return original_youtube(
            database, command, transport=fake.transport(), sleep=no_sleep
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


async def _arrange(url: str, *, published: datetime | None = None) -> None:
    """One seed listed just now, plus a stranger's payload 31 days old."""
    now = datetime.now(UTC)
    database = Database(url)
    try:
        async with database.session() as session:
            await create_channels(session, [channel_id(1), channel_id(9)])
            await store_payloads(
                session, {channel_id(1): channel_resource(1)}, fetched_at=now
            )
            await store_payloads(
                session,
                {channel_id(9): channel_resource(9)},
                fetched_at=now - timedelta(days=31),
            )
            record = await session.get(YtChannel, channel_id(1))
            assert record is not None
            record.status = ChannelStatus.SEED
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN
            if published is None:
                await record_listings(session, [channel_id(1)], now=now)
    finally:
        await database.dispose()


async def _stranger_expired(url: str) -> bool:
    database = Database(url)
    try:
        async with database.session() as session:
            return await session.get(YtRawChannel, channel_id(9)) is None
    finally:
        await database.dispose()


async def _last_outcome(url: str) -> YtPassOutcome | None:
    database = Database(url)
    try:
        async with database.session() as session:
            found = await last_pass(session)
            return found.outcome if found is not None else None
    finally:
        await database.dispose()


def test_a_pass_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))

    result = runner.invoke(app, ["yt", "watch"])

    assert result.exit_code == 0, result.output
    assert "complete: 1 channels" in result.output
    assert asyncio.run(_stranger_expired(database_url))
    assert asyncio.run(_last_outcome(database_url)) is YtPassOutcome.COMPLETE


def test_a_pool_spent_part_way_ends_quietly(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """One unit of pool: detection spends it, the listing is refused."""
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 1)
    asyncio.run(_arrange(database_url, published=datetime.now(UTC)))
    fake = FakeYouTube(channel_resource(1), uploads={uploads_id(1): []})
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "watch"])

    assert result.exit_code == 0, result.output
    assert "pool_spent" in result.output
    assert "next quota day begins" in result.output
    assert fake.calls("playlistItems") == []
    assert asyncio.run(_stranger_expired(database_url))
    assert asyncio.run(_last_outcome(database_url)) is YtPassOutcome.POOL_SPENT


def test_a_pass_after_exhaustion_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    async def exhaust() -> None:
        await _arrange(database_url)
        database = Database(database_url)
        try:
            await record_exhaustion(database, command=YtCommand.REFRESH)
        finally:
            await database.dispose()

    asyncio.run(exhaust())
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "watch"])

    assert result.exit_code == 0, result.output
    assert "quotaExceeded" in result.output
    assert fake.requests == []
    assert asyncio.run(_stranger_expired(database_url))
    assert asyncio.run(_last_outcome(database_url)) is YtPassOutcome.EXHAUSTED


@pytest.fixture
def held_lock(database_url: str) -> Iterator[None]:
    """Another pass, holding the lock from a thread of its own."""
    ready = threading.Event()
    release = threading.Event()

    async def hold() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": WATCH_LOCK_KEY},
                )
                await connection.commit()
                ready.set()
                while not release.is_set():
                    await asyncio.sleep(0.02)
        finally:
            await engine.dispose()

    thread = threading.Thread(target=lambda: asyncio.run(hold()))
    thread.start()
    assert ready.wait(10)
    try:
        yield
    finally:
        release.set()
        thread.join(10)


def test_a_second_pass_beside_a_running_one(
    monkeypatch: pytest.MonkeyPatch, database_url: str, held_lock: None
) -> None:
    asyncio.run(_arrange(database_url))
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "watch"])

    assert result.exit_code == 0, result.output
    assert "another `yt watch` pass is running" in result.output
    assert fake.requests == []
    # Expiry is not the pass's, and still ran.
    assert asyncio.run(_stranger_expired(database_url))
    assert asyncio.run(_last_outcome(database_url)) is None


def test_a_failed_request_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))
    fake = FakeYouTube()
    fake.queued = [httpx.Response(400, json=error_body(400, "invalidFields"))]
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "watch"])

    assert result.exit_code == 1
    assert "invalidFields" in result.stderr
    assert asyncio.run(_last_outcome(database_url)) is YtPassOutcome.FAILED


def test_without_a_key_the_setting_is_named(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))
    fake = FakeYouTube()
    _use(monkeypatch, database_url, fake)
    monkeypatch.setattr(settings, "youtube_api_key", None)

    result = runner.invoke(app, ["yt", "watch"])

    assert result.exit_code == 1
    assert "YOUTUBE_API_KEY" in result.output
    assert fake.requests == []


async def test_progress_is_kept_per_request(database: Database) -> None:
    """Sixty due videos: the first fifty stored, the last ten's request fails."""
    now = datetime.now(UTC)
    published = now - timedelta(hours=3)
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        await store_payloads(
            session, {channel_id(1): channel_resource(1)}, fetched_at=now
        )
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.SEED
        record.kind = YtChannelKind.PERSONAL
        record.lang = YtLang.EN
        await record_listings(session, [channel_id(1)], now=now)
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(n),
                    payload=video_resource(n, published=published),
                    due_at=now - timedelta(minutes=1),
                    reading=Counters(views=1),
                    discovered_via=YtDiscoveryPath.FIRST,
                )
                for n in range(60)
            ],
            observed_at=published + timedelta(minutes=5),
        )
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [
        httpx.Response(200, json={"items": [channel_resource(1)]}),
        httpx.Response(
            200,
            json={
                "items": [
                    video_resource(n, published=published) for n in range(50)
                ]
            },
        ),
        *(
            httpx.Response(500, json=error_body(500, "backendError"))
            for _ in range(3)
        ),
    ]

    async with youtube(
        database, YtCommand.WATCH, transport=fake.transport(), sleep=no_sleep
    ) as client:
        result = await watch_pass(client, database, clock=lambda: now)

    assert result.outcome is YtPassOutcome.FAILED
    assert result.readings == 50
    async with database.session() as session:
        stored = await session.scalar(
            select(func.count())
            .select_from(YtRawVideoStat)
            .where(YtRawVideoStat.observed_at == now)
        )
        requests = await session.scalar(
            select(func.count())
            .select_from(YtRequest)
            .where(YtRequest.command == YtCommand.WATCH)
        )
    assert stored == 50
    assert requests == 5


def test_the_status_reports_the_loop_without_asking_youtube(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    published = datetime.now(UTC) - timedelta(minutes=10)
    asyncio.run(_arrange(database_url, published=published))
    fake = FakeYouTube(
        channel_resource(1),
        uploads={uploads_id(1): [upload_item(1, published=published)]},
        videos=[video_resource(1, published=published)],
    )
    _use(monkeypatch, database_url, fake)
    assert runner.invoke(app, ["yt", "watch"]).exit_code == 0

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the status command built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)
    result = runner.invoke(app, ["yt", "watch-status"])

    assert result.exit_code == 0, result.output
    assert "1 video(s) tracked, 0 due now" in result.output
    # Detection, the listing and the read: a unit each.
    assert "watch pool: 3 of 3000 units today" in result.output
    assert (
        "found in 7 days: 0 by the video count, 0 by the daily check, "
        "1 by first listings"
    ) in result.output
    assert "complete: listed 1, new 1, readings 1, removed 0" in result.output


def test_the_status_before_any_pass(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    _use(monkeypatch, database_url, FakeYouTube())

    result = runner.invoke(app, ["yt", "watch-status"])

    assert result.exit_code == 0, result.output
    assert "0 video(s) tracked" in result.output
    assert "no pass has run yet" in result.output
