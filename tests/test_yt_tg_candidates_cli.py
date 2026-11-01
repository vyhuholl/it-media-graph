"""`itgraph yt tg-candidates` end to end.

Offline by construction: no key, no HTTP client. What it prints is
ranked by distinct families and shows no API data; with `--add` the top
becomes candidates from `telegram`, refused while anything awaits a
lookup; the refresh then brings their data for review. Every channel,
post, id and handle is invented.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from tglinks import post, seed_linking, tg_channel, tg_posts
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    no_sleep,
    use_key,
    video_id,
)

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtCommand,
    YtDiscoverySource,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.yt import client as yt_client

runner = CliRunner()


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test database, no API key, and no HTTP client to be had."""
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    monkeypatch.setattr(settings, "youtube_api_key", None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("yt tg-candidates built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)


def _arrange(database_url: str, *steps: Any) -> None:
    """Run setup coroutines, each given a database, in order."""

    async def run() -> None:
        database = Database(database_url)
        try:
            for step in steps:
                await step(database)
        finally:
            await database.dispose()

    asyncio.run(run())


def _linking(tg_id: int, *urls: str) -> Any:
    async def step(database: Database) -> None:
        await seed_linking(database, tg_id, *urls)

    return step


def _channel_links(numbers: range) -> list[str]:
    return [f"youtube.com/channel/{channel_id(n)}" for n in numbers]


def _candidates(*args: str) -> Any:
    return runner.invoke(app, ["yt", "tg-candidates", *args])


async def _records(database_url: str) -> dict[str, YtChannel]:
    database = Database(database_url)
    try:
        async with database.session() as session:
            rows = (await session.scalars(select(YtChannel))).all()
        return {row.channel_id: row for row in rows}
    finally:
        await database.dispose()


# --- the report ------------------------------------------------------


def test_a_ranked_row_shows_no_api_data(database_url: str) -> None:
    async def known(database: Database) -> None:
        async with database.session() as session:
            await create_channels(session, [channel_id(1)])
            await store_payloads(
                session,
                {channel_id(1): channel_resource(1, title="Fake Known Title")},
                fetched_at=datetime.now(UTC),
            )

    _arrange(
        database_url,
        known,
        _linking(2001, "youtube.com/channel/" + channel_id(50)),
        _linking(2002, "youtube.com/channel/" + channel_id(50)),
        _linking(2003, "youtube.com/channel/" + channel_id(1)),
    )

    result = _candidates()

    assert result.exit_code == 0, result.output
    row = next(
        line for line in result.output.splitlines() if channel_id(50) in line
    )
    assert "2 families" in row
    assert "2026-10-01" in row
    assert "Fake Known Title" not in result.output
    assert "linked channels already in the inventory: 1" in result.output


def test_the_top_and_the_rest(database_url: str) -> None:
    links = _channel_links(range(150))
    _arrange(database_url, _linking(2001, *links), _linking(2002, *links))

    result = _candidates()

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert sum(1 for line in lines if "UCfake_channel_" in line) == 100
    assert "  … and 50 more: 50 with 2 families" in lines


def test_what_could_not_be_counted(database_url: str) -> None:
    _arrange(
        database_url,
        _linking(
            2001,
            f"youtu.be/{video_id(1)}",
            "youtube.com/c/FakeCustom",
            "youtube.com/playlist?list=PLfake-playlist-0001",
            "bit.ly/fake-short",
        ),
    )

    result = _candidates()

    assert result.exit_code == 0, result.output
    assert "awaiting a lookup: 1 links" in result.output
    assert "fakecustom" in result.output
    assert "counted: 1 playlist pages, 1 shortened links" in result.output


def test_without_a_key_or_a_client_it_still_reads(database_url: str) -> None:
    # The autouse fixture removed the key and made any HTTP client fail.
    _arrange(database_url, _linking(2001, "youtube.com/@fake-unknown"))
    assert _candidates().exit_code == 0


# --- adding ----------------------------------------------------------


def test_the_top_hundred_are_added(database_url: str) -> None:
    links = _channel_links(range(150))
    _arrange(database_url, _linking(2001, *links), _linking(2002, *links))

    result = _candidates("--add")

    assert result.exit_code == 0, result.output
    assert "added 100 candidates" in result.output
    records = asyncio.run(_records(database_url))
    assert set(records) == {channel_id(n) for n in range(100)}
    for record in records.values():
        assert record.status is ChannelStatus.CANDIDATE
        assert record.discovered_via is YtDiscoverySource.TELEGRAM
        assert record.reviewed_at is None


def test_an_incomplete_lookup_refuses_the_cut(database_url: str) -> None:
    links = _channel_links(range(3))
    _arrange(
        database_url,
        _linking(2001, *links, f"youtu.be/{video_id(1)}"),
        _linking(2002, *links),
    )

    result = _candidates("--add")

    assert result.exit_code == 1
    assert "1 links still await a lookup" in result.output
    assert "itgraph yt tg-lookup" in result.output
    assert asyncio.run(_records(database_url)) == {}


def test_the_next_run_brings_the_next_hundred(database_url: str) -> None:
    links = _channel_links(range(150))
    _arrange(database_url, _linking(2001, *links), _linking(2002, *links))

    assert _candidates("--add").exit_code == 0
    result = _candidates("--add")

    assert result.exit_code == 0, result.output
    assert "added 50 candidates" in result.output
    assert len(asyncio.run(_records(database_url))) == 150


def test_a_candidate_s_data_arrives_with_the_refresh(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    links = _channel_links(range(2))
    _arrange(database_url, _linking(2001, *links), _linking(2002, *links))
    assert _candidates("--add").exit_code == 0

    monkeypatch.undo()
    use_key(monkeypatch)
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    fake = FakeYouTube(
        channel_resource(0, handle="fake-candidate-zero"),
        channel_resource(1, handle="fake-candidate-one"),
    )
    original_youtube = yt_client.youtube

    def faked(database: Database, command: YtCommand, **kwargs: Any) -> Any:
        return original_youtube(
            database, command, transport=fake.transport(), sleep=no_sleep
        )

    monkeypatch.setattr(yt_client, "youtube", faked)

    refreshed = runner.invoke(app, ["yt", "refresh"])
    exported = runner.invoke(app, ["yt", "review-export"])

    assert refreshed.exit_code == 0, refreshed.output
    assert exported.exit_code == 0, exported.output
    assert "@fake-candidate-zero" in exported.output
    assert "@fake-candidate-one" in exported.output


def test_yt_add_never_records_telegram() -> None:
    """Only the ranking writes `telegram`: the import has no way to.

    That `yt add` records `manual` is `test_yt_add`'s to check.
    """
    refused = runner.invoke(app, ["yt", "add", "--via", "telegram", "fake"])
    shown = runner.invoke(app, ["yt", "add", "--help"])

    assert refused.exit_code == 2
    assert shown.exit_code == 0
    assert "telegram" not in shown.output.lower()


# --- logs ------------------------------------------------------------


def test_logs_name_ids_only(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    async def with_usernames(database: Database) -> None:
        await tg_channel(database, 2003)
        await tg_posts(database, 2003, post(1, "youtube.com/@fake-secret-two"))

    _arrange(
        database_url,
        _linking(
            2001,
            "youtube.com/@fake-secret-handle",
            "youtube.com/c/FakeSecretCustom",
        ),
        _linking(2002, "youtube.com/@fake-secret-handle"),
        with_usernames,
    )

    with caplog.at_level(logging.DEBUG):
        assert _candidates().exit_code == 0

    logged = "\n".join(entry.getMessage() for entry in caplog.records)
    for said in (
        "fake-secret-handle",
        "fakesecretcustom",
        "fake_tg_",
        "youtube.com",
        "http",
    ):
        assert said not in logged.lower(), said
