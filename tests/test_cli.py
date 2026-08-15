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
from itgraph.tg.errors import WatchStalled

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
    async def connected(command: str) -> AsyncIterator[FakeTelegramClient]:
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
    async def refuse(command: str) -> AsyncIterator[None]:
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


AWAITING = 2000000001  # discovered by forward, never resolved


def seed_resolution_queue(url: str, *, attempts: int = 0) -> None:
    """One channel awaiting resolution, and one already resolved.

    Written directly: a channel gets into this queue by being forwarded
    from, and collecting a forward to build the row would test the
    collector.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def build() -> None:
        engine = create_async_engine(url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                await session.execute(
                    text(
                        "INSERT INTO channels "
                        "(tg_id, username, title, discovered_via, status, "
                        "resolved_at, resolve_attempts, resolve_last_error) "
                        "VALUES "
                        "(:queued, NULL, NULL, 'forward', 'candidate', "
                        "NULL, :attempts, :error), "
                        "(:done, 'fake_known', 'Known', 'manual', 'seed', "
                        "now(), 0, NULL)"
                    ),
                    {
                        "queued": AWAITING,
                        "done": KNOWN,
                        "attempts": attempts,
                        "error": "no access hash" if attempts else None,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(build())


def refusing_telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace `connected` with one that records and refuses to yield.

    A refusal has to happen before the session lease is taken, so these
    tests assert on what never ran rather than on what did.
    """
    entered: list[str] = []

    @asynccontextmanager
    async def connected(command: str) -> AsyncIterator[FakeTelegramClient]:
        entered.append(command)
        raise AssertionError("the session was claimed by a refused run")
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(tg_client, "connected", connected)
    return entered


def test_resolve_passes_the_named_channel_through(
    monkeypatch: pytest.MonkeyPatch,
    inventory: None,
    database_url: str,
    dialog_records: list[dict[str, Any]],
) -> None:
    """A TG_ID has to reach the pass, not stop at the parser."""
    seed_resolution_queue(database_url)
    seen: dict[str, Any] = {}

    async def fake_resolve(client: Any, database: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        from itgraph.tg.resolve import ResolveSummary

        return ResolveSummary()

    use_telegram(monkeypatch, collector_client(dialog_records, posts=1))
    no_sleeping(monkeypatch)
    monkeypatch.setattr("itgraph.tg.resolve.resolve_inventory", fake_resolve)

    result = runner.invoke(app, ["resolve", str(AWAITING)])

    assert result.exit_code == 0, result.output
    assert seen["tg_id"] == AWAITING


def test_resolve_refuses_a_limit_beside_a_named_channel(
    monkeypatch: pytest.MonkeyPatch, inventory: None, database_url: str
) -> None:
    seed_resolution_queue(database_url)
    entered = refusing_telegram(monkeypatch)

    result = runner.invoke(app, ["resolve", str(AWAITING), "--limit", "5"])

    assert result.exit_code != 0
    assert entered == []


def test_resolve_refuses_an_evidence_floor_beside_a_named_channel(
    monkeypatch: pytest.MonkeyPatch, inventory: None, database_url: str
) -> None:
    seed_resolution_queue(database_url)
    entered = refusing_telegram(monkeypatch)

    result = runner.invoke(
        app, ["resolve", str(AWAITING), "--min-sources", "2"]
    )

    assert result.exit_code != 0
    assert entered == []


def test_resolve_refuses_an_unknown_id_without_connecting(
    monkeypatch: pytest.MonkeyPatch, inventory: None, database_url: str
) -> None:
    seed_resolution_queue(database_url)
    entered = refusing_telegram(monkeypatch)

    result = runner.invoke(app, ["resolve", "999999999"])

    assert result.exit_code == 1
    assert "no channel 999999999 in the inventory" in result.output
    assert entered == []


def test_resolve_refuses_an_already_resolved_channel(
    monkeypatch: pytest.MonkeyPatch, inventory: None, database_url: str
) -> None:
    seed_resolution_queue(database_url)
    entered = refusing_telegram(monkeypatch)

    result = runner.invoke(app, ["resolve", str(KNOWN)])

    assert result.exit_code == 1
    assert "already resolved" in result.output
    assert entered == []


def test_resolve_refuses_a_past_failure_and_names_the_flag(
    monkeypatch: pytest.MonkeyPatch, inventory: None, database_url: str
) -> None:
    seed_resolution_queue(database_url, attempts=1)
    entered = refusing_telegram(monkeypatch)

    result = runner.invoke(app, ["resolve", str(AWAITING)])

    assert result.exit_code == 1
    assert "--retry-failed" in result.output
    assert entered == []


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


# --- affiliation ------------------------------------------------------


def seed_affiliation_fixture(url: str) -> None:
    """Two channels named alike, with edges and a description between.

    Written directly rather than collected: the signals read the
    inventory, the derived edges and stored descriptions, and building
    those through the collector would test the collector.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def build() -> None:
        # A plain engine, not `Database`: the tests monkeypatch that name
        # to a zero-argument factory, and a fixture builder must work
        # whichever side of the patch it is called from.
        engine = create_async_engine(url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                await session.execute(
                    text(
                        "INSERT INTO channels "
                        "(tg_id, username, title, discovered_via, status) "
                        "VALUES "
                        "(:a, 'fake_gonzo_main', 'Main', 'manual', 'seed'), "
                        "(:b, 'fake_gonzo_pod', 'Pod', 'manual', 'seed')"
                    ),
                    {"a": KNOWN, "b": KNOWN + 1},
                )
                await session.execute(
                    text(
                        "INSERT INTO raw_channels (channel_id, payload) "
                        "VALUES (:a, :payload)"
                    ),
                    {
                        "a": KNOWN,
                        "payload": (
                            '{"full_chat": {"about": '
                            '"\\u041f\\u043e\\u0434\\u043a\\u0430\\u0441\\u0442'
                            ' @fake_gonzo_pod"}}'
                        ),
                    },
                )
                for index in range(25):
                    await session.execute(
                        text(
                            "INSERT INTO edges (src_channel_id, "
                            "dst_channel_id, kind, msg_id, published_at) "
                            "VALUES (:a, :b, 'forward', :msg, now())"
                        ),
                        {"a": KNOWN, "b": KNOWN + 1, "msg": index},
                    )
        finally:
            await engine.dispose()

    asyncio.run(build())


def seed_handle_group_fixture(url: str) -> None:
    """Three channels sharing a handle, one of them signing it.

    The shape the named-handle signal was written for: a hub whose
    description reads "@gonzo" and satellites whose usernames carry the
    same token. No edges and no cross-references, so nothing but that
    signal can propose these pairs.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def build() -> None:
        engine = create_async_engine(url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                await session.execute(
                    text(
                        "INSERT INTO channels "
                        "(tg_id, username, title, discovered_via, status) "
                        "VALUES "
                        "(:a, 'tg_gonzo', 'Hub', 'manual', 'seed'), "
                        "(:b, 'logs_gonzo', 'Logs', 'manual', 'seed'), "
                        "(:c, 'files_gonzo', 'Files', 'manual', 'seed')"
                    ),
                    {"a": KNOWN, "b": KNOWN + 1, "c": KNOWN + 2},
                )
                await session.execute(
                    text(
                        "INSERT INTO raw_channels (channel_id, payload) "
                        "VALUES (:a, :payload)"
                    ),
                    {
                        "a": KNOWN,
                        "payload": '{"full_chat": {"about": "@gonzo"}}',
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(build())


def test_affiliates_ranks_pairs_and_shows_its_evidence(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates"])

    assert result.exit_code == 0, result.output
    assert "candidate pairs proposed" in result.output
    assert "@fake_gonzo_main" in result.output
    # The evidence is on screen, not folded into the score: the operator
    # is being asked which signal said so.
    assert "token:gonzo" in result.output
    assert "about:a_to_b" in result.output
    assert "share:a=1.00 of 25" in result.output


def test_affiliates_reports_description_coverage(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """A short list over 40% coverage is not a small problem."""
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates"])

    assert "descriptions: 1 of 2 channels have one, 1 do not" in result.output


def test_affiliates_writes_no_family_link(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The one thing detection may never do, whatever the scores."""
    import asyncio

    from sqlalchemy import text

    from itgraph.db.session import Database

    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates"])
    assert result.exit_code == 0, result.output

    async def read() -> list[Any]:
        database = Database(database_url)
        try:
            async with database.session() as session:
                rows = await session.execute(
                    text("SELECT family_key FROM channel_families")
                )
                return list(rows.scalars().all())
        finally:
            await database.dispose()

    assert asyncio.run(read()) == []


def test_affiliates_needs_no_telegram_session(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Every input was collected by an earlier pass, so this makes no
    request and must not even ask for a session."""

    def refuse() -> None:
        raise AssertionError("affiliates must not connect to Telegram")

    monkeypatch.setattr(tg_client, "connected", refuse)
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates"])

    assert result.exit_code == 0, result.output


def test_affiliates_thresholds_reach_the_signals(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(
        app,
        [
            "affiliates",
            "--min-out-edges",
            "500",
            "--max-token-channels",
            "2",
            "--min-token-length",
            "40",
        ],
    )

    assert result.exit_code == 0, result.output
    # The token and share signals are now out of reach; only the
    # description reference is left.
    assert "token:" not in result.output
    assert "share:" not in result.output
    assert "about:a_to_b" in result.output


def test_affiliates_limit_bounds_the_output(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert result.output.count("@fake_gonzo_main") == 1


def test_affiliates_refuses_a_share_outside_its_range(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates", "--max-share", "1.5"])

    assert result.exit_code != 0
    assert "max_share_min" in result.output


def test_affiliates_refuses_a_token_cap_below_two(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["affiliates", "--max-token-channels", "1"])

    assert result.exit_code != 0
    assert "max_token_channels" in result.output


def test_family_confirms_and_the_listing_shows_it(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    confirmed = runner.invoke(
        app, ["family", "@fake_gonzo_main", "@fake_gonzo_pod"]
    )
    listing = runner.invoke(app, ["channels", "--family", "@fake_gonzo_pod"])
    summary = runner.invoke(app, ["channels"])

    assert confirmed.exit_code == 0, confirmed.output
    assert "1 pairs recorded; family of 2 channels" in confirmed.output
    # Asked by either member, the answer is the whole family, and no
    # member is marked out as the main one.
    assert "@fake_gonzo_main" in listing.output
    assert "canonical" not in listing.output
    assert "families   1 (2 channels)" in summary.output


def test_family_rejects_and_the_pair_leaves_the_review_list(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    runner.invoke(app, ["affiliates"])
    rejected = runner.invoke(
        app,
        [
            "family",
            "@fake_gonzo_main",
            "@fake_gonzo_pod",
            "--reject",
            "--note",
            "different people",
        ],
    )
    pending = runner.invoke(app, ["affiliates"])
    everything = runner.invoke(app, ["affiliates", "--all"])

    assert rejected.exit_code == 0, rejected.output
    assert "not affiliated" in rejected.output
    assert "@fake_gonzo_main" not in pending.output
    # Kept, not deleted: the rejection is what stops it being proposed.
    assert "@fake_gonzo_main" in everything.output


def test_family_withdraws_a_confirmation(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    runner.invoke(app, ["family", str(KNOWN), str(KNOWN + 1)])
    result = runner.invoke(
        app, ["family", str(KNOWN), str(KNOWN + 1), "--withdraw"]
    )
    summary = runner.invoke(app, ["channels"])

    assert result.exit_code == 0, result.output
    assert "decision withdrawn" in result.output
    assert "families   0" in summary.output


def test_affiliates_stops_proposing_a_confirmed_pair(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    runner.invoke(app, ["affiliates"])
    runner.invoke(app, ["family", str(KNOWN), str(KNOWN + 1)])
    result = runner.invoke(app, ["affiliates"])

    assert result.exit_code == 0, result.output
    assert "0 candidate pairs proposed" in result.output


def test_affiliates_hides_a_pair_with_no_seed_in_it(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    async def unreview() -> None:
        engine = create_async_engine(database_url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                await session.execute(
                    text("UPDATE channels SET status = 'candidate'")
                )
        finally:
            await engine.dispose()

    asyncio.run(unreview())

    hidden = runner.invoke(app, ["affiliates"])
    shown = runner.invoke(app, ["affiliates", "--any-status"])

    assert hidden.exit_code == 0, hidden.output
    assert "@fake_gonzo_main" not in hidden.output
    # Computed and stored either way — only the reading is narrowed.
    assert "1 candidate pairs proposed" in hidden.output
    assert "0 awaiting review" in hidden.output
    assert "@fake_gonzo_main" in shown.output


def test_affiliates_shows_a_pair_with_one_seed_in_it(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    async def unreview_one() -> None:
        engine = create_async_engine(database_url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                await session.execute(
                    text(
                        "UPDATE channels SET status = 'candidate' "
                        "WHERE tg_id = :b"
                    ),
                    {"b": KNOWN + 1},
                )
        finally:
            await engine.dispose()

    asyncio.run(unreview_one())

    result = runner.invoke(app, ["affiliates"])

    assert result.exit_code == 0, result.output
    assert "@fake_gonzo_main" in result.output


def seed_vacancies_group(url: str) -> list[int]:
    """Five channels one author runs, and no channel is the hub.

    The shape that motivated dropping the canonical channel: detection
    finds pairs among them that form no star, so under the old model the
    second confirmation was refused and the group could not be assembled.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = [KNOWN + 10 + offset for offset in range(5)]

    async def build() -> None:
        engine = create_async_engine(url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                for index, tg_id in enumerate(ids):
                    await session.execute(
                        text(
                            "INSERT INTO channels (tg_id, username, title, "
                            "discovered_via, status) VALUES "
                            "(:id, :name, :title, 'manual', 'seed')"
                        ),
                        {
                            "id": tg_id,
                            "name": f"fake_jobs_{index}",
                            "title": f"Jobs {index}",
                        },
                    )
        finally:
            await engine.dispose()

    asyncio.run(build())
    return ids


def test_family_assembles_a_group_from_pairs_that_form_no_star(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Confirmed in the order detection proposed them, one pair at a
    time. Every one of these was refused by the canonical model."""
    use_test_database(monkeypatch, database_url)
    a, b, c, d, e = seed_vacancies_group(database_url)

    # a-b, a-c, d-b, e-b: no channel is in every pair, and d-b bridges
    # what would otherwise be two separate groups.
    for first, second in ((a, b), (a, c), (d, b), (e, b)):
        result = runner.invoke(app, ["family", str(first), str(second)])
        assert result.exit_code == 0, result.output

    listing = runner.invoke(app, ["channels", "--family", str(e)])

    for tg_id in (a, b, c, d, e):
        assert str(tg_id) in listing.output


def test_family_confirms_a_whole_group_in_one_command(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    ids = seed_vacancies_group(database_url)

    result = runner.invoke(app, ["family", *(str(tg_id) for tg_id in ids)])
    listing = runner.invoke(app, ["channels", "--family", str(ids[2])])

    assert result.exit_code == 0, result.output
    # Five channels are ten pairs, not a chain of four.
    assert "10 pairs recorded; family of 5 channels" in result.output
    for tg_id in ids:
        assert str(tg_id) in listing.output


def test_family_reports_a_merge_rather_than_performing_it_silently(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    a, b, c, d, _ = seed_vacancies_group(database_url)

    runner.invoke(app, ["family", str(a), str(b)])
    runner.invoke(app, ["family", str(c), str(d)])
    bridged = runner.invoke(app, ["family", str(b), str(c)])

    assert bridged.exit_code == 0, bridged.output
    # One pair written, four channels in the family it produced.
    assert "1 pairs recorded; family of 4 channels" in bridged.output


def test_family_no_longer_accepts_canonical(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    a, b, *_ = seed_vacancies_group(database_url)

    result = runner.invoke(
        app, ["family", str(a), str(b), "--canonical", str(a)]
    )

    assert result.exit_code != 0
    assert "No such option" in result.output or "--canonical" in result.output


def test_family_needs_at_least_two_channels(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    a, *_ = seed_vacancies_group(database_url)

    result = runner.invoke(app, ["family", str(a)])

    assert result.exit_code != 0
    assert "at least two" in result.output


def test_family_reject_takes_exactly_two_channels(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """A rejection is a statement about a pair; there is no reading of
    rejecting a group that says anything definite."""
    use_test_database(monkeypatch, database_url)
    a, b, c, *_ = seed_vacancies_group(database_url)

    result = runner.invoke(app, ["family", str(a), str(b), str(c), "--reject"])

    assert result.exit_code != 0
    assert "exactly two" in result.output


def test_family_withdrawal_splits_only_what_it_held_together(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    a, b, c, *_ = seed_vacancies_group(database_url)

    runner.invoke(app, ["family", str(a), str(b)])
    runner.invoke(app, ["family", str(b), str(c)])
    runner.invoke(app, ["family", str(a), str(b), "--withdraw"])

    listing = runner.invoke(app, ["channels", "--family", str(b)])

    assert str(b) in listing.output
    assert str(c) in listing.output
    assert str(a) not in listing.output


def seed_seed_channel(url: str, tg_id: int, username: str) -> None:
    """One accepted channel, written directly.

    Not collected through the CLI: what these tests are about is the loop
    and the lease, and going through `dump-dialogs` plus `mark` would put
    two other commands between the test and its subject.
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def build() -> None:
        engine = create_async_engine(url)
        try:
            async with (
                async_sessionmaker(engine)() as session,
                session.begin(),
            ):
                await session.execute(
                    text(
                        "INSERT INTO channels "
                        "(tg_id, username, title, discovered_via, status) "
                        "VALUES (:id, :name, 'Example', 'manual', 'seed')"
                    ),
                    {"id": tg_id, "name": username},
                )
        finally:
            await engine.dispose()

    asyncio.run(build())


def test_watch_polls_and_reports(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The loop reachable through the CLI, bounded by `--cycles`.

    Without the bound this command never returns, which is the point of
    the command and a problem for a test.
    """
    from datetime import UTC, datetime

    from fakes import FakeChannel, FakeHistoryMessage

    from itgraph.config import settings as live_settings

    # Equal bounds switch the window off. Without this the test polls
    # nothing between 02:00 and 07:00 Moscow time and fails for five
    # hours every night — `tests/test_watch.py` has carried the same
    # fixture from the start, and this one was simply missed.
    monkeypatch.setattr(live_settings, "watch_quiet_from_hour", 0)
    monkeypatch.setattr(live_settings, "watch_quiet_to_hour", 0)

    use_test_database(monkeypatch, database_url)
    seed_seed_channel(database_url, 1000000001, "example")

    telegram = FakeTelegramClient(
        entities={"example": FakeChannel(1000000001, "example")},
        histories={
            1000000001: [
                FakeHistoryMessage(
                    10, datetime.now(UTC), "post", views=100, forwards=1
                )
            ]
        },
    )

    @asynccontextmanager
    async def connected_with_lease(
        command: str,
    ) -> AsyncIterator[tuple[FakeTelegramClient, None]]:
        assert command == "watch"
        yield telegram, None

    monkeypatch.setattr(
        tg_client, "connected_with_lease", connected_with_lease
    )

    result = runner.invoke(app, ["watch", "--cycles", "1"])

    assert result.exit_code == 0, result.output
    assert "1 new messages" in result.output
    assert "1 snapshots" in result.output


def test_watch_refuses_while_the_session_is_held(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The whole reason the lease exists, at the seam an operator meets it."""
    from itgraph.db import session_lease as lease_module

    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def busy(command: str, **kwargs: Any) -> AsyncIterator[None]:
        raise lease_module.SessionBusyError(
            "the Telegram session itgraph.session is in use by itgraph watch"
        )
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(lease_module, "session_lease", busy)

    result = runner.invoke(app, ["watch", "--cycles", "1"])

    assert result.exit_code == 1
    assert "in use by" in result.output


def test_watch_reports_a_stall_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """A wedged loop must leave a sentence, not a traceback.

    It exits non-zero so a supervisor restarts it, and the operator who
    reads the journal afterwards should find out why in one line —
    which is the difference between this and the two days of silence it
    replaces.
    """
    from itgraph.tg import watch as watch_module

    use_test_database(monkeypatch, database_url)

    async def stalled(*args: Any, **kwargs: Any) -> Any:
        raise WatchStalled("no poll has concluded in 30 minutes")

    @asynccontextmanager
    async def connected_with_lease(
        command: str,
    ) -> AsyncIterator[tuple[object, None]]:
        yield object(), None

    monkeypatch.setattr(watch_module, "watch", stalled)
    monkeypatch.setattr(
        tg_client, "connected_with_lease", connected_with_lease
    )

    result = runner.invoke(app, ["watch"])

    assert result.exit_code == 1
    assert "no poll has concluded" in result.output
    assert "Traceback" not in result.output


def test_backfill_refuses_while_the_loop_holds_the_session(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from itgraph.db import session_lease as lease_module

    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def busy(command: str, **kwargs: Any) -> AsyncIterator[None]:
        raise lease_module.SessionBusyError("in use by itgraph watch pid=1")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(lease_module, "session_lease", busy)

    result = runner.invoke(app, ["backfill", "--since", "2026-05-01"])

    assert result.exit_code == 1
    assert "in use by itgraph watch" in result.output


def test_watch_status_needs_no_lease(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """A status command that could not run alongside the loop would be
    reporting on the one state nobody can observe."""
    from itgraph.db import session_lease as lease_module

    use_test_database(monkeypatch, database_url)
    seed_seed_channel(database_url, 1000000001, "example")

    @asynccontextmanager
    async def refuse(command: str, **kwargs: Any) -> AsyncIterator[None]:
        raise AssertionError("watch-status must not take the session lease")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(lease_module, "session_lease", refuse)

    result = runner.invoke(app, ["watch-status"])

    assert result.exit_code == 0, result.output
    assert "1 due now" in result.output
    assert "snapshots:" in result.output


def test_watch_status_says_when_it_is_paused(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Paused and stuck look identical without this line.

    "2 due now, oldest overdue by 5h" is exactly what the quiet window
    produces, so a status command that does not name it reports the
    healthy state and the broken one in the same words.
    """
    from itgraph.config import settings as live_settings

    use_test_database(monkeypatch, database_url)
    seed_seed_channel(database_url, 1000000001, "example")
    monkeypatch.setattr(live_settings, "watch_quiet_from_hour", 0)
    monkeypatch.setattr(live_settings, "watch_quiet_to_hour", 23)

    result = runner.invoke(app, ["watch-status"])

    assert result.exit_code == 0, result.output
    assert "quiet hours until" in result.output
    assert "not polling" in result.output


def test_watch_status_says_nothing_about_quiet_hours_outside_them(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The line has to be absent when it does not apply, or it stops
    carrying information."""
    from itgraph.config import settings as live_settings

    use_test_database(monkeypatch, database_url)
    seed_seed_channel(database_url, 1000000001, "example")
    monkeypatch.setattr(live_settings, "watch_quiet_from_hour", 0)
    monkeypatch.setattr(live_settings, "watch_quiet_to_hour", 0)

    result = runner.invoke(app, ["watch-status"])

    assert result.exit_code == 0, result.output
    assert "quiet hours" not in result.output


def test_alerts_reports_what_it_raised(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The pass takes no session lease, so it runs beside a collector."""
    from itgraph.db import session_lease as lease_module

    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def refuse(command: str, **kwargs: Any) -> AsyncIterator[None]:
        raise AssertionError("the alert pass must take no session lease")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(lease_module, "session_lease", refuse)

    result = runner.invoke(app, ["alerts"])

    assert result.exit_code == 0, result.output
    assert "new alert(s)" in result.output


def test_alerts_reports_stale_evidence(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Silence is this system's healthy state, so staleness must be visible.

    Without this line, "nothing travelled" and "`derive` has not run
    since Tuesday" are the same observation. What must *not* happen is
    the reverse: a quiet stretch reported as a broken pipeline.
    """
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["alerts"])

    assert result.exit_code == 0, result.output
    assert "nothing reposted in the window" in result.output


def test_baselines_and_score_need_no_lease(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Both run beside a collector, so neither may claim the session.

    The refresh is the one worth pinning down: it reads every raw message
    in the inventory, which looks enough like collection that taking the
    lease "to be safe" would be an easy thing to add later — and it would
    stop the collector for the duration.
    """
    from itgraph.db import session_lease as lease_module

    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def refuse(command: str, **kwargs: Any) -> AsyncIterator[None]:
        raise AssertionError("scoring must take no session lease")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(lease_module, "session_lease", refuse)

    baselines = runner.invoke(app, ["baselines"])
    assert baselines.exit_code == 0, baselines.output
    assert "have a baseline" in baselines.output

    scored = runner.invoke(app, ["score"])
    assert scored.exit_code == 0, scored.output


def test_score_says_so_when_there_are_no_baselines(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Rather than scoring against defaults nobody chose.

    An empty database is the state every new deployment starts in, and
    the message has to name the command that fixes it — silence here
    would look exactly like a quiet day.
    """
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["score"])

    assert result.exit_code == 0, result.output
    assert "itgraph baselines" in result.output


def test_since_is_refused_without_replay(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """A live pass with a week-wide window would alert on settled posts."""
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["score", "--since", "7"])

    assert result.exit_code != 0
    assert "--replay" in result.output


def test_neither_scoring_command_connects_to_telegram(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def refuse(command: str) -> AsyncIterator[None]:
        raise AssertionError("scoring must not connect over MTProto")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(tg_client, "connected", refuse)
    monkeypatch.setattr(tg_client, "connected_with_lease", refuse)

    assert runner.invoke(app, ["baselines"]).exit_code == 0
    assert runner.invoke(app, ["score", "--replay"]).exit_code == 0


def test_bot_refuses_without_a_token(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from itgraph.config import settings as live_settings

    use_test_database(monkeypatch, database_url)
    monkeypatch.setattr(live_settings, "telegram_bot_token", None)
    monkeypatch.setattr(live_settings, "alert_chat_id", None)

    result = runner.invoke(app, ["bot"])

    assert result.exit_code == 1
    assert "TELEGRAM_BOT_TOKEN" in result.output


def test_neither_alert_command_connects_to_telegram(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)

    @asynccontextmanager
    async def refuse(command: str) -> AsyncIterator[None]:
        raise AssertionError("alerting must not connect over MTProto")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(tg_client, "connected", refuse)
    monkeypatch.setattr(tg_client, "connected_with_lease", refuse)

    assert runner.invoke(app, ["alerts"]).exit_code == 0


# Modules an offline command reaches for. Naming them individually
# rather than importing the whole CLI is what makes a failure say which
# path went wrong.
OFFLINE_MODULES = [
    "itgraph.tg.errors",
    "itgraph.db.channels",
    "itgraph.db.floods",
    "itgraph.db.alerts",
    "itgraph.db.session_lease",
    "itgraph.derive.edges",
    "itgraph.affiliation.run",
    "itgraph.alerts.run",
    "itgraph.bot.app",
]


def test_offline_commands_do_not_import_telethon() -> None:
    """A pass that goes nowhere near Telegram must not load Telethon.

    Not a style point. Telethon logs a line about encryption libraries
    the moment it is imported, so `itgraph derive` printed evidence of
    touching Telegram while doing nothing but read Postgres — and this
    project's clearest promise is that some passes do not. A log line
    contradicting it makes the promise unverifiable by the cheapest
    method available, which is reading the output.

    Run in a subprocess because the assertion is about module state, and
    by the time this test runs the suite has imported Telethon many
    times over.
    """
    import subprocess
    import sys

    program = (
        "import sys\n"
        + "".join(f"import {name}\n" for name in OFFLINE_MODULES)
        + "assert 'telethon' not in sys.modules, "
        "sorted(m for m in sys.modules if m.startswith('telethon'))[:3]\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_the_networked_path_still_carries_the_exception() -> None:
    """Moving it must not break the name every call site already uses."""
    from itgraph.tg.client import NotAuthorizedError as from_client
    from itgraph.tg.errors import NotAuthorizedError as from_errors

    assert from_client is from_errors


@pytest.fixture
def restore_aiogram_level() -> Iterator[None]:
    """Logger levels are global; put this one back after fiddling."""
    import logging

    logger = logging.getLogger("aiogram.event")
    before = logger.level
    try:
        yield
    finally:
        logger.setLevel(before)


def test_unhandled_update_noise_is_quiet_by_default(
    restore_aiogram_level: None,
) -> None:
    """Nearly every update this bot sees is unhandled by design.

    Two handlers, a group full of ordinary chatter, and strangers who
    found the username: at roughly one alert a day that line would be
    the entire log.
    """
    import logging

    logging.getLogger("aiogram.event").setLevel(logging.NOTSET)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert logging.getLogger("aiogram.event").level == logging.WARNING


def test_verbose_brings_the_unhandled_update_log_back(
    restore_aiogram_level: None,
) -> None:
    """It is the right diagnostic when a handler stops matching.

    Silencing it unconditionally would remove the one visible symptom of
    a wrong chat id — which is precisely what somebody running with
    `--verbose` is trying to find.
    """
    import logging

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    result = runner.invoke(app, ["--verbose", "version"])

    assert result.exit_code == 0
    assert logging.getLogger("aiogram.event").level != logging.WARNING


def test_affiliates_prints_a_handle_group_as_one_block(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """Three channels are three pairs and one decision, so they are
    printed together with the command that would confirm them."""
    use_test_database(monkeypatch, database_url)
    seed_handle_group_fixture(database_url)

    result = runner.invoke(app, ["affiliates"])

    assert result.exit_code == 0, result.output
    assert "@gonzo — 3 channels, 3 pairs" in result.output
    assert "handle:gonzo/3" in result.output
    assert "itgraph family tg_gonzo logs_gonzo files_gonzo" in result.output
    # One block, not one heading per pair.
    assert result.output.count("@gonzo — ") == 1


def test_affiliates_reports_the_pairs_a_limit_hides_from_a_group(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The bound still counts pairs, so part of a family must not read as
    the whole of one."""
    use_test_database(monkeypatch, database_url)
    seed_handle_group_fixture(database_url)

    result = runner.invoke(app, ["affiliates", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "@gonzo — 3 channels, 3 pairs, 1 not shown" in result.output
    assert result.output.count("handle:gonzo/3") == 2


def test_affiliates_handle_cap_reaches_the_signal(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_handle_group_fixture(database_url)

    result = runner.invoke(
        app, ["affiliates", "--max-handle-token-channels", "2"]
    )

    assert result.exit_code == 0, result.output
    assert "handle:" not in result.output
    # The rarity signal reads the token instead, now that no signed
    # handle is claiming it.
    assert "token:gonzo/3" in result.output


def test_affiliates_handle_weight_reaches_the_score(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_handle_group_fixture(database_url)

    default = runner.invoke(app, ["affiliates"])
    heavier = runner.invoke(app, ["affiliates", "--weight-handle", "2.0"])

    assert " 1.000  " in default.output
    assert " 2.000  " in heavier.output


def test_affiliates_refuses_a_handle_cap_below_two(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_handle_group_fixture(database_url)

    result = runner.invoke(
        app, ["affiliates", "--max-handle-token-channels", "1"]
    )

    assert result.exit_code != 0
    assert "max_handle_token_channels" in result.output
