from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakeTelegramClient
from typer.testing import CliRunner

from itgraph import __version__
from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.tg import auth as tg_auth
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
