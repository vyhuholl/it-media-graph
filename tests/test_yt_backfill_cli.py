"""`itgraph yt backfill` and `backfill-status` end to end.

The floor, the request limit, an exhausted day and a walk already
running are expected states, and end with status 0 and a sentence; a
failed request is a failure. In every case expiry ran first and what
was done is kept.
"""

import asyncio
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

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
    video_resource,
)

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtBackfillWalk,
    YtChannel,
    YtChannelKind,
    YtCommand,
    YtLang,
    YtRawChannel,
    YtRawVideo,
)
from itgraph.db.session import Database
from itgraph.db.yt_backfill import BACKFILL_LOCK_KEY, finish_walk, start_walk
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_quota import record_exhaustion
from itgraph.yt import client as yt_client

runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    original_youtube = yt_client.youtube

    def faked(database: Database, command: YtCommand, **kwargs: Any) -> Any:
        return original_youtube(
            database,
            command,
            transport=fake.transport(),
            sleep=no_sleep,
            limit=kwargs.get("limit"),
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


def _ages(count: int) -> list[int]:
    return [30 + 3 * n for n in range(count)]


def _api(count: int = 160) -> FakeYouTube:
    now = datetime.now(UTC)
    ages = _ages(count)
    return FakeYouTube(
        uploads={
            uploads_id(1): [
                upload_item(n, published=now - timedelta(days=age))
                for n, age in enumerate(ages)
            ]
        },
        videos=[
            video_resource(n, published=now - timedelta(days=age))
            for n, age in enumerate(ages)
        ],
    )


async def _arrange(
    url: str, *, status: ChannelStatus = ChannelStatus.SEED
) -> None:
    """One channel, plus a stranger's payload 31 days old."""
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
            record.status = status
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN
    finally:
        await database.dispose()


async def _scalar(url: str, statement: Any) -> Any:
    database = Database(url)
    try:
        async with database.session() as session:
            return await session.scalar(statement)
    finally:
        await database.dispose()


def _stranger_expired(url: str) -> bool:
    return (
        asyncio.run(
            _scalar(
                url,
                select(func.count())
                .select_from(YtRawChannel)
                .where(YtRawChannel.channel_id == channel_id(9)),
            )
        )
        == 0
    )


def _videos(url: str) -> int:
    return int(
        asyncio.run(_scalar(url, select(func.count()).select_from(YtRawVideo)))
    )


def test_a_walk_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))
    _use(monkeypatch, database_url, _api())

    result = runner.invoke(app, ["yt", "backfill"])

    assert result.exit_code == 0, result.output
    assert "complete: 1 of 1 channels walked" in result.output
    assert "112 videos" in result.output
    assert _stranger_expired(database_url)


def test_the_floor_ends_a_walk_quietly(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 4)
    monkeypatch.setattr(settings, "youtube_backfill_general_floor", 4)
    asyncio.run(_arrange(database_url))
    fake = _api()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill"])

    assert result.exit_code == 0, result.output
    assert "stopped:" in result.output
    assert "leaves 4 units" in result.output
    assert "next quota day begins" in result.output
    assert len(fake.requests) == 2
    assert _videos(database_url) == 50
    assert _stranger_expired(database_url)


def test_the_limit_ends_a_walk_quietly(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))
    fake = _api()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill", "--limit", "3"])

    assert result.exit_code == 0, result.output
    assert "stopped:" in result.output
    assert len(fake.requests) == 3


def test_a_walk_after_exhaustion_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    async def exhaust() -> None:
        await _arrange(database_url)
        database = Database(database_url)
        try:
            await record_exhaustion(database, command=YtCommand.WATCH)
        finally:
            await database.dispose()

    asyncio.run(exhaust())
    fake = _api()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill"])

    assert result.exit_code == 0, result.output
    assert "quotaExceeded" in result.output
    assert fake.requests == []
    assert _stranger_expired(database_url)


@pytest.fixture
def held_lock(database_url: str) -> Iterator[None]:
    """Another walk, holding the lock from a thread of its own."""
    ready = threading.Event()
    release = threading.Event()

    async def hold() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": BACKFILL_LOCK_KEY},
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


def test_a_second_walk_beside_a_running_one(
    monkeypatch: pytest.MonkeyPatch, database_url: str, held_lock: None
) -> None:
    asyncio.run(_arrange(database_url))
    fake = _api()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill"])

    assert result.exit_code == 0, result.output
    assert "another `yt backfill` walk is running" in result.output
    assert fake.requests == []
    assert _stranger_expired(database_url)


class _RefusingTokens(FakeYouTube):
    """Every listing past the first page answered 400."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.method(request) == "playlistItems" and (
            "pageToken" in request.url.params
        ):
            self.requests.append(request)
            return httpx.Response(
                400, json=error_body(400, "invalidPageToken")
            )
        return super().handler(request)


def test_a_failure_part_way_keeps_earlier_work(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))
    template = _api()
    fake = _RefusingTokens(
        uploads=template.uploads, videos=list(template.videos.values())
    )
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill"])

    assert result.exit_code == 1
    assert "invalidPageToken" in result.stderr
    assert _videos(database_url) == 50
    finished = asyncio.run(
        _scalar(database_url, select(YtBackfillWalk.finished_at))
    )
    assert finished is None


def test_whole_without_channels_never_reaches_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> None:
        raise AssertionError("refused input opened the database")

    monkeypatch.setattr(db_session, "Database", refuse)

    result = runner.invoke(app, ["yt", "backfill", "--whole"])

    assert result.exit_code == 2
    assert "--whole needs the channels" in result.output


def test_a_named_channel_that_is_not_a_seed_is_refused(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url, status=ChannelStatus.MAYBE))
    fake = _api()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill", channel_id(1)])

    assert result.exit_code == 1
    assert "not a seed" in result.stderr
    assert fake.requests == []


def test_a_named_seed_is_walked_again_and_whole(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    async def walked() -> None:
        await _arrange(database_url)
        now = datetime.now(UTC)
        database = Database(database_url)
        try:
            async with database.session() as session:
                await start_walk(
                    session,
                    channel_id(1),
                    now=now,
                    cutoff=now - timedelta(days=365),
                    whole=False,
                )
                await finish_walk(
                    session, channel_id(1), now=now, capped=False
                )
        finally:
            await database.dispose()

    asyncio.run(walked())
    fake = _api()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "backfill", "@fakehandle1", "--whole"])

    assert result.exit_code == 0, result.output
    assert len(fake.calls("playlistItems")) == 4
    assert asyncio.run(_scalar(database_url, select(YtBackfillWalk.whole)))


# --- backfill-status --------------------------------------------------------


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an offline command built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)


def test_the_status_reports_the_walk_without_asking_youtube(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    async def arranged() -> None:
        await _arrange(database_url)
        now = datetime.now(UTC)
        database = Database(database_url)
        try:
            async with database.session() as session:
                await start_walk(
                    session,
                    channel_id(1),
                    now=now,
                    cutoff=now - timedelta(days=365),
                    whole=True,
                )
                await session.execute(
                    text(
                        "UPDATE yt_backfill_walks SET out_of_order = 2, "
                        "undated = 1, unreturned = 3"
                    )
                )
        finally:
            await database.dispose()

    asyncio.run(arranged())
    monkeypatch.setattr(settings, "youtube_backfill_enabled", False)
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))

    result = runner.invoke(app, ["yt", "backfill-status"])

    assert result.exit_code == 0, result.output
    assert "walk disabled" in result.output
    assert "1 seed(s): 0 walked, 1 due (1 interrupted)" in result.output
    assert "2 item(s) out of order, 1 undated, 3 not returned" in result.output
    assert "0 stored video(s) found by walks" in result.output
    assert "a walk stops with 2000 left" in result.output
