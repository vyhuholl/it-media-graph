from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import (
    FakeChannel,
    FakeFullChannel,
    FakeTelegramClient,
    history,
)
from typer.testing import CliRunner

from itgraph import __version__
from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.tg import auth as tg_auth
from itgraph.tg import backfill as tg_backfill
from itgraph.tg import client as tg_client

runner = CliRunner()

KNOWN = 1000000001


def use_test_database(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Point every command at the throwaway database.

    The command builds its own ``Database`` inside its own event loop —
    handing it one built elsewhere would bind asyncpg to the wrong loop.
    """
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(url))


def use_telegram(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegramClient
) -> None:
    @asynccontextmanager
    async def connected() -> AsyncIterator[FakeTelegramClient]:
        yield telegram

    monkeypatch.setattr(tg_client, "connected", connected)


@pytest.fixture
def inventory(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    telegram: FakeTelegramClient,
) -> Iterator[None]:
    use_test_database(monkeypatch, database_url)
    use_telegram(monkeypatch, telegram)
    yield


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "login" in result.output
    assert "dump-dialogs" in result.output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_login_authorizes_and_disconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.start = AsyncMock()
    client.get_me = AsyncMock(return_value=MagicMock(username="itgraph_bot"))
    client.disconnect = AsyncMock()
    monkeypatch.setattr(tg_client, "build_client", lambda: client)

    result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "itgraph_bot" in result.output
    client.start.assert_awaited_once()
    client.disconnect.assert_awaited_once()


def test_login_qr_never_asks_for_a_phone_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.connect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=False)
    client.start = AsyncMock()
    client.get_me = AsyncMock(return_value=MagicMock(username="itgraph_bot"))
    client.disconnect = AsyncMock()
    monkeypatch.setattr(tg_client, "build_client", lambda: client)
    authorize = AsyncMock()
    monkeypatch.setattr(tg_auth, "authorize_qr", authorize)

    result = runner.invoke(app, ["login", "--qr"])

    assert result.exit_code == 0, result.output
    assert "itgraph_bot" in result.output
    authorize.assert_awaited_once()
    # `start` is the phone-and-code path; --qr must not fall into it.
    client.start.assert_not_awaited()
    client.disconnect.assert_awaited_once()


def test_login_qr_skips_an_already_authorized_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.connect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_me = AsyncMock(return_value=MagicMock(username="itgraph_bot"))
    client.disconnect = AsyncMock()
    monkeypatch.setattr(tg_client, "build_client", lambda: client)
    authorize = AsyncMock()
    monkeypatch.setattr(tg_auth, "authorize_qr", authorize)

    result = runner.invoke(app, ["login", "--qr"])

    assert result.exit_code == 0, result.output
    authorize.assert_not_awaited()


def test_dump_dialogs_reports_counts(inventory: None) -> None:
    first = runner.invoke(app, ["dump-dialogs"])
    second = runner.invoke(app, ["dump-dialogs"])

    assert first.exit_code == 0, first.output
    assert "inserted 3, updated 0, skipped 3 private" in first.output
    assert "inserted 0, updated 3, skipped 3 private" in second.output


def test_an_unauthorized_session_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def refuse() -> AsyncIterator[None]:
        raise tg_client.NotAuthorizedError("no authorized session at x")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(tg_client, "connected", refuse)

    result = runner.invoke(app, ["dump-dialogs"])

    assert result.exit_code == 1
    assert "no authorized session" in result.output


def test_mark_seeds_a_channel(inventory: None) -> None:
    runner.invoke(app, ["dump-dialogs"])

    result = runner.invoke(
        app, ["mark", str(KNOWN), "--seed", "--kind", "media"]
    )
    listing = runner.invoke(app, ["channels", "--status", "seed"])

    assert result.exit_code == 0, result.output
    assert "seed" in result.output
    assert str(KNOWN) in listing.output
    assert "media" in listing.output


def test_mark_rejects_with_a_reason(inventory: None) -> None:
    runner.invoke(app, ["dump-dialogs"])

    result = runner.invoke(
        app,
        [
            "mark",
            str(KNOWN),
            "--reject",
            "--reason",
            "crypto",
            "--note",
            "trading calls",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "rejected" in result.output


def test_mark_accepts_a_username(inventory: None) -> None:
    runner.invoke(app, ["dump-dialogs"])

    at = runner.invoke(app, ["mark", "@example_notes", "--seed"])
    bare = runner.invoke(app, ["mark", "example_jobs", "--maybe"])
    listing = runner.invoke(app, ["channels", "--status", "seed"])

    assert at.exit_code == 0, at.output
    assert bare.exit_code == 0, bare.output
    assert str(KNOWN) in at.output
    assert "@example_notes" in listing.output


def test_mark_an_unknown_username_exits_non_zero(inventory: None) -> None:
    runner.invoke(app, ["dump-dialogs"])

    result = runner.invoke(app, ["mark", "@example_nobody", "--maybe"])

    assert result.exit_code == 1
    assert "no channel @example_nobody" in result.output


def test_mark_needs_exactly_one_outcome(inventory: None) -> None:
    neither = runner.invoke(app, ["mark", str(KNOWN)])
    both = runner.invoke(app, ["mark", str(KNOWN), "--seed", "--maybe"])

    assert neither.exit_code != 0
    assert both.exit_code != 0


def test_mark_refuses_a_reasonless_rejection(inventory: None) -> None:
    runner.invoke(app, ["dump-dialogs"])

    result = runner.invoke(app, ["mark", str(KNOWN), "--reject"])

    assert result.exit_code != 0
    assert "--reason" in result.output


def test_mark_an_unknown_channel_exits_non_zero(inventory: None) -> None:
    result = runner.invoke(app, ["mark", "1", "--maybe"])

    assert result.exit_code == 1
    assert "no channel 1" in result.output


def test_channels_summarises_progress(inventory: None) -> None:
    runner.invoke(app, ["dump-dialogs"])
    runner.invoke(app, ["mark", str(KNOWN), "--maybe"])

    result = runner.invoke(app, ["channels"])

    assert result.exit_code == 0, result.output
    assert "candidate  2" in result.output
    assert "maybe      1" in result.output
    # A listing without a filter still shows every channel.
    assert "@example_notes" in result.output


def collector_client(
    dialog_records: list[dict[str, Any]], *, posts: int
) -> FakeTelegramClient:
    """A client that serves both the dialog list and some history.

    The dialog records matter: the commands under test import the
    inventory first, and a client without them leaves nothing to walk.
    """
    notes = FakeChannel(KNOWN, "example_notes", "Example Notes")
    return FakeTelegramClient(
        dialog_records,
        entities={"example_notes": notes},
        full_channels={KNOWN: FakeFullChannel(notes)},
        histories={KNOWN: history(posts)},
    )


def no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(tg_backfill.asyncio, "sleep", sleep)


def test_backfill_requires_a_cutoff(inventory: None) -> None:
    """An unbounded walk is hours of requests; asking for it is deliberate."""
    result = runner.invoke(app, ["backfill"])

    assert result.exit_code != 0
    assert "--since" in result.output


def test_backfill_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    telegram = collector_client(dialog_records, posts=3)
    use_telegram(monkeypatch, telegram)
    no_sleeping(monkeypatch)

    runner.invoke(app, ["dump-dialogs"])
    runner.invoke(app, ["mark", str(KNOWN), "--seed"])

    result = runner.invoke(app, ["backfill", "--since", "2026-05-01"])

    assert result.exit_code == 0, result.output
    assert "completed 1" in result.output
    assert "3 new messages" in result.output


def test_backfill_caps_a_channel_at_max_messages(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    telegram = collector_client(dialog_records, posts=3)
    use_telegram(monkeypatch, telegram)
    no_sleeping(monkeypatch)

    runner.invoke(app, ["dump-dialogs"])
    runner.invoke(app, ["mark", str(KNOWN), "--seed"])

    result = runner.invoke(
        app,
        ["backfill", "--since", "2026-05-01", "--max-messages", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "1 new messages" in result.output
    # Stopped by the ceiling rather than by the cutoff, and reported as
    # such: the rest of that channel is not going to be collected.
    assert "completed 0" in result.output
    assert "capped 1" in result.output


def test_backfill_rejects_a_negative_ceiling(inventory: None) -> None:
    result = runner.invoke(
        app,
        ["backfill", "--since", "2026-05-01", "--max-messages", "-1"],
    )

    assert result.exit_code != 0


def test_backfill_rejects_a_malformed_date(inventory: None) -> None:
    result = runner.invoke(app, ["backfill", "--since", "last tuesday"])

    assert result.exit_code != 0


def test_channels_can_show_backfill_state(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    """Progress across runs has to be readable without opening psql."""
    use_telegram(monkeypatch, collector_client(dialog_records, posts=2))
    no_sleeping(monkeypatch)

    runner.invoke(app, ["dump-dialogs"])
    runner.invoke(app, ["mark", str(KNOWN), "--seed"])
    runner.invoke(app, ["backfill", "--since", "2026-05-01"])

    result = runner.invoke(app, ["channels", "--backfill"])

    assert result.exit_code == 0, result.output
    assert "complete to 2026-05-01" in result.output


def test_derive_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    """The derivation command runs off stored data and reports a summary.

    The collected history here carries no forwards, so it derives no
    edges — which is exactly the point: the command wires up and reports
    without ever reaching for the network.
    """
    telegram = collector_client(dialog_records, posts=3)
    use_telegram(monkeypatch, telegram)
    no_sleeping(monkeypatch)

    runner.invoke(app, ["dump-dialogs"])
    runner.invoke(app, ["mark", str(KNOWN), "--seed"])
    runner.invoke(app, ["backfill", "--since", "2026-05-01"])

    result = runner.invoke(app, ["derive"])

    assert result.exit_code == 0, result.output
    assert "edges written" in result.output


def test_resolve_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    """Imported channels are already resolved, so a fresh run has no work.

    Every dialog-imported channel carries a username, so it is born
    resolved and the queue is empty — the command reports zero rather than
    re-resolving what is already known.
    """
    use_telegram(monkeypatch, collector_client(dialog_records, posts=1))
    no_sleeping(monkeypatch)

    runner.invoke(app, ["dump-dialogs"])

    result = runner.invoke(app, ["resolve"])

    assert result.exit_code == 0, result.output
    assert "resolved 0" in result.output


def test_resolve_passes_the_evidence_floor_through(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    """`--min-sources` has to reach the pass, not stop at the parser."""
    seen: dict[str, Any] = {}

    async def fake_resolve(client: Any, database: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        from itgraph.tg.resolve import ResolveSummary

        return ResolveSummary()

    use_telegram(monkeypatch, collector_client(dialog_records, posts=1))
    no_sleeping(monkeypatch)
    monkeypatch.setattr("itgraph.tg.resolve.resolve_inventory", fake_resolve)

    result = runner.invoke(app, ["resolve", "--min-sources", "2"])

    assert result.exit_code == 0, result.output
    assert seen["min_sources"] == 2


def test_resolve_refuses_a_negative_evidence_floor(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    dialog_records: list[dict[str, Any]],
) -> None:
    use_telegram(monkeypatch, collector_client(dialog_records, posts=1))
    no_sleeping(monkeypatch)

    result = runner.invoke(app, ["resolve", "--min-sources", "-1"])

    assert result.exit_code != 0


# --- add ---------------------------------------------------------------


def adding_client(**entities: int) -> FakeTelegramClient:
    """A client that resolves the given names to fresh channels."""
    from fakes import tl_channel

    return FakeTelegramClient(
        entities={
            name: tl_channel(tg_id, username=name, title=name.title())
            for name, tg_id in entities.items()
        }
    )


def test_add_records_a_channel_by_username(
    monkeypatch: pytest.MonkeyPatch, inventory: None
) -> None:
    use_telegram(monkeypatch, adding_client(fake_new=3000000001))

    result = runner.invoke(app, ["add", "@fake_new", "--delay", "0"])

    assert result.exit_code == 0, result.output
    assert "added 1" in result.output


def test_add_reaches_the_pass_with_every_option(
    monkeypatch: pytest.MonkeyPatch, inventory: None
) -> None:
    telegram = adding_client(fake_new=3000000001, fake_other=3000000002)
    use_telegram(monkeypatch, telegram)

    result = runner.invoke(
        app,
        [
            "add",
            "fake_new",
            "fake_other",
            "--limit",
            "1",
            "--delay",
            "0",
            "--seed",
            "--kind",
            "media",
        ],
    )

    assert result.exit_code == 0, result.output
    # `--limit 1` reached the pass: only the first name was looked up.
    assert telegram.resolved == ["fake_new"]
    listing = runner.invoke(app, ["channels", "--status", "seed"])
    assert "fake_new" in listing.output


def test_add_refuses_seed_with_a_file(
    monkeypatch: pytest.MonkeyPatch, inventory: None, tmp_path: Any
) -> None:
    """The load-bearing refusal: a list nobody re-read, accepted unseen."""
    telegram = adding_client(fake_new=3000000001)
    use_telegram(monkeypatch, telegram)
    listing = tmp_path / "channels.txt"
    listing.write_text("fake_new\n")

    result = runner.invoke(app, ["add", "--from-file", str(listing), "--seed"])

    assert result.exit_code != 0
    assert "--seed" in result.output
    # Refused before connecting: nothing was looked up.
    assert telegram.resolved == []


def test_add_refuses_neither_usernames_nor_a_file(
    monkeypatch: pytest.MonkeyPatch, inventory: None
) -> None:
    telegram = adding_client()
    use_telegram(monkeypatch, telegram)

    result = runner.invoke(app, ["add"])

    assert result.exit_code != 0
    assert telegram.resolved == []


def test_add_refuses_both_usernames_and_a_file(
    monkeypatch: pytest.MonkeyPatch, inventory: None, tmp_path: Any
) -> None:
    telegram = adding_client(fake_new=3000000001)
    use_telegram(monkeypatch, telegram)
    listing = tmp_path / "channels.txt"
    listing.write_text("fake_other\n")

    result = runner.invoke(
        app, ["add", "fake_new", "--from-file", str(listing)]
    )

    assert result.exit_code != 0
    assert telegram.resolved == []


def test_add_refuses_a_bad_entry_before_spending_anything(
    monkeypatch: pytest.MonkeyPatch, inventory: None, tmp_path: Any
) -> None:
    telegram = adding_client(fake_new=3000000001)
    use_telegram(monkeypatch, telegram)
    listing = tmp_path / "channels.txt"
    listing.write_text("fake_new\nt.me/+AbCdEf\n")

    result = runner.invoke(app, ["add", "--from-file", str(listing)])

    assert result.exit_code != 0
    assert "line 2" in result.output
    assert telegram.resolved == []


def test_add_reads_a_file(
    monkeypatch: pytest.MonkeyPatch, inventory: None, tmp_path: Any
) -> None:
    telegram = adding_client(fake_new=3000000001, fake_other=3000000002)
    use_telegram(monkeypatch, telegram)
    listing = tmp_path / "channels.txt"
    listing.write_text("# a list\nfake_new\n\n@fake_other\n")

    result = runner.invoke(
        app, ["add", "--from-file", str(listing), "--delay", "0"]
    )

    assert result.exit_code == 0, result.output
    assert "added 2" in result.output
    assert telegram.resolved == ["fake_new", "fake_other"]


def test_add_writes_the_failures_as_the_next_runs_input(
    monkeypatch: pytest.MonkeyPatch, inventory: None, tmp_path: Any
) -> None:
    use_telegram(monkeypatch, adding_client(fake_new=3000000001))
    failures = tmp_path / "failed.txt"

    result = runner.invoke(
        app,
        [
            "add",
            "fake_new",
            "fake_missing",
            "--delay",
            "0",
            "--failures-out",
            str(failures),
        ],
    )

    assert result.exit_code == 0, result.output
    written = failures.read_text()
    assert written.startswith("fake_missing  # ")
    # The form `--from-file` reads: the reason is a comment.
    assert written.count("\n") == 1


def test_add_writes_no_failure_file_after_a_clean_run(
    monkeypatch: pytest.MonkeyPatch, inventory: None, tmp_path: Any
) -> None:
    """An empty file would have to be interpreted; absence does not."""
    use_telegram(monkeypatch, adding_client(fake_new=3000000001))
    failures = tmp_path / "failed.txt"

    result = runner.invoke(
        app,
        ["add", "fake_new", "--delay", "0", "--failures-out", str(failures)],
    )

    assert result.exit_code == 0, result.output
    assert not failures.exists()
