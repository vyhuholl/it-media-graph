"""`itgraph yt tg-lookup`: what it asks, in what order, and what it keeps.

What any stored answer already names costs nothing; the best-evidenced
names go first; a run stopped part way keeps its answers and the next
one goes on; nothing it logs names a handle. Every channel, post, id
and handle is invented, and nothing touches a socket.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from tglinks import seed_linking
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_list,
    channel_resource,
    error_body,
    no_sleep,
    use_key,
    video_id,
    video_resource,
)

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    YtCommand,
    YtDiscoveryPath,
    YtLookupKind,
    YtQuotaPool,
    YtRawLookup,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_quota import POOL_OF, units_to_leave
from itgraph.db.yt_videos import VideoWrite, store_videos
from itgraph.yt import client as yt_client
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.tg_lookup import LookupSummary, run_lookups

NOW = datetime.now(UTC)
OWNER = 7


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube, *, limit: int | None = None
) -> AsyncIterator[YouTube]:
    async with youtube(
        database,
        YtCommand.TG_LOOKUP,
        transport=fake.transport(),
        sleep=no_sleep,
        limit=limit,
    ) as connected:
        yield connected


async def _run(
    database: Database, fake: FakeYouTube, *, limit: int | None = None
) -> LookupSummary:
    async with client(database, fake, limit=limit) as yt:
        return await run_lookups(yt, database)


async def _stored(database: Database) -> dict[tuple[YtLookupKind, str], Any]:
    async with database.session() as session:
        rows = (await session.scalars(select(YtRawLookup))).all()
    return {(row.kind, row.value): row.channel_id for row in rows}


def _video(number: int) -> dict[str, Any]:
    return video_resource(number, published=NOW, channel=OWNER)


# --- names linked from telegram are looked up ------------------------


async def test_names_linked_from_telegram_are_looked_up(
    database: Database,
) -> None:
    await seed_linking(
        database, 2001, f"youtu.be/{video_id(1)}", "youtube.com/@fake-unknown"
    )
    fake = FakeYouTube(
        channel_resource(8, handle="fake-unknown"), videos=[_video(1)]
    )

    summary = await _run(database, fake)

    assert await _stored(database) == {
        (YtLookupKind.VIDEO, video_id(1)): channel_id(OWNER),
        (YtLookupKind.HANDLE, "fake-unknown"): channel_id(8),
    }
    assert summary.awaiting == 0
    assert summary.requests == 2


async def test_fifty_videos_to_a_request(database: Database) -> None:
    await seed_linking(
        database, 2001, *(f"youtu.be/{video_id(n)}" for n in range(100))
    )
    fake = FakeYouTube(videos=[_video(n) for n in range(100)])

    await _run(database, fake)

    assert len(fake.calls("videos")) == 2


# --- what needs no request -------------------------------------------


async def test_what_is_answered_costs_nothing(database: Database) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        await store_payloads(
            session,
            {channel_id(1): channel_resource(1, handle="fake-seed")},
            fetched_at=NOW,
        )
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(1),
                    payload=video_resource(1, published=NOW, channel=1),
                    due_at=None,
                    reading=None,
                    discovered_via=YtDiscoveryPath.CHECK,
                )
            ],
            observed_at=NOW,
        )
        await store_lookups(
            session,
            {
                (YtLookupKind.VIDEO, video_id(2)): channel_id(9),
                (YtLookupKind.HANDLE, "fake-nobody"): None,
            },
            fetched_at=NOW - timedelta(days=5),
        )
    await seed_linking(
        database,
        2001,
        f"youtube.com/channel/{channel_id(3)}",
        f"youtu.be/{video_id(1)}",
        "youtube.com/@fake-seed",
        f"youtu.be/{video_id(2)}",
        "youtube.com/@fake-nobody",
    )
    fake = FakeYouTube()

    summary = await _run(database, fake)

    assert fake.requests == []
    assert summary.lines() == ["nothing awaits a lookup"]


async def test_a_handle_two_records_carry_is_asked_about(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(1), channel_id(2)])
        await store_payloads(
            session,
            {
                channel_id(1): channel_resource(1, handle="fake-dup"),
                channel_id(2): channel_resource(2, handle="fake-dup"),
            },
            fetched_at=NOW,
        )
    await seed_linking(database, 2001, "youtube.com/@fake-dup")
    fake = FakeYouTube(channel_resource(2, handle="fake-dup"))

    await _run(database, fake)

    assert [call["forHandle"] for call in fake.calls("channels")] == [
        "@fake-dup"
    ]


async def test_an_expired_lookup_is_asked_again(database: Database) -> None:
    async with database.session() as session:
        await store_lookups(
            session,
            {(YtLookupKind.VIDEO, video_id(1)): channel_id(OWNER)},
            fetched_at=NOW - timedelta(days=31),
        )
    await seed_linking(database, 2001, f"youtu.be/{video_id(1)}")
    fake = FakeYouTube(videos=[_video(1)])

    await _run(database, fake)

    assert len(fake.calls("videos")) == 1


# --- spending ---------------------------------------------------------


async def test_a_handle_nobody_holds_does_not_stop_the_run(
    database: Database,
) -> None:
    for tg_id in (2001, 2002):
        await seed_linking(database, tg_id, "youtube.com/@fake-nobody")
    await seed_linking(database, 2003, "youtube.com/@fake-one")
    fake = FakeYouTube(channel_resource(8, handle="fake-one"))

    summary = await _run(database, fake)

    assert [call["forHandle"] for call in fake.calls("channels")] == [
        "@fake-nobody",
        "@fake-one",
    ]
    assert await _stored(database) == {
        (YtLookupKind.HANDLE, "fake-nobody"): None,
        (YtLookupKind.HANDLE, "fake-one"): channel_id(8),
    }
    assert summary.stopped is None
    assert summary.awaiting == 0


async def test_the_best_evidenced_names_go_first(database: Database) -> None:
    for tg_id in (2001, 2002, 2003):
        await seed_linking(database, tg_id, "youtube.com/@fake-three")
    await seed_linking(database, 2004, "youtube.com/@fake-one")
    fake = FakeYouTube(channel_resource(8, handle="fake-three"))

    summary = await _run(database, fake, limit=1)

    assert [call["forHandle"] for call in fake.calls("channels")] == [
        "@fake-three"
    ]
    assert summary.limited is not None
    assert summary.awaiting == 1


async def test_a_stopped_run_resumes(database: Database) -> None:
    for tg_id in (2001, 2002):
        await seed_linking(database, tg_id, "youtube.com/@fake-two")
    await seed_linking(database, 2003, "youtube.com/@fake-one")
    fake = FakeYouTube(
        channel_resource(8, handle="fake-two"),
        channel_resource(9, handle="fake-one"),
    )

    await _run(database, fake, limit=1)
    await _run(database, fake)

    assert [call["forHandle"] for call in fake.calls("channels")] == [
        "@fake-two",
        "@fake-one",
    ]


async def test_spent_quota_keeps_what_was_done(database: Database) -> None:
    for tg_id in (2001, 2002):
        await seed_linking(database, tg_id, "youtube.com/@fake-two")
    await seed_linking(database, 2003, "youtube.com/@fake-one")
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(
            200, json=channel_list(channel_resource(8, handle="fake-two"))
        ),
        httpx.Response(403, json=error_body(403, "quotaExceeded")),
    ]

    summary = await _run(database, fake)

    assert summary.stopped is not None
    assert await _stored(database) == {
        (YtLookupKind.HANDLE, "fake-two"): channel_id(8)
    }


async def test_no_floor_is_left(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A floor above everything the pool holds stops the walk, not this."""
    assert POOL_OF[YtCommand.TG_LOOKUP] is YtQuotaPool.GENERAL
    assert units_to_leave(YtCommand.TG_LOOKUP) == 0
    # Above the general pool's whole cap, so fewer units are left than the
    # floor from the first request on. Settings would refuse it; the
    # monkeypatch sets the state the scenario names directly.
    monkeypatch.setattr(settings, "youtube_backfill_general_floor", 10_000)
    await seed_linking(database, 2001, "youtube.com/@fake-one")
    fake = FakeYouTube(channel_resource(8, handle="fake-one"))

    summary = await _run(database, fake)

    assert summary.stopped is None
    assert summary.requests == 1


# --- logs ------------------------------------------------------------


async def test_logs_name_no_handle_and_no_link(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_linking(
        database,
        2001,
        "youtube.com/@fake-secret-handle",
        "youtube.com/user/FakeSecretLegacy",
        f"youtu.be/{video_id(1)}",
    )
    fake = FakeYouTube(
        channel_resource(8, handle="fake-secret-handle"), videos=[_video(1)]
    )

    with caplog.at_level(logging.DEBUG):
        await _run(database, fake)

    logged = "\n".join(entry.getMessage() for entry in caplog.records)
    for said in (
        "fake-secret-handle",
        "fakesecretlegacy",
        "youtube.com",
        "youtu.be",
        "http",
    ):
        assert said not in logged.lower(), said
    assert logging.getLogger("httpx").level == logging.NOTSET


# --- the command -----------------------------------------------------

runner = CliRunner()


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    original_youtube = yt_client.youtube

    def faked(database: Database, command: YtCommand, **kwargs: Any) -> Any:
        assert command is YtCommand.TG_LOOKUP
        return original_youtube(
            database,
            command,
            transport=fake.transport(),
            sleep=no_sleep,
            limit=kwargs.get("limit"),
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


def _given(database_url: str, tg_id: int, *urls: str) -> None:
    async def arrange() -> None:
        database = Database(database_url)
        try:
            await seed_linking(database, tg_id, *urls)
        finally:
            await database.dispose()

    asyncio.run(arrange())


def test_the_command_needs_no_telegram_session(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.db.session_lease import SessionLease

    async def held(self: object) -> None:
        raise AssertionError("tg-lookup asked for the Telegram session")

    def no_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("tg-lookup built a Telegram client")

    monkeypatch.setattr(SessionLease, "acquire", held)
    monkeypatch.setattr("telethon.TelegramClient.__init__", no_client)
    _given(database_url, 2001, "youtube.com/@fake-one")
    fake = FakeYouTube(channel_resource(8, handle="fake-one"))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "tg-lookup"])

    assert result.exit_code == 0, result.output
    assert "handles        looked up     1, found     1" in result.output
    assert "still awaiting a lookup: 0" in result.output


def test_the_command_with_nothing_to_look_up(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeYouTube()
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "tg-lookup"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "nothing awaits a lookup"
    assert fake.requests == []


def test_the_command_reports_its_limit(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _given(
        database_url, 2001, "youtube.com/@fake-one", "youtube.com/@fake-two"
    )
    fake = FakeYouTube(channel_resource(8, handle="fake-one"))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "tg-lookup", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert "requests 1" in result.output
    assert "still awaiting a lookup: 1" in result.output
    assert "stopped at --limit 1" in result.output


def test_a_limit_below_one_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        raise AssertionError("a refused option opened a database")

    monkeypatch.setattr(db_session, "Database", refuse)
    result = runner.invoke(app, ["yt", "tg-lookup", "--limit", "0"])
    assert result.exit_code == 2
