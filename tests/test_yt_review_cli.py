"""Review by file and by command, and the listing — all offline."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from typer.testing import CliRunner
from ytfakes import channel_id, channel_resource

from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, mark_gone, store_payloads
from itgraph.yt_review import COLUMNS

runner = CliRunner()


@pytest.fixture
def inventory(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> Iterator[str]:
    """The test database, and no way to build an HTTP client."""
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an offline command built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)
    yield database_url


def _run(url: str, body: Any) -> Any:
    async def go() -> Any:
        database = Database(url)
        try:
            return await body(database)
        finally:
            await database.dispose()

    return asyncio.run(go())


def _channels(url: str, *numbers: int, stored: bool = True) -> None:
    async def body(database: Database) -> None:
        async with database.session() as session:
            await create_channels(session, [channel_id(n) for n in numbers])
            if stored:
                await store_payloads(
                    session,
                    {
                        channel_id(n): channel_resource(
                            n,
                            title=f"Fake Channel {n}",
                            language="en",
                            country="US",
                        )
                        for n in numbers
                    },
                    fetched_at=datetime.now(UTC),
                )

    _run(url, body)


def _review(url: str, number: int, **values: Any) -> None:
    async def body(database: Database) -> None:
        async with database.session() as session:
            record = await session.get(YtChannel, channel_id(number))
            assert record is not None
            for column, value in values.items():
                setattr(record, column, value)
            record.reviewed_at = values.get("reviewed_at", datetime.now(UTC))

    _run(url, body)


def _record(url: str, number: int) -> YtChannel:
    async def body(database: Database) -> YtChannel:
        async with database.session() as session:
            record = await session.get(YtChannel, channel_id(number))
            assert record is not None
            return record

    result: YtChannel = _run(url, body)
    return result


def _rows(text: str) -> dict[str, dict[str, str]]:
    rows = {}
    for line in text.splitlines():
        if line.startswith("UC"):
            row = dict(zip(COLUMNS, line.split("\t"), strict=True))
            rows[row["channel_id"]] = row
    return rows


def _fill(text: str, number: int, **values: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(channel_id(number)):
            fields = line.split("\t")
            for column, value in values.items():
                fields[COLUMNS.index(column)] = value
            lines[index] = "\t".join(fields)
    return "\n".join(lines) + "\n"


def _export(*args: str) -> str:
    result = runner.invoke(app, ["yt", "review-export", *args])
    assert result.exit_code == 0, result.output
    return result.stdout


# --- export --------------------------------------------------------------


def test_unreviewed_channels_are_exported_by_default(inventory: str) -> None:
    _channels(inventory, 1, 2)
    _review(inventory, 2, status=ChannelStatus.MAYBE)

    rows = _rows(_export())

    assert list(rows) == [channel_id(1)]
    row = rows[channel_id(1)]
    assert row["handle"] == "@fakehandle1"
    assert row["title"] == "Fake Channel 1"
    assert row["subscribers"] == "1200"
    assert row["declared"] == "en/US"
    assert row["link"] == f"https://www.youtube.com/channel/{channel_id(1)}"
    assert row["decision"] == row["kind"] == ""


def test_undecided_channels_come_back_with_what_was_decided(
    inventory: str,
) -> None:
    _channels(inventory, 1)
    _review(
        inventory,
        1,
        status=ChannelStatus.MAYBE,
        kind=YtChannelKind.MEDIA,
        lang=YtLang.EN,
        kind_note="looks like a newsroom",
    )

    row = _rows(_export("--status", "maybe"))[channel_id(1)]

    assert (row["decision"], row["kind"], row["lang"], row["note"]) == (
        "maybe",
        "media",
        "en",
        "looks like a newsroom",
    )


def test_a_channel_without_data_is_exported_by_id_and_link(
    inventory: str,
) -> None:
    _channels(inventory, 1, stored=False)

    row = _rows(_export())[channel_id(1)]

    assert row["title"] == row["handle"] == ""
    assert row["link"].endswith(channel_id(1))


def test_export_header_and_count(inventory: str) -> None:
    _channels(inventory, 1)

    result = runner.invoke(app, ["yt", "review-export"])

    assert result.stdout.startswith("# itgraph yt review-export\n")
    assert "1 channels exported" in result.stderr


# --- apply ---------------------------------------------------------------


def _apply(text: str) -> Any:
    return runner.invoke(app, ["yt", "review-apply", "-"], input=text)


def test_decisions_are_applied(inventory: str) -> None:
    _channels(inventory, 1, 2, 3, 4)
    text = _export()
    text = _fill(
        text, 1, decision="seed", kind="personal", lang="en", note="dev"
    )
    text = _fill(text, 2, decision="maybe")
    text = _fill(
        text, 3, decision="reject", reason="consumer_tech", note="gadgets"
    )

    result = _apply(text)

    assert result.exit_code == 0, result.output
    assert "applied 3, unchanged 0, conflicts 0, left blank 1" in result.output
    seed = _record(inventory, 1)
    assert (seed.status, seed.kind, seed.lang, seed.kind_note) == (
        ChannelStatus.SEED,
        YtChannelKind.PERSONAL,
        YtLang.EN,
        "dev",
    )
    assert _record(inventory, 2).status is ChannelStatus.MAYBE
    rejected = _record(inventory, 3)
    assert (rejected.reject_reason, rejected.reject_note) == (
        YtRejectReason.CONSUMER_TECH,
        "gadgets",
    )
    assert rejected.reviewed_at is not None
    blank = _record(inventory, 4)
    assert (blank.status, blank.reviewed_at) == (ChannelStatus.CANDIDATE, None)


def test_applying_a_file_twice_changes_nothing_the_second_time(
    inventory: str,
) -> None:
    _channels(inventory, 1)
    text = _fill(_export(), 1, decision="seed", kind="company", lang="ru")
    assert _apply(text).exit_code == 0
    first = _record(inventory, 1).reviewed_at

    result = _apply(text)

    assert result.exit_code == 0, result.output
    assert "applied 0, unchanged 1" in result.output
    assert _record(inventory, 1).reviewed_at == first


def test_a_row_changed_since_the_export_is_a_conflict(inventory: str) -> None:
    _channels(inventory, 1, 2)
    text = _export()
    _review(
        inventory,
        1,
        status=ChannelStatus.REJECTED,
        reject_reason=YtRejectReason.NOT_IT,
        reviewed_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    text = _fill(text, 1, decision="seed", kind="personal", lang="en")
    text = _fill(text, 2, decision="maybe")

    result = _apply(text)

    assert result.exit_code == 0, result.output
    assert "applied 1, unchanged 0, conflicts 1" in result.output
    assert "use `itgraph yt mark`" in result.output
    assert _record(inventory, 1).status is ChannelStatus.REJECTED
    assert _record(inventory, 2).status is ChannelStatus.MAYBE


def test_read_only_edits_have_no_effect(inventory: str) -> None:
    _channels(inventory, 1)
    text = _fill(
        _export(), 1, decision="maybe", title="Edited Title", handle="@edited"
    )

    assert _apply(text).exit_code == 0

    exported = _rows(_export("--status", "maybe"))[channel_id(1)]
    assert exported["title"] == "Fake Channel 1"


def test_every_mistake_at_once_and_nothing_written(inventory: str) -> None:
    _channels(inventory, *range(1, 7))
    text = _export()
    text = _fill(text, 1, decision="seed", lang="en")
    text = _fill(text, 2, decision="seed", kind="personal")
    text = _fill(text, 3, decision="reject")
    text = _fill(text, 4, decision="seed", kind="persnal", lang="en")
    text = _fill(text, 5, decision="maybe")  # correct, and still not written
    text += f"{channel_id(99)}\tmaybe\n"  # not in the inventory
    text += f"{channel_id(6)}\tmaybe\n"
    text += f"{channel_id(6)}\tmaybe\n"  # listed twice

    result = _apply(text)

    assert result.exit_code == 1
    for said in (
        "needs a kind",
        "needs a lang",
        "needs a reason",
        "'persnal'",
        "is not in the inventory",
        "listed twice",
    ):
        assert said in result.stderr, said
    assert _record(inventory, 5).status is ChannelStatus.CANDIDATE


def test_a_forgotten_decision_and_a_stray_reason_are_mistakes(
    inventory: str,
) -> None:
    _channels(inventory, 1, 2, 3)
    text = _export()
    text = _fill(text, 1, kind="media", lang="en")  # no decision
    text = _fill(text, 2, decision="maybe", reason="ads")
    text = _fill(text, 3, decision="maybe")  # correct, and still not written

    result = _apply(text)

    assert result.exit_code == 1
    assert "no decision" in result.stderr
    assert "only to a rejection" in result.stderr
    for number in (1, 2, 3):
        assert _record(inventory, number).status is ChannelStatus.CANDIDATE


def test_a_file_without_its_header_is_refused(inventory: str) -> None:
    _channels(inventory, 1)
    text = _fill(_export(), 1, decision="maybe")
    text = "\n".join(
        line for line in text.splitlines() if not line.startswith("# exported")
    )

    result = _apply(text)

    assert result.exit_code == 1
    assert "export moment" in result.stderr
    assert _record(inventory, 1).status is ChannelStatus.CANDIDATE


def test_an_expired_file_is_refused(inventory: str) -> None:
    _channels(inventory, 1)
    old = (datetime.now(UTC) - timedelta(days=31)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    text = _fill(_export(), 1, decision="maybe")
    text = "\n".join(
        f"# exported: {old}" if line.startswith("# exported") else line
        for line in text.splitlines()
    )

    result = _apply(text)

    assert result.exit_code == 1
    assert "Delete it and export a new one" in result.stderr


# --- mark ----------------------------------------------------------------


def _mark(*args: str) -> Any:
    return runner.invoke(app, ["yt", "mark", *args])


def test_accepting_a_channel(inventory: str) -> None:
    _channels(inventory, 1)

    result = _mark(channel_id(1), "--seed", "--kind", "event", "--lang", "en")

    assert result.exit_code == 0, result.output
    record = _record(inventory, 1)
    assert (record.status, record.kind, record.lang) == (
        ChannelStatus.SEED,
        YtChannelKind.EVENT,
        YtLang.EN,
    )
    assert record.reviewed_at is not None


@pytest.mark.parametrize(
    "missing", [["--kind", "event"], ["--lang", "en"], []]
)
def test_accepting_without_a_kind_or_a_language_fails(
    inventory: str, missing: list[str]
) -> None:
    _channels(inventory, 1)

    result = _mark(channel_id(1), "--seed", *missing)

    assert result.exit_code == 2
    assert _record(inventory, 1).status is ChannelStatus.CANDIDATE


def test_rejecting_and_deferring(inventory: str) -> None:
    _channels(inventory, 1, 2)

    assert (
        _mark(
            channel_id(1),
            "--reject",
            "--reason",
            "infobiz",
            "--note",
            "courses",
        ).exit_code
        == 0
    )
    assert _mark(channel_id(2), "--maybe").exit_code == 0

    rejected = _record(inventory, 1)
    assert (rejected.reject_reason, rejected.reject_note) == (
        YtRejectReason.INFOBIZ,
        "courses",
    )
    assert _record(inventory, 2).status is ChannelStatus.MAYBE


def test_a_decision_can_be_changed(inventory: str) -> None:
    _channels(inventory, 1)
    _mark(channel_id(1), "--reject", "--reason", "not_it")

    result = _mark(
        channel_id(1), "--seed", "--kind", "personal", "--lang", "ru"
    )

    assert result.exit_code == 0, result.output
    record = _record(inventory, 1)
    assert (record.status, record.reject_reason) == (ChannelStatus.SEED, None)


def test_deferring_keeps_the_labels_it_was_not_given(inventory: str) -> None:
    """`mark` shows no current values, so leaving a flag out is never a
    choice to clear what a reviewer wrote."""
    _channels(inventory, 1)
    _mark(
        channel_id(1),
        "--seed",
        "--kind",
        "media",
        "--lang",
        "en",
        "--note",
        "a newsroom",
    )

    result = _mark(channel_id(1), "--maybe")

    assert result.exit_code == 0, result.output
    record = _record(inventory, 1)
    assert (record.status, record.kind, record.lang, record.kind_note) == (
        ChannelStatus.MAYBE,
        YtChannelKind.MEDIA,
        YtLang.EN,
        "a newsroom",
    )


def test_rejecting_keeps_kind_and_language(inventory: str) -> None:
    _channels(inventory, 1)
    _mark(channel_id(1), "--seed", "--kind", "company", "--lang", "ru")

    assert _mark(channel_id(1), "--reject", "--reason", "ads").exit_code == 0

    record = _record(inventory, 1)
    assert (record.kind, record.lang) == (YtChannelKind.COMPANY, YtLang.RU)
    # A rejection's note goes with its reason: none was given.
    assert record.reject_note is None


def test_a_label_given_replaces_the_old_one(inventory: str) -> None:
    _channels(inventory, 1)
    _mark(channel_id(1), "--maybe", "--kind", "event", "--note", "old")

    _mark(channel_id(1), "--maybe", "--kind", "education", "--note", "new")

    record = _record(inventory, 1)
    assert (record.kind, record.kind_note) == (YtChannelKind.EDUCATION, "new")


@pytest.mark.parametrize("ref", ["@FakeHandle1", "fakehandle1"])
def test_addressing_a_channel_by_handle(inventory: str, ref: str) -> None:
    _channels(inventory, 1)

    result = _mark(ref, "--maybe")

    assert result.exit_code == 0, result.output
    assert channel_id(1) in result.output


def test_a_handle_carried_by_two_records(inventory: str) -> None:
    async def body(database: Database) -> None:
        async with database.session() as session:
            await create_channels(session, [channel_id(1), channel_id(2)])
            await store_payloads(
                session,
                {
                    channel_id(1): channel_resource(1, handle="sharedfake"),
                    channel_id(2): channel_resource(2, handle="sharedfake"),
                },
                fetched_at=datetime.now(UTC),
            )

    _run(inventory, body)

    result = _mark("@sharedfake", "--maybe")

    assert result.exit_code == 1
    assert channel_id(1) in result.stderr and channel_id(2) in result.stderr


def test_reviewing_an_unknown_channel(inventory: str) -> None:
    result = _mark(channel_id(5), "--maybe")

    assert result.exit_code == 1
    assert "no YouTube channel" in result.stderr


# --- listing -------------------------------------------------------------


def test_listing_by_status(inventory: str) -> None:
    _channels(inventory, 1, 2)
    _mark(channel_id(1), "--seed", "--kind", "personal", "--lang", "en")

    result = runner.invoke(app, ["yt", "channels", "--status", "seed"])

    assert result.exit_code == 0, result.output
    [line] = result.stdout.splitlines()
    assert line.split()[:2] == [channel_id(1), "@fakehandle1"]
    assert line.split()[-3:] == ["seed", "personal", "en"]


def test_channels_without_data_are_said_to_be(inventory: str) -> None:
    _channels(inventory, 1, stored=False)
    _channels(inventory, 2)

    async def gone(database: Database) -> None:
        async with database.session() as session:
            await mark_gone(session, [channel_id(2)], now=datetime.now(UTC))

    _run(inventory, gone)

    result = runner.invoke(app, ["yt", "channels", "--status", "candidate"])

    assert "(expired)" in result.stdout
    assert "(gone)" in result.stdout


def test_the_progress_summary(inventory: str) -> None:
    _channels(inventory, 1, 2, 3)
    _channels(inventory, 4, stored=False)
    _mark(channel_id(1), "--seed", "--kind", "personal", "--lang", "en")
    _mark(channel_id(2), "--seed", "--kind", "company", "--lang", "other")
    _mark(channel_id(3), "--reject", "--reason", "crypto")

    result = runner.invoke(app, ["yt", "channels"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "seed       2" in lines
    assert "rejected   1" in lines
    assert "seeds      en 1, ru 0, other 1" in lines
    assert "data       current 3, expired 1, gone 0" in lines


def test_reviewed_records_are_never_deleted(inventory: str) -> None:
    _channels(inventory, 1)
    _mark(channel_id(1), "--reject", "--reason", "ads")

    async def count(database: Database) -> int:
        async with database.session() as session:
            return len((await session.scalars(select(YtChannel))).all())

    assert _run(inventory, count) == 1
