"""`itgraph yt offer` end to end, against a `_test` database.

Offline by default: the selection ranks what is stored and, with no
key, opens no client. Where a key is configured, the one buy call rides
a fake API. Ids and names are fake and spelled so.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from tglinks import post, tg_channel, tg_posts
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    use_key,
    video_id,
    video_resource,
)
from ytinventory import channels

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtCommand,
    YtDiscoveryPath,
    YtLookupKind,
)
from itgraph.db.session import Database
from itgraph.db.yt_chart import store_chart
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_videos import VideoWrite, store_videos
from itgraph.yt import client as yt_client
from itgraph.yt.client import youtube

NOW = datetime.now(UTC)
TODAY = NOW.astimezone(ZoneInfo("Europe/Moscow")).date()
TARGET = 90
CHARTED = 91
runner = CliRunner()


@pytest.fixture(autouse=True)
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key anywhere: the selection needs none."""
    monkeypatch.setattr(settings, "youtube_api_key", None)


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))


async def _arrange(database_url: str) -> None:
    """A rising channel of two families — one Telegram, one a video's
    reference — and a channel of the day's RU chart."""
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
        )
        await channels({1: ChannelStatus.SEED})(database)
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
            await store_lookups(
                session,
                {(YtLookupKind.VIDEO, video_id(1)): channel_id(TARGET)},
                fetched_at=NOW - timedelta(days=1),
            )
            await store_chart(
                session,
                "RU",
                {
                    "items": [
                        {
                            "id": video_id(4),
                            "snippet": {"channelId": channel_id(CHARTED)},
                        }
                    ]
                },
                fetched_at=datetime.now(UTC),
            )
    finally:
        await database.dispose()


async def _offers(url: str) -> list[tuple[str, str, str, str]]:
    database = Database(url)
    try:
        async with database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT o.channel_id, o.ranking::text, "
                    "o.discovered_via::text, o.topic::text "
                    "FROM yt_rising_offers o ORDER BY o.id"
                )
            )
            return [(row[0], row[1], row[2], row[3]) for row in rows]
    finally:
        await database.dispose()


async def _days(url: str) -> list[str]:
    database = Database(url)
    try:
        async with database.session() as session:
            return list(
                await session.scalars(
                    text(
                        "SELECT day::text FROM yt_rising_offer_days "
                        "ORDER BY day"
                    )
                )
            )
    finally:
        await database.dispose()


async def _records(url: str) -> dict[str, tuple[str, str, bool]]:
    database = Database(url)
    try:
        async with database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT channel_id, status::text, discovered_via::text, "
                    "first_seen_at IS NOT NULL FROM yt_channels"
                )
            )
            return {
                channel: (status, via, seen)
                for channel, status, via, seen in rows
            }
    finally:
        await database.dispose()


def test_the_day_s_selection_with_records_and_counts_only(
    database_url: str,
) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["yt", "offer"])

    assert result.exit_code == 0, result.output
    offers = asyncio.run(_offers(database_url))
    # The link ranking's head first, the tie of one family a side going
    # to Telegram; then the chart's, in the Russian topic.
    assert offers == [
        (channel_id(TARGET), "link", "telegram", "en"),
        (channel_id(CHARTED), "chart", "chart", "ru"),
    ]
    # Each offered channel became a candidate record, without data.
    records = asyncio.run(_records(database_url))
    assert records[channel_id(TARGET)] == ("candidate", "telegram", True)
    assert records[channel_id(CHARTED)] == ("candidate", "chart", True)
    assert f"{TODAY}: 1 rising by links, 1 in the day's charts" in (
        result.output
    )
    assert "2 offered, 0 left for a later day" in result.output
    # Counts and ids only: nothing a payload carries is printed.
    for said in ("Fake", "fakehandle", "youtu.be"):
        assert said not in result.output


def test_a_day_is_selected_once(database_url: str) -> None:
    asyncio.run(_arrange(database_url))
    runner.invoke(app, ["yt", "offer"])

    result = runner.invoke(app, ["yt", "offer"])

    assert result.exit_code == 0, result.output
    assert f"{TODAY} has its selection; nothing written" in result.output
    assert len(asyncio.run(_offers(database_url))) == 2


def test_an_empty_day_is_recorded(database_url: str) -> None:
    result = runner.invoke(app, ["yt", "offer"])

    assert result.exit_code == 0, result.output
    assert asyncio.run(_days(database_url)) == [str(TODAY)]
    assert asyncio.run(_offers(database_url)) == []


def test_a_preview_names_the_offers_by_id_and_writes_nothing(
    database_url: str,
) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["yt", "offer", "--preview"])

    assert result.exit_code == 0, result.output
    assert "(preview, nothing written)" in result.output
    assert channel_id(TARGET) in result.output
    assert channel_id(CHARTED) in result.output
    assert "chart RU 1" in result.output
    assert asyncio.run(_days(database_url)) == []
    records = asyncio.run(_records(database_url))
    assert channel_id(TARGET) not in records
    assert channel_id(CHARTED) not in records


def test_the_overflow_is_offered_the_next_day(database_url: str) -> None:
    asyncio.run(_arrange(database_url))
    runner.invoke(app, ["yt", "offer"])

    tomorrow = (NOW + timedelta(days=1)).isoformat()
    result = runner.invoke(app, ["yt", "offer", "--now", tomorrow])

    assert result.exit_code == 0, result.output
    # Both channels were offered: neither is offered again, and the
    # day's rows are the same channels the next day cannot take.
    assert "0 offered" in result.output
    assert len(asyncio.run(_offers(database_url))) == 2


def test_the_logs_of_a_selection_name_ids_and_counts_only(
    database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(_arrange(database_url))

    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(app, ["yt", "offer"])

    assert result.exit_code == 0, result.output
    logged = "\n".join(entry.getMessage() for entry in caplog.records)
    for said in ("Fake", "fakehandle", "youtube.com/channel", "youtu.be"):
        assert said not in logged, said


# --- the buy ------------------------------------------------------------


async def _payloads(url: str) -> dict[str, dict]:
    database = Database(url)
    try:
        async with database.session() as session:
            rows = await session.execute(
                text("SELECT channel_id, payload FROM yt_raw_channels")
            )
            return {channel: payload for channel, payload in rows}
    finally:
        await database.dispose()


def _fake_api(monkeypatch: pytest.MonkeyPatch, fake: FakeYouTube) -> None:
    """`youtube()` over the fake, whatever the command of a call is."""

    @asynccontextmanager
    async def api(database: Database, command: YtCommand, **_: object):
        async with youtube(
            database, command, transport=fake.transport(), sleep=no_sleep
        ) as client:
            yield client

    monkeypatch.setattr(yt_client, "youtube", api)


def test_the_selection_buys_the_offered_payloads_with_the_day(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One call for the whole day, and the payloads land in the same
    transaction as the day — before the bot can claim an offer, which
    is the point of buying them there."""
    asyncio.run(_arrange(database_url))
    use_key(monkeypatch)
    fake = FakeYouTube(
        channel_resource(TARGET, title="Fake Rising <Co>"),
        channel_resource(CHARTED, title="Fake Chart Top"),
    )
    _fake_api(monkeypatch, fake)

    result = runner.invoke(app, ["yt", "offer"])

    assert result.exit_code == 0, result.output
    # One call, both channels of the day.
    [asked] = fake.calls("channels")
    assert set(asked["id"].split(",")) == {
        channel_id(TARGET),
        channel_id(CHARTED),
    }
    payloads = asyncio.run(_payloads(database_url))
    assert payloads[channel_id(TARGET)]["snippet"]["title"] == (
        "Fake Rising <Co>"
    )
    assert payloads[channel_id(CHARTED)]["snippet"]["title"] == (
        "Fake Chart Top"
    )
    # The day is the day either way.
    assert len(asyncio.run(_offers(database_url))) == 2


def test_a_refused_buy_loses_nothing_but_the_titles(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call is refused three times over: the day and its records
    are stored as ever, and the cards link by id until the refresh."""
    asyncio.run(_arrange(database_url))
    use_key(monkeypatch)
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(500, json=error_body(500, "backendError"))
    ] * 3
    _fake_api(monkeypatch, fake)

    result = runner.invoke(app, ["yt", "offer"])

    assert result.exit_code == 0, result.output
    assert "2 offered, 0 left for a later day" in result.output
    assert len(asyncio.run(_offers(database_url))) == 2
    # The arrange's own seed keeps its payload; the offered channels
    # gained none.
    payloads = asyncio.run(_payloads(database_url))
    assert channel_id(TARGET) not in payloads
    assert channel_id(CHARTED) not in payloads
