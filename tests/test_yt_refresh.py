"""The refresh pass: everything due, fifty a call, nothing lost on a stop."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    use_key,
)

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtCommand,
    YtRawChannel,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import (
    create_channels,
    due_for_refresh,
    store_payloads,
)
from itgraph.db.yt_quota import QuotaExhaustedError, record_exhaustion
from itgraph.yt import client as yt_client
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.refresh import refresh_channels

STALE = datetime.now(UTC) - timedelta(days=26)
FRESH = datetime.now(UTC) - timedelta(days=1)

runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[YouTube]:
    async with youtube(
        database,
        YtCommand.REFRESH,
        transport=fake.transport(),
        sleep=no_sleep,
    ) as connected:
        yield connected


async def _channels(
    database: Database, numbers: range | list[int], fetched_at: datetime | None
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in numbers])
        if fetched_at is not None:
            await store_payloads(
                session,
                {channel_id(n): channel_resource(n) for n in numbers},
                fetched_at=fetched_at,
            )


async def _fetched_at(database: Database) -> dict[str, datetime]:
    async with database.session() as session:
        rows = await session.execute(
            select(YtRawChannel.channel_id, YtRawChannel.fetched_at)
        )
        return {channel: when for channel, when in rows.tuples()}


async def test_a_stale_payload_is_refreshed_and_a_fresh_one_is_not(
    database: Database,
) -> None:
    await _channels(database, [1], STALE)
    await _channels(database, [2], FRESH)
    fake = FakeYouTube(channel_resource(1), channel_resource(2))

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert (summary.due, summary.refreshed) == (1, 1)
    assert fake.params()[0]["id"] == channel_id(1)
    fetched = await _fetched_at(database)
    assert fetched[channel_id(1)] > STALE
    assert fetched[channel_id(2)] == FRESH


async def test_rejected_channels_are_refreshed_too(
    database: Database,
) -> None:
    await _channels(database, [1], STALE)
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.REJECTED
        record.reject_reason = YtRejectReason.ADJACENT

    async with client(database, FakeYouTube(channel_resource(1))) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.refreshed == 1


async def test_a_hundred_due_cost_two_requests(database: Database) -> None:
    await _channels(database, range(100), STALE)
    fake = FakeYouTube(*(channel_resource(n) for n in range(100)))

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.refreshed == 100
    assert len(fake.requests) == 2


async def test_gone_and_back(database: Database) -> None:
    await _channels(database, [1, 2], STALE)

    async with client(database, FakeYouTube(channel_resource(1))) as yt:
        first = await refresh_channels(yt, database)

    assert first.gone == [channel_id(2)]
    assert channel_id(2) not in await _fetched_at(database)

    fake = FakeYouTube(channel_resource(1), channel_resource(2))
    async with client(database, fake) as yt:
        second = await refresh_channels(yt, database)

    # Only the gone one was due: it has no payload to be fresh.
    assert second.due == 1
    assert second.returned == [channel_id(2)]
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(2))
    assert record is not None and record.gone_at is None


async def test_one_dead_channel_does_not_cost_the_others(
    database: Database,
) -> None:
    await _channels(database, [1, 2, 3], STALE)
    fake = FakeYouTube(
        channel_resource(1), channel_resource(3), batch_404=True
    )

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.refreshed == 2
    assert summary.gone == [channel_id(2)]


async def test_exhaustion_part_way_keeps_what_was_done(
    database: Database,
) -> None:
    await _channels(database, range(60), STALE)
    fake = FakeYouTube(*(channel_resource(n) for n in range(60)))
    fake.queued = [
        httpx.Response(
            200, json={"items": [channel_resource(n) for n in range(50)]}
        ),
        httpx.Response(403, json=error_body(403, "quotaExceeded")),
    ]

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert isinstance(summary.stopped, QuotaExhaustedError)
    assert summary.refreshed == 50
    assert summary.gone == []

    # The next quota day: only the ten not reached are still due.
    async with database.session() as session:
        due = await due_for_refresh(
            session, older_than=datetime.now(UTC) - timedelta(days=25)
        )
    assert len(due) == 10


async def test_the_log_carries_ids_and_nothing_youtube_said(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await _channels(database, [1, 2], STALE)
    fake = FakeYouTube(channel_resource(1, title="A Title For The Log Test"))

    async with client(database, fake) as yt:
        await refresh_channels(yt, database)

    assert channel_id(2) in caplog.text
    assert "A Title For The Log Test" not in caplog.text
    assert "fakehandle1" not in caplog.text


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    original_youtube = yt_client.youtube

    def faked(database: Database, command: YtCommand) -> object:
        return original_youtube(
            database, command, transport=fake.transport(), sleep=no_sleep
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


async def _setup_stale(url: str) -> None:
    database = Database(url)
    try:
        await _channels(database, [1, 2], STALE)
    finally:
        await database.dispose()


def test_the_command_reports_and_lists_the_gone(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    asyncio.run(_setup_stale(database_url))
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 0, result.output
    assert "due 2, refreshed 1, gone 1, back 0" in result.output
    assert f"gone: {channel_id(2)}" in result.output


def test_the_command_fails_when_stopped(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    asyncio.run(_setup_stale(database_url))
    fake = FakeYouTube()
    fake.queued = [httpx.Response(403, json=error_body(403, "quotaExceeded"))]
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 1
    assert "quotaExceeded" in result.output


def test_the_command_without_a_key_names_the_setting(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    asyncio.run(_setup_stale(database_url))
    fake = FakeYouTube()
    _use(monkeypatch, database_url, fake)
    monkeypatch.setattr(settings, "youtube_api_key", None)

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 1
    assert "YOUTUBE_API_KEY" in result.output
    assert fake.requests == []


def test_the_command_refuses_to_start_on_an_exhausted_day(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    async def exhaust() -> None:
        database = Database(database_url)
        try:
            await _channels(database, [1], STALE)
            await record_exhaustion(database, command=YtCommand.ADD)
        finally:
            await database.dispose()

    asyncio.run(exhaust())
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 1
    assert "quotaExceeded" in result.stderr
    assert fake.requests == []
