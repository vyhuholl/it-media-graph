"""`itgraph yt rising-lookups`: both passes, bounded, recorded.

The two queues are drained by one scheduled run, each pass stopped
after its setting's requests; nothing to look up is still a run; a
spent pool records a stopped run and refuses nothing to whoever runs
next. Every id and handle is invented, and nothing touches a socket.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from tglinks import seed_linking
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_resource,
    no_sleep,
    use_key,
)
from ytinventory import channels, video

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtCommand,
    YtLookupKind,
    YtLookupRun,
    YtPassOutcome,
    YtQuotaExhausted,
    YtRawLookup,
)
from itgraph.db.session import Database
from itgraph.db.yt_quota import book
from itgraph.yt import client as yt_client

NOW = datetime.now(UTC)
SEED = ChannelStatus.SEED
OWNER = 7
runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    _fake_the_api(monkeypatch, fake)


def _fake_the_api(monkeypatch: pytest.MonkeyPatch, fake: FakeYouTube) -> None:
    """Put the fake behind the client the timer's code opens."""
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


async def _run_row(database: Database) -> YtLookupRun | None:
    async with database.session() as session:
        rows = await session.scalars(select(YtLookupRun))
        [row] = list(rows)
    return row


async def _stored(database: Database) -> set[tuple[YtLookupKind, str]]:
    async with database.session() as session:
        rows = (await session.scalars(select(YtRawLookup))).all()
    return {(row.kind, row.value) for row in rows}


def _fake_with_handles(names: list[str]) -> FakeYouTube:
    """YouTube that knows the channel behind each fake handle."""
    return FakeYouTube(
        *(
            channel_resource(100 + n, handle=name)
            for n, name in enumerate(names)
        )
    )


def _tg_links(names: list[str]) -> list[str]:
    return [f"youtube.com/@{name}" for name in names]


TG_HANDLES = [f"fake-unk{n}" for n in range(30)]
DESCRIPTION_HANDLES = [f"fake-desc{n}" for n in range(5)]


async def _both_queues(database: Database) -> None:
    """Thirty Telegram-linked handles and five description-referenced
    ones, no two queues sharing a name. The inventory helpers are steps
    a Database is given; `arrange` is only their synchronous wrapper."""
    for step in (
        channels({1: SEED, OWNER: SEED}),
        *(
            video(n, channel=1, description=f"see youtube.com/@{handle}")
            for n, handle in enumerate(DESCRIPTION_HANDLES, start=1)
        ),
    ):
        await step(database)
    await seed_linking(database, 2001, *_tg_links(TG_HANDLES))


# --- both passes run bounded ------------------------------------------


async def test_both_passes_run_under_their_bounds(
    database: Database, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thirty Telegram handles and five description handles, limits of
    ten and ten: the first pass stops after its ten requests, the second
    spends five, every answer is stored."""
    await _both_queues(database)
    fake = _fake_with_handles(TG_HANDLES + DESCRIPTION_HANDLES)
    _fake_the_api(monkeypatch, fake)

    from itgraph.yt.rising_lookups import run_rising_lookups

    summary = await run_rising_lookups(database)

    assert summary.tg is not None and summary.tg.limited is not None
    assert summary.tg.requests == 10
    assert summary.tg.awaiting == 20
    assert summary.mention is not None and summary.mention.limited is None
    assert summary.mention.requests == 5
    assert summary.mention.awaiting == 0
    # Ten of the thirty handles answered, and the five descriptions'.
    assert len(await _stored(database)) == 15
    row = await _run_row(database)
    assert row is not None
    assert row.outcome is YtPassOutcome.COMPLETE
    assert (row.tg_requests, row.tg_awaiting) == (10, 20)
    assert (row.mention_requests, row.mention_awaiting) == (5, 0)
    assert row.error is None


async def test_nothing_to_look_up_is_still_a_run(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeYouTube()
    _fake_the_api(monkeypatch, fake)
    from itgraph.yt.rising_lookups import run_rising_lookups

    summary = await run_rising_lookups(database)

    assert summary.tg is not None and summary.tg.requests == 0
    assert summary.mention is not None and summary.mention.requests == 0
    row = await _run_row(database)
    assert row is not None
    assert row.outcome is YtPassOutcome.COMPLETE
    assert (
        row.tg_requests,
        row.tg_awaiting,
        row.mention_requests,
        row.mention_awaiting,
    ) == (0, 0, 0, 0)


async def test_a_spent_pool_records_a_stopped_run(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool spent for the day: no request, the run recorded as
    stopped — and nothing recorded that would refuse an operator's
    command after it."""
    monkeypatch.setattr(settings, "youtube_daily_quota", 1)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 0)
    await book(
        database,
        command=YtCommand.TG_LOOKUP,
        method="channels.list",
        units=1,
    )
    await seed_linking(database, 2001, *_tg_links(TG_HANDLES[:3]))
    fake = _fake_with_handles(TG_HANDLES[:3])
    _fake_the_api(monkeypatch, fake)

    from itgraph.yt.rising_lookups import run_rising_lookups

    await run_rising_lookups(database)

    assert fake.requests == []
    row = await _run_row(database)
    assert row is not None
    assert row.outcome is YtPassOutcome.POOL_SPENT
    assert row.tg_requests == 0
    assert row.error is not None
    # A spent pool is arithmetic, not a day YouTube declared over: the
    # operator's command after it is refused by the pool's cap alone.
    async with database.session() as session:
        exhausted = list(await session.scalars(select(YtQuotaExhausted)))
    assert exhausted == []


# --- the command ------------------------------------------------------


def test_the_command_prints_counts_only(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    fake = _fake_with_handles(TG_HANDLES[:2])
    asyncio.run(
        _seed_two_links(database_url),
    )
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "rising-lookups"])

    assert result.exit_code == 0
    assert "telegram links:" in result.output
    assert "mentions:" in result.output
    assert "handles" in result.output and "found     2" in result.output
    assert "requests 2" in result.output
    # Counts only: no handle the queues hold reaches the output.
    for said in ("fake-unk0", "fake-unk1", "youtube.com"):
        assert said not in result.output


async def _seed_two_links(database_url: str) -> None:
    database = Database(database_url)
    try:
        await seed_linking(database, 2001, *_tg_links(TG_HANDLES[:2]))
    finally:
        await database.dispose()


def test_the_command_without_a_key_records_a_failed_run(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    fake = FakeYouTube()
    _use(monkeypatch, database_url, fake)
    monkeypatch.setattr(settings, "youtube_api_key", None)

    result = runner.invoke(app, ["yt", "rising-lookups"])

    assert result.exit_code == 1
    assert "YOUTUBE_API_KEY" in result.output
    assert fake.requests == []

    async def read() -> YtLookupRun | None:
        database = Database(database_url)
        try:
            async with database.session() as session:
                rows = await session.scalars(select(YtLookupRun))
                [row] = list(rows)
            return row
        finally:
            await database.dispose()

    row = asyncio.run(read())
    assert row is not None
    assert row.outcome is YtPassOutcome.FAILED
    assert "YOUTUBE_API_KEY" in (row.error or "")
