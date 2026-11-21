"""`itgraph yt rising`: the ranking as one report, ids and counts only.

A ranked row shows ids, numbers and dates and nothing read from the API
beyond ids; the command writes nothing, makes no request and needs no
key; what could not be counted is said. Every id and post is invented,
and nothing touches a socket.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from tglinks import post, tg_channel, tg_posts
from typer.testing import CliRunner
from ytfakes import channel_id, video_id, video_resource
from ytinventory import channels

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import YtDiscoveryPath, YtLookupKind
from itgraph.db.session import Database
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_videos import VideoWrite, store_videos

NOW = datetime.now(UTC)
SEED = "seed"
TARGET = 90
runner = CliRunner()


@pytest.fixture(autouse=True)
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key anywhere: the report needs none."""
    monkeypatch.setattr(settings, "youtube_api_key", None)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))


async def _arrange(database_url: str) -> None:
    """A rising channel of two families, and a reference awaiting a
    lookup."""
    database = Database(database_url)
    try:
        await tg_channel(database, 2001)
        await tg_posts(
            database,
            2001,
            post(
                1,
                f"youtu.be/{video_id(1)}",
                date=(NOW - timedelta(days=5)).isoformat(),
            ),
            # A link nothing stored answers: it awaits a lookup.
            post(
                2,
                f"youtu.be/{video_id(5)}",
                date=(NOW - timedelta(days=4)).isoformat(),
            ),
        )
        await channels({1: SEED})(database)
        async with database.session() as session:
            await store_videos(
                session,
                [
                    VideoWrite(
                        video_id=video_id(2),
                        payload=video_resource(
                            2,
                            published=NOW - timedelta(days=3),
                            channel=1,
                            description=(
                                "see https://youtube.com/channel/"
                                f"{channel_id(TARGET)}"
                            ),
                        ),
                        due_at=None,
                        reading=None,
                        discovered_via=YtDiscoveryPath.CHECK,
                    )
                ],
                observed_at=NOW,
            )
            # A video the Telegram post links, awaiting its lookup.
            await store_videos(
                session,
                [
                    VideoWrite(
                        video_id=video_id(3),
                        payload=video_resource(
                            3,
                            published=NOW - timedelta(days=2),
                            channel=TARGET,
                        ),
                        due_at=None,
                        reading=None,
                        discovered_via=YtDiscoveryPath.CHECK,
                    )
                ],
                observed_at=NOW,
            )
            await store_lookups(
                session,
                {(YtLookupKind.VIDEO, video_id(1)): channel_id(TARGET)},
                fetched_at=NOW - timedelta(days=1),
            )
    finally:
        await database.dispose()


def test_the_report_shows_a_ranked_row(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["yt", "rising"])

    assert result.exit_code == 0
    assert channel_id(TARGET) in result.output  # the link-ranked row
    assert "2 families in the window, 0 before" in result.output
    first = (NOW - timedelta(days=5)).date().isoformat()
    assert f"first in the window {first}" in result.output
    # What could not be counted.
    assert "awaiting a lookup: 1" in result.output
    assert "run `itgraph yt rising-lookups`" in result.output
    assert "seeds with a featured list out of date: 1" in result.output


def test_the_report_shows_no_data_read_from_the_api(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["yt", "rising"])

    # The stored payloads carry a title and a handle; none of it shows.
    for said in ("Fake", "fakehandle"):
        assert said not in result.output


def test_the_report_writes_nothing_and_needs_no_key(
    database_url: str,
) -> None:
    asyncio.run(_arrange(database_url))

    async def counts() -> int:
        database = Database(database_url)
        try:
            async with database.session() as session:
                total = await session.scalar(
                    text(
                        "SELECT (SELECT COUNT(*) FROM yt_raw_channels) "
                        "+ (SELECT COUNT(*) FROM yt_channels) "
                        "+ (SELECT COUNT(*) FROM yt_raw_videos) "
                        "+ (SELECT COUNT(*) FROM yt_raw_lookups)"
                    )
                )
            return int(total or 0)
        finally:
            await database.dispose()

    before = asyncio.run(counts())
    result = runner.invoke(app, ["yt", "rising"])
    after = asyncio.run(counts())

    assert result.exit_code == 0
    assert before == after


def test_the_logs_of_a_report_name_ids_only(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_arrange(database_url))

    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(app, ["yt", "rising"])

    assert result.exit_code == 0
    logged = "\n".join(entry.getMessage() for entry in caplog.records)
    for said in ("Fake", "fakehandle", "youtube.com/channel", "youtu.be"):
        assert said not in logged, said
