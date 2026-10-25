"""The import: resolved before recorded, nothing spent twice, nothing reviewed."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
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
    YtChannelKind,
    YtCommand,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_quota import QuotaExhaustedError, book, record_exhaustion
from itgraph.yt import client as yt_client
from itgraph.yt.add import add_channels
from itgraph.yt.client import YouTube, youtube
from itgraph.yt_entries import parse_entries, read_entries

runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube, limit: int | None = None
) -> AsyncIterator[YouTube]:
    async with youtube(
        database,
        YtCommand.ADD,
        limit=limit,
        transport=fake.transport(),
        sleep=no_sleep,
    ) as connected:
        yield connected


async def _records(database: Database) -> dict[str, YtChannel]:
    async with database.session() as session:
        rows = await session.scalars(select(YtChannel))
        return {row.channel_id: row for row in rows}


async def _payload_count(database: Database) -> int:
    async with database.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(YtRawChannel)
        )
    return int(count or 0)


async def test_a_handle_is_added_unreviewed(database: Database) -> None:
    fake = FakeYouTube(channel_resource(1, handle="fakeone"))

    async with client(database, fake) as yt:
        summary = await add_channels(yt, database, parse_entries(["@fakeone"]))

    assert [resource["id"] for _, resource in summary.added] == [channel_id(1)]
    record = (await _records(database))[channel_id(1)]
    assert record.status is ChannelStatus.CANDIDATE
    assert record.discovered_via is YtDiscoverySource.MANUAL
    assert (record.kind, record.lang, record.reviewed_at) == (None, None, None)
    assert await _payload_count(database) == 1


async def test_an_existing_review_is_never_overwritten(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.SEED
        record.kind = YtChannelKind.EDUCATION
        record.lang = YtLang.OTHER
        record.reviewed_at = datetime(2026, 10, 1, tzinfo=UTC)
    before = (await _records(database))[channel_id(1)]
    fake = FakeYouTube(channel_resource(1, handle="fakeone"))

    async with client(database, fake) as yt:
        summary = await add_channels(yt, database, parse_entries(["@fakeone"]))

    after = (await _records(database))[channel_id(1)]
    assert summary.added == []
    assert summary.refreshed == parse_entries(["@fakeone"])
    for column in (
        "status",
        "kind",
        "lang",
        "reject_reason",
        "reviewed_at",
        "discovered_via",
        "first_seen_at",
    ):
        assert getattr(after, column) == getattr(before, column), column
    # Its data is current, though.
    assert await _payload_count(database) == 1


async def test_two_entries_naming_one_channel_make_one_record(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1, handle="fakeone"))
    entries = parse_entries(["@fakeone", channel_id(1)])

    async with client(database, fake) as yt:
        summary = await add_channels(yt, database, entries)

    assert len(await _records(database)) == 1
    [(second, first)] = summary.same
    assert first.text == "@fakeone"
    assert second.text == channel_id(1)


async def test_an_entry_the_inventory_answers_costs_nothing(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(1), channel_id(2)])
        await store_payloads(
            session,
            {channel_id(2): channel_resource(2, handle="FakeTwo")},
            fetched_at=datetime.now(UTC),
        )
    fake = FakeYouTube()

    async with client(database, fake) as yt:
        summary = await add_channels(
            yt, database, parse_entries([channel_id(1), "@faketwo"])
        )

    assert fake.requests == []
    assert len(summary.known) == 2


async def test_a_run_is_bounded_by_requests_not_entries(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(9)])
    fake = FakeYouTube(*(channel_resource(n) for n in range(5)))
    entries = parse_entries(
        [channel_id(9)] + [f"@fakehandle{n}" for n in range(5)]
    )

    async with client(database, fake, limit=3) as yt:
        summary = await add_channels(yt, database, entries)

    assert len(fake.requests) == 3
    assert summary.limited is not None
    assert len(summary.known) == 1  # did not count towards the three
    assert len(summary.added) == 3
    assert len(summary.unreached) == 2


async def test_a_run_resumes_from_the_same_input(database: Database) -> None:
    fake = FakeYouTube(*(channel_resource(n) for n in range(5)))
    entries = parse_entries([f"@fakehandle{n}" for n in range(5)])

    async with client(database, fake, limit=2) as yt:
        await add_channels(yt, database, entries)
    fake.requests.clear()
    async with client(database, fake) as yt:
        summary = await add_channels(yt, database, entries)

    assert len(fake.requests) == 3
    assert len(summary.known) == 2
    assert len(summary.added) == 3


async def test_ids_share_requests(database: Database) -> None:
    fake = FakeYouTube(*(channel_resource(n) for n in range(100)))

    async with client(database, fake) as yt:
        summary = await add_channels(
            yt, database, parse_entries([channel_id(n) for n in range(100)])
        )

    assert len(fake.requests) == 2
    assert len(summary.added) == 100


async def test_an_entry_naming_no_channel_creates_nothing(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(2, handle="faketwo"))
    entries = parse_entries(["@nobodyhere", channel_id(3), "@faketwo"])

    async with client(database, fake) as yt:
        summary = await add_channels(yt, database, entries)

    assert [entry.text for entry in summary.not_found] == [
        "@nobodyhere",
        channel_id(3),
    ]
    assert list(await _records(database)) == [channel_id(2)]


async def test_a_spent_quota_keeps_what_was_done(database: Database) -> None:
    fake = FakeYouTube(*(channel_resource(n) for n in range(3)))
    fake.queued = [
        httpx.Response(
            200,
            json={"items": [channel_resource(0)]},
        ),
        httpx.Response(403, json=error_body(403, "quotaExceeded")),
    ]

    async with client(database, fake) as yt:
        summary = await add_channels(
            yt, database, parse_entries([f"@fakehandle{n}" for n in range(3)])
        )

    assert isinstance(summary.stopped, QuotaExhaustedError)
    assert list(await _records(database)) == [channel_id(0)]
    assert len(summary.unreached) == 2


# --- the command -------------------------------------------------------


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    original_youtube = yt_client.youtube

    def faked(
        database: Database, command: YtCommand, *, limit: int | None = None
    ) -> object:
        return original_youtube(
            database,
            command,
            limit=limit,
            transport=fake.transport(),
            sleep=no_sleep,
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


def test_the_command_reads_standard_input(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    fake = FakeYouTube(channel_resource(1, handle="fakeone", title="Fake One"))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(
        app,
        ["yt", "add", "--from-file", "-"],
        input="# seeds\n\n@fakeone\n",
    )

    assert result.exit_code == 0, result.output
    assert "added 1" in result.output
    assert f"+ {channel_id(1)}  @fakeone  Fake One" in result.output


def test_the_command_reads_a_file(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("@fakehandle1\n", encoding="utf-8")

    result = runner.invoke(app, ["yt", "add", "--from-file", str(seeds)])

    assert result.exit_code == 0, result.output
    assert "added 1" in result.output


def test_a_bad_form_is_refused_before_anything_is_spent(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(
        app,
        ["yt", "add", "--from-file", "-"],
        input="@fakehandle1\nhttps://www.youtube.com/c/FakeCustom\n"
        "https://youtu.be/fakevideo00\n",
    )

    assert result.exit_code == 2
    assert "line 2" in result.output and "line 3" in result.output
    assert fake.requests == []
    assert asyncio.run(_count(database_url)) == 0


async def _count(url: str) -> int:
    database = Database(url)
    try:
        return len(await _records(database))
    finally:
        await database.dispose()


def test_failures_written_back_out_as_input(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))
    out = tmp_path / "failed.txt"

    result = runner.invoke(
        app,
        ["yt", "add", "@fakehandle1", "@nobodyhere", "--failures", str(out)],
    )

    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert text == "@nobodyhere  # YouTube returns no channel for it\n"
    # And the file reads back as input.
    assert [entry.text for entry in read_entries(text)] == ["@nobodyhere"]


def test_failures_on_stdout_and_the_report_on_stderr(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))

    result = runner.invoke(
        app,
        ["yt", "add", "@fakehandle1", "@nobodyhere", "--failures", "-"],
    )

    assert result.exit_code == 0, result.output
    assert (
        result.stdout == "@nobodyhere  # YouTube returns no channel for it\n"
    )
    assert "added 1" in result.stderr


def test_no_failures_no_file(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))
    out = tmp_path / "failed.txt"

    result = runner.invoke(
        app, ["yt", "add", "@fakehandle1", "--failures", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert not out.exists()


def test_a_stopped_run_fails_the_command(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    fake = FakeYouTube()
    fake.queued = [httpx.Response(403, json=error_body(403, "quotaExceeded"))]
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "add", "@fakehandle1"])

    assert result.exit_code == 1
    assert "quotaExceeded" in result.stderr


async def _exhaust(url: str) -> None:
    database = Database(url)
    try:
        await record_exhaustion(database, command=YtCommand.REFRESH)
    finally:
        await database.dispose()


def test_a_later_command_refuses_to_start_on_an_exhausted_day(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Exhausted by another command earlier today: nothing is sent, and
    the refusal says when sending can resume."""
    asyncio.run(_exhaust(database_url))
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "add", "@fakehandle1"])

    assert result.exit_code == 1
    assert "quotaExceeded" in result.stderr
    assert "UTC" in result.stderr and "here)" in result.stderr
    assert fake.requests == []


async def _spend_the_pool(url: str, units: int) -> None:
    database = Database(url)
    try:
        await book(
            database,
            command=YtCommand.REFRESH,
            method="channels.list",
            units=units,
        )
    finally:
        await database.dispose()


def test_a_spent_pool_stops_the_command_before_sending(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The pool's own cap, not YouTube's: our count says no first."""
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 3)
    asyncio.run(_spend_the_pool(database_url, 7))
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "add", "@fakehandle1"])

    assert result.exit_code == 1
    assert "general YouTube quota pool is spent" in result.stderr
    assert fake.requests == []
    assert asyncio.run(_count(database_url)) == 0
