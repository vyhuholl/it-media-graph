"""The `itgraph yt` commands, end to end against the test database.

No network: a command that makes requests gets a fake API through
``httpx.MockTransport``; one that makes none gets an ``AsyncClient`` that
refuses to be built, so a stray request fails the test instead of
reaching YouTube.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner
from ytfakes import channel_id, channel_resource, video_id, video_resource

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import YtCommand, YtRawChannel, YtRequestOutcome
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_quota import book, record_exhaustion, record_outcome

runner = CliRunner()


def use_test_database(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Point every command at the throwaway database.

    The same reason as Telegram's CLI tests: the command builds its own
    ``Database`` inside its own event loop.
    """
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(url))


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any attempt to build an HTTP client fails the command."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an offline command built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)
    yield


@pytest.fixture
def small_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 3)


def test_yt_commands_are_listed_apart_from_telegrams() -> None:
    result = runner.invoke(app, ["yt", "--help"])
    assert result.exit_code == 0
    assert "quota" in result.output
    assert "dump-dialogs" not in result.output


def test_quota_on_an_empty_day(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    offline: None,
    small_quota: None,
) -> None:
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    assert "general" in result.output
    assert "0 of      7 units" in result.output
    assert "UTC" in result.output
    assert "No requests today." in result.output


async def _spend(database_url: str) -> None:
    database = Database(database_url)
    try:
        answered = await book(
            database, command=YtCommand.ADD, method="channels.list", units=1
        )
        await record_outcome(database, answered, YtRequestOutcome.OK)
        await book(
            database, command=YtCommand.ADD, method="channels.list", units=2
        )
    finally:
        await database.dispose()


async def _exhaust(database_url: str) -> None:
    database = Database(database_url)
    try:
        await record_exhaustion(database, command=YtCommand.REFRESH)
    finally:
        await database.dispose()


def test_quota_shows_spending_by_command_and_method(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    offline: None,
    small_quota: None,
) -> None:

    asyncio.run(_spend(database_url))
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    assert "3 of      7 units, 4 left" in result.output
    row = next(
        line for line in result.output.splitlines() if "channels.list" in line
    )
    assert row.split() == ["add", "channels.list", "2", "3", "1"]
    assert "never answered" in result.output


def test_quota_says_when_the_day_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    offline: None,
    small_quota: None,
) -> None:

    asyncio.run(_exhaust(database_url))
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    assert "quotaExceeded" in result.output
    assert "yt refresh" in result.output
    # The main bucket ended; the search bucket did not.
    assert "main bucket: exhausted" in result.output
    assert "search bucket: not exhausted" in result.output


def test_quota_shows_the_search_pool(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    offline: None,
    small_quota: None,
) -> None:
    use_test_database(monkeypatch, database_url)
    monkeypatch.setattr(settings, "youtube_search_daily_quota", 100)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    row = next(
        line
        for line in result.output.splitlines()
        if line.startswith("search ")
    )
    assert row.split()[:4] == ["search", "0", "of", "100"]


def test_the_reset_is_also_on_the_local_clock() -> None:
    from itgraph.db.yt_quota import describe_reset

    text = describe_reset(datetime(2026, 10, 27, 7, 0, tzinfo=UTC))
    assert text.startswith("2026-10-27 07:00 UTC (")
    assert text.endswith(" here)")


async def _old_payload(database_url: str) -> None:

    database = Database(database_url)
    try:
        async with database.session() as session:
            await create_channels(session, [channel_id(1)])
            await store_payloads(
                session,
                {channel_id(1): channel_resource(1)},
                fetched_at=datetime.now(UTC) - timedelta(days=31),
            )
    finally:
        await database.dispose()


async def _payload_count(database_url: str) -> int:

    database = Database(database_url)
    try:
        async with database.session() as session:
            count = await session.scalar(
                select(func.count()).select_from(YtRawChannel)
            )
    finally:
        await database.dispose()
    return int(count or 0)


def test_no_yt_command_deletes_old_payloads(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", None)
    asyncio.run(_old_payload(database_url))
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    assert asyncio.run(_payload_count(database_url)) == 1


async def _videos_with_readings(database_url: str) -> None:
    """Video 1 last fetched 31 days ago; video 2 fresh, with readings
    taken 32 and 31 days ago."""
    from itgraph.db.models import YtDiscoveryPath
    from itgraph.db.yt_videos import VideoWrite, store_videos
    from itgraph.derive.yt_videos import Counters

    now = datetime.now(UTC)

    def write(number: int) -> VideoWrite:
        return VideoWrite(
            video_id=video_id(number),
            payload=video_resource(number, published=now - timedelta(days=33)),
            due_at=None,
            reading=Counters(views=1),
            discovered_via=YtDiscoveryPath.FIRST,
        )

    database = Database(database_url)
    try:
        async with database.session() as session:
            for number, days in ((1, 31), (2, 32), (2, 31), (2, 0)):
                await store_videos(
                    session,
                    [write(number)],
                    observed_at=now - timedelta(days=days),
                )
    finally:
        await database.dispose()


async def _video_counts(database_url: str) -> tuple[int, int]:
    from itgraph.db.models import YtRawVideo, YtRawVideoStat

    database = Database(database_url)
    try:
        async with database.session() as session:
            videos = await session.scalar(
                select(func.count()).select_from(YtRawVideo)
            )
            readings = await session.scalar(
                select(func.count()).select_from(YtRawVideoStat)
            )
    finally:
        await database.dispose()
    return int(videos or 0), int(readings or 0)


def test_no_yt_command_deletes_old_videos_or_readings(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", None)
    asyncio.run(_videos_with_readings(database_url))
    before = asyncio.run(_video_counts(database_url))
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    assert asyncio.run(_video_counts(database_url)) == before


async def _walks_ended(database_url: str, *ages: int) -> None:
    """A seed per age, its walk ended that many days ago."""
    from itgraph.db.models import (
        ChannelStatus,
        YtChannel,
        YtChannelKind,
        YtLang,
    )
    from itgraph.db.yt_backfill import finish_walk, start_walk

    now = datetime.now(UTC)
    database = Database(database_url)
    try:
        async with database.session() as session:
            for number, age in enumerate(ages, start=1):
                await create_channels(session, [channel_id(number)])
                record = await session.get(YtChannel, channel_id(number))
                assert record is not None
                record.status = ChannelStatus.SEED
                record.kind = YtChannelKind.PERSONAL
                record.lang = YtLang.EN
                ended = now - timedelta(days=age)
                await start_walk(
                    session,
                    channel_id(number),
                    now=ended,
                    cutoff=ended - timedelta(days=365),
                    whole=False,
                )
                await finish_walk(
                    session, channel_id(number), now=ended, capped=False
                )
    finally:
        await database.dispose()


async def _walks_left(database_url: str) -> int:
    from itgraph.db.models import YtBackfillWalk

    database = Database(database_url)
    try:
        async with database.session() as session:
            count = await session.scalar(
                select(func.count()).select_from(YtBackfillWalk)
            )
    finally:
        await database.dispose()
    return int(count or 0)


@pytest.mark.parametrize("enabled", [False, True])
def test_walks_are_kept_whether_or_not_the_walk_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    offline: None,
    enabled: bool,
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", None)
    monkeypatch.setattr(settings, "youtube_backfill_enabled", enabled)
    asyncio.run(_walks_ended(database_url, 90))
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "quota"])

    assert result.exit_code == 0, result.output
    assert asyncio.run(_walks_left(database_url)) == 1


@pytest.mark.parametrize(
    "command",
    [
        "quota",
        "refresh",
        "add",
        "review-export",
        "review-apply",
        "mark",
        "channels",
        "watch",
        "watch-status",
        "backfill",
        "backfill-status",
    ],
)
def test_help_needs_no_database(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """`--help` must not need a database, or a `.env`."""

    def refuse() -> None:
        raise AssertionError("--help opened the database")

    monkeypatch.setattr(db_session, "Database", refuse)

    result = runner.invoke(app, ["yt", command, "--help"])

    assert result.exit_code == 0, result.output


def test_refused_input_never_reaches_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused before anything is spent."""

    def refuse() -> None:
        raise AssertionError("refused input opened the database")

    monkeypatch.setattr(db_session, "Database", refuse)

    result = runner.invoke(
        app, ["yt", "add", "https://www.youtube.com/c/FakeCustom"]
    )

    assert result.exit_code == 2
    assert "/c/ custom URL" in result.output
