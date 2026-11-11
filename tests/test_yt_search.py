"""`itgraph yt search`: what it asks, what it skips, what it spends.

Every query and id is invented, and nothing touches a socket.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner
from ytfakes import FakeYouTube, channel_id, error_body, no_sleep, use_key

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    YtCommand,
    YtQuotaDay,
    YtQuotaPool,
    YtRawSearchResult,
    YtSearchRun,
    YtSearchType,
)
from itgraph.db.session import Database
from itgraph.db.yt_search import start_run
from itgraph.derive.yt_search import read_queries
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.search_pass import SearchSummary, run_searches

VIDEO = YtSearchType.VIDEO
CHANNEL = YtSearchType.CHANNEL
runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube, *, limit: int | None = None
) -> AsyncIterator[YouTube]:
    async with youtube(
        database,
        YtCommand.SEARCH,
        transport=fake.transport(),
        sleep=no_sleep,
        limit=limit,
    ) as connected:
        yield connected


async def _search(
    database: Database,
    fake: FakeYouTube,
    queries: list[str],
    kind: YtSearchType = VIDEO,
    *,
    pages: int = 1,
    force: bool = False,
    limit: int | None = None,
) -> SearchSummary:
    async with client(database, fake, limit=limit) as yt:
        return await run_searches(
            yt, database, queries, kind, pages=pages, force=force
        )


async def _previous(
    database: Database, query: str, kind: YtSearchType, *, days_ago: int
) -> datetime:
    at = datetime.now(UTC) - timedelta(days=days_ago)
    async with database.session() as session:
        await start_run(
            session,
            query,
            kind,
            [channel_id(1)],
            asked_at=at,
            published_after=None,
        )
    return at


def _fake(query: str, channels: list[str]) -> FakeYouTube:
    """YouTube answering a video search for one query with these channels."""
    return FakeYouTube(searches={(query, "video"): channels})


# --- the queries ---------------------------------------------------------


def test_blank_lines_and_comments_are_skipped_and_repeats_asked_once() -> None:
    lines = [
        "fake rust",
        "",
        "# a comment",
        "  fake   go  ",
        "fake rust",
        "C# fake tutorial",
    ]

    assert read_queries(lines) == ["fake rust", "fake go", "C# fake tutorial"]


async def test_each_query_is_asked(database: Database) -> None:
    fake = FakeYouTube()

    summary = await _search(database, fake, ["fake a", "fake b", "fake c"])

    assert summary.asked == 3
    assert [call["q"] for call in fake.calls("search")] == [
        "fake a",
        "fake b",
        "fake c",
    ]


# --- the publication window ----------------------------------------------


async def test_a_first_video_search_asks_for_the_last_30_days(
    database: Database,
) -> None:
    fake = FakeYouTube()
    before = datetime.now(UTC)

    await _search(database, fake, ["fake query"])

    [asked] = fake.calls("search")
    bound = datetime.fromisoformat(asked["publishedAfter"])
    assert (
        before - timedelta(days=30, seconds=2)
        <= bound
        <= datetime.now(UTC) - timedelta(days=30) + timedelta(seconds=2)
    )


async def test_a_forced_repeat_asks_only_since_that_run(
    database: Database,
) -> None:
    then = await _previous(database, "fake query", VIDEO, days_ago=10)
    fake = FakeYouTube()

    await _search(database, fake, ["fake query"], force=True)

    [asked] = fake.calls("search")
    assert asked["publishedAfter"] == then.strftime("%Y-%m-%dT%H:%M:%SZ")


async def test_a_channel_search_has_no_window(database: Database) -> None:
    fake = FakeYouTube()

    await _search(database, fake, ["fake query"], CHANNEL)

    assert "publishedAfter" not in fake.calls("search")[0]


# --- not asked twice while a run is in date ------------------------------


async def test_a_repeat_within_the_month_costs_nothing(
    database: Database,
) -> None:
    await _previous(database, "fake query", CHANNEL, days_ago=5)
    fake = FakeYouTube()

    summary = await _search(database, fake, ["fake query"], CHANNEL)

    assert fake.requests == []
    assert (summary.asked, summary.skipped) == (0, 1)
    assert "skipped 1" in summary.line()


async def test_another_type_is_another_query(database: Database) -> None:
    await _previous(database, "fake query", VIDEO, days_ago=5)
    fake = FakeYouTube()

    summary = await _search(database, fake, ["fake query"], CHANNEL)

    assert summary.asked == 1


async def test_after_the_month_it_is_asked_again(database: Database) -> None:
    await _previous(database, "fake query", VIDEO, days_ago=31)
    fake = FakeYouTube()

    summary = await _search(database, fake, ["fake query"])

    assert summary.asked == 1


async def test_a_query_with_no_results_is_still_a_run(
    database: Database,
) -> None:
    fake = FakeYouTube()

    await _search(database, fake, ["fake nothing"])
    second = await _search(database, fake, ["fake nothing"])

    assert second.skipped == 1
    assert len(fake.calls("search")) == 1


# --- pages and spending ----------------------------------------------------


async def test_more_pages_on_request(database: Database) -> None:
    fake = _fake("fake query", [channel_id(n) for n in range(120)])

    summary = await _search(database, fake, ["fake query"], pages=3)

    assert len(fake.calls("search")) == 3
    assert summary.results == 120
    async with database.session() as session:
        positions = list(
            await session.scalars(
                select(YtRawSearchResult.position).order_by(
                    YtRawSearchResult.position
                )
            )
        )
        [run] = await session.scalars(select(YtSearchRun))
    assert positions == list(range(1, 121))
    assert run.pages == 3


async def test_a_run_stopped_at_its_limit_keeps_its_pages(
    database: Database,
) -> None:
    fake = _fake("fake query", [channel_id(n) for n in range(150)])

    summary = await _search(database, fake, ["fake query"], pages=3, limit=2)

    assert summary.limited is not None
    async with database.session() as session:
        stored = await session.scalar(
            select(func.count()).select_from(YtRawSearchResult)
        )
    assert stored == 100


async def test_the_search_pool_alone_is_spent(database: Database) -> None:
    await _search(database, FakeYouTube(), ["fake query"])

    async with database.session() as session:
        pools = {
            row.pool: row.spent
            for row in await session.scalars(select(YtQuotaDay))
        }
    assert pools == {YtQuotaPool.SEARCH: 1}


async def test_a_spent_search_pool_stops_before_any_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_search_daily_quota", 1)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    fake = FakeYouTube()

    summary = await _search(database, fake, ["fake a", "fake b"])

    assert len(fake.requests) == 1
    assert summary.stopped is not None
    assert "next quota day begins" in str(summary.stopped)
    async with database.session() as session:
        pools = {row.pool for row in await session.scalars(select(YtQuotaDay))}
    assert pools == {YtQuotaPool.SEARCH}


async def test_quota_exceeded_from_the_search_bucket_keeps_what_was_stored(
    database: Database,
) -> None:
    fake = _fake("fake a", [channel_id(1)])
    real = fake.handler
    calls = 0

    def second_spent(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            fake.requests.append(request)
            return httpx.Response(403, json=error_body(403, "quotaExceeded"))
        return real(request)

    async with youtube(
        database,
        YtCommand.SEARCH,
        transport=httpx.MockTransport(second_spent),
        sleep=no_sleep,
    ) as yt:
        summary = await run_searches(yt, database, ["fake a", "fake b"], VIDEO)

    assert summary.asked == 1 and summary.stopped is not None
    async with database.session() as session:
        runs = await session.scalar(
            select(func.count()).select_from(YtSearchRun)
        )
    assert runs == 1


async def test_logs_name_ids_only(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = _fake("fake secret words", [channel_id(1)])

    await _search(database, fake, ["fake secret words"])

    assert "secret" not in caplog.text
    assert "googleapis" not in caplog.text


# --- the command ---------------------------------------------------------


def _patched(
    monkeypatch: pytest.MonkeyPatch, database_url: str, fake: FakeYouTube
) -> None:
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    real = httpx.AsyncClient.__init__

    def faked(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = fake.transport()
        real(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", faked)


def test_queries_from_standard_input(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    fake = FakeYouTube()
    _patched(monkeypatch, database_url, fake)

    result = runner.invoke(
        app,
        ["yt", "search", "--from-file", "-"],
        input="fake a\n\n# comment\nfake b\nfake c\n",
    )

    assert result.exit_code == 0, result.output
    assert "queries asked 3, skipped 0" in result.output
    assert len(fake.calls("search")) == 3


def test_queries_and_a_file_are_refused_together(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    _patched(monkeypatch, database_url, FakeYouTube())

    result = runner.invoke(
        app, ["yt", "search", "fake a", "--from-file", "-"], input="fake b\n"
    )

    assert result.exit_code != 0
    assert "not both" in result.output
