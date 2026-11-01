"""`itgraph yt mention-lookup`: what it asks, in what order, what it keeps.

The names come from the seeds' own descriptions; a name any source
already looked up costs nothing; the most-referenced names go first; a
stopped run keeps its answers; nothing it logs names a handle. Every
id and handle is invented, and nothing touches a socket.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

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
    video_id,
    video_resource,
)
from ytinventory import arrange, channels, featured, video

from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtCommand,
    YtLookupKind,
    YtQuotaPool,
    YtRawLookup,
    YtRequest,
)
from itgraph.db.session import Database
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_quota import POOL_OF, units_to_leave
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.lookup_pass import LookupSummary
from itgraph.yt.mention_lookup import run_mention_lookups

NOW = datetime.now(UTC)
SEED = ChannelStatus.SEED
OWNER = 7
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
        YtCommand.MENTION_LOOKUP,
        transport=fake.transport(),
        sleep=no_sleep,
        limit=limit,
    ) as connected:
        yield connected


async def _run(
    database: Database, fake: FakeYouTube, *, limit: int | None = None
) -> LookupSummary:
    async with client(database, fake, limit=limit) as yt:
        return await run_mention_lookups(yt, database)


async def _stored(database: Database) -> dict[tuple[YtLookupKind, str], Any]:
    async with database.session() as session:
        rows = (await session.scalars(select(YtRawLookup))).all()
    return {(row.kind, row.value): row.channel_id for row in rows}


async def _given(database: Database, *steps: Any) -> None:
    for step in steps:
        await step(database)


def _fake() -> FakeYouTube:
    return FakeYouTube(
        channel_resource(8, handle="fake-unknown"),
        channel_resource(9, handle="fake-other"),
        legacy={"fakelegacy": channel_id(8)},
        videos=[video_resource(50, published=NOW, channel=OWNER)],
    )


async def test_names_from_descriptions_are_looked_up(
    database: Database,
) -> None:
    await _given(
        database,
        channels({1: SEED}),
        video(
            1,
            channel=1,
            description=(
                f"youtu.be/{video_id(50)} youtube.com/@fake-unknown "
                "youtube.com/user/FakeLegacy"
            ),
        ),
    )

    summary = await _run(database, _fake())

    assert await _stored(database) == {
        (YtLookupKind.VIDEO, video_id(50)): channel_id(OWNER),
        (YtLookupKind.HANDLE, "fake-unknown"): channel_id(8),
        (YtLookupKind.LEGACY, "fakelegacy"): channel_id(8),
    }
    assert summary.requests == 3
    assert summary.awaiting == 0


async def test_a_name_looked_up_for_telegram_costs_nothing(
    database: Database,
) -> None:
    await _given(
        database,
        channels({1: SEED}),
        video(1, channel=1, description="youtube.com/@fake-unknown"),
    )
    async with database.session() as session:
        await store_lookups(
            session,
            {(YtLookupKind.HANDLE, "fake-unknown"): channel_id(8)},
            fetched_at=NOW,
        )
    fake = _fake()

    summary = await _run(database, fake)

    assert fake.requests == []
    assert summary.lines() == ["nothing awaits a lookup"]


async def test_a_channel_id_and_a_featured_id_are_never_looked_up(
    database: Database,
) -> None:
    await _given(
        database,
        channels({1: SEED}),
        video(
            1, channel=1, description=f"youtube.com/channel/{channel_id(9)}"
        ),
        featured(1, 8),
    )
    fake = _fake()

    await _run(database, fake)

    assert fake.requests == []


async def test_the_most_referenced_names_first(database: Database) -> None:
    await _given(
        database,
        channels({1: SEED, 2: SEED}),
        video(1, channel=1, description="youtube.com/@fake-other"),
        video(2, channel=1, description="youtube.com/@fake-unknown"),
        video(3, channel=2, description="youtube.com/@fake-unknown"),
    )

    summary = await _run(database, _fake(), limit=1)

    assert await _stored(database) == {
        (YtLookupKind.HANDLE, "fake-unknown"): channel_id(8)
    }
    assert summary.limited is not None
    assert summary.awaiting == 1


async def test_a_stopped_run_resumes(database: Database) -> None:
    await _given(
        database,
        channels({1: SEED}),
        video(
            1,
            channel=1,
            description="youtube.com/@fake-unknown youtube.com/@fake-other",
        ),
    )
    fake = _fake()

    await _run(database, fake, limit=1)
    await _run(database, fake)

    assert len(fake.calls("channels")) == 2
    assert len(await _stored(database)) == 2


async def test_spent_quota_keeps_what_was_done(database: Database) -> None:
    await _given(
        database,
        channels({1: SEED}),
        video(
            1,
            channel=1,
            description="youtube.com/@fake-other youtube.com/@fake-unknown",
        ),
    )
    fake = _fake()
    fake.queued = [
        httpx.Response(200, json={"items": [{"id": channel_id(9)}]}),
        httpx.Response(403, json=error_body(403, "quotaExceeded")),
    ]

    summary = await _run(database, fake)

    assert summary.stopped is not None
    assert await _stored(database) == {
        (YtLookupKind.HANDLE, "fake-other"): channel_id(9)
    }


async def test_it_spends_the_general_pool_with_no_floor(
    database: Database,
) -> None:
    assert POOL_OF[YtCommand.MENTION_LOOKUP] is YtQuotaPool.GENERAL
    assert units_to_leave(YtCommand.MENTION_LOOKUP) == 0
    await _given(
        database,
        channels({1: SEED}),
        video(1, channel=1, description="youtube.com/@fake-unknown"),
    )

    await _run(database, _fake())

    async with database.session() as session:
        [row] = await session.scalars(select(YtRequest))
    assert (row.command, row.pool) == (
        YtCommand.MENTION_LOOKUP,
        YtQuotaPool.GENERAL,
    )


async def test_logs_name_ids_only(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await _given(
        database,
        channels({1: SEED}),
        video(
            1,
            channel=1,
            description="youtube.com/@fake-unknown youtube.com/user/FakeLegacy",
        ),
    )

    await _run(database, _fake())

    assert "fake-unknown" not in caplog.text
    assert "fakelegacy" not in caplog.text.lower()
    assert "youtube.com/" not in caplog.text


# --- the command ---------------------------------------------------------


def test_the_command_reports_like_tg_lookup(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrange(
        database_url,
        channels({1: SEED}),
        video(1, channel=1, description="youtube.com/@fake-unknown"),
    )
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    fake = _fake()
    real = httpx.AsyncClient.__init__

    def faked(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = fake.transport()
        real(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", faked)

    result = runner.invoke(app, ["yt", "mention-lookup"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split()[:4] == ["videos", "looked", "up", "0,"]
    assert "requests 1" in lines
    assert "still awaiting a lookup: 0" in lines
    assert "fake-unknown" not in result.output


def test_nothing_to_look_up_says_so(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrange(database_url, channels({1: SEED}))
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))

    result = runner.invoke(app, ["yt", "mention-lookup"])

    assert result.exit_code == 0, result.output
    assert "nothing awaits a lookup" in result.output
