"""`itgraph rising` end to end, against a `_test` database.

Offline by construction: no Telegram client can be had, and the session
lease is held by someone else while it runs. Ids and names are fake and
spelled so.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.edges import (
    EdgeRow,
    MentionSource,
    add_pending_mentions,
    create_discovered_channels,
    insert_edges,
)
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    EdgeKind,
    ResolutionKind,
    ResolutionOutcome,
)
from itgraph.db.rising import record_resolution
from itgraph.db.session import Database
from itgraph.db.session_lease import SessionLease
from itgraph.tg import client as tg_client

runner = CliRunner()

SEEDS = (1000000001, 1000000002, 1000000003)
RISING = 2000000001
FORWARDED = 2000000002  # known by id alone, never resolved
FIRST = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test database, and no Telegram client to be had."""
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))

    @asynccontextmanager
    async def refuse(command: str) -> AsyncIterator[None]:
        raise AssertionError("itgraph rising opened a Telegram client")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(tg_client, "connected", refuse)


def edge(src: int, dst: int, msg_id: int, at: datetime) -> EdgeRow:
    return EdgeRow(
        src_channel_id=src,
        dst_channel_id=dst,
        kind=EdgeKind.FORWARD,
        msg_id=msg_id,
        published_at=at,
    )


async def _arrange(url: str) -> None:
    database = Database(url)
    try:
        async with database.session() as session:
            await upsert_channels(
                session,
                [
                    *(
                        DiscoveredChannel(seed, f"fake_seed_{n}", None, False)
                        for n, seed in enumerate(SEEDS)
                    ),
                    DiscoveredChannel(
                        RISING, "fake_rising", "Fake Rising", False
                    ),
                ],
                discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
            )
            for seed in SEEDS:
                await mark_channel(
                    session,
                    seed,
                    status=ChannelStatus.SEED,
                    kind=ChannelKind.PERSONAL,
                )
            await create_discovered_channels(
                session,
                tg_ids=[FORWARDED],
                discovered_via=DiscoverySource.FORWARD,
            )
            await insert_edges(
                session,
                [
                    edge(SEEDS[0], RISING, 1, FIRST),
                    edge(SEEDS[1], RISING, 2, FIRST + timedelta(days=1)),
                    edge(SEEDS[2], RISING, 3, FIRST - timedelta(days=60)),
                    edge(SEEDS[0], FORWARDED, 4, FIRST),
                    edge(SEEDS[1], FORWARDED, 5, FIRST),
                ],
            )
            await add_pending_mentions(
                session,
                [
                    MentionSource(SEEDS[0], "fake_secret_name", FIRST),
                    MentionSource(SEEDS[2], "fake_secret_name", FIRST),
                ],
            )
            for command in (
                CollectionCommand.RESOLVE,
                CollectionCommand.RESOLVE,
                CollectionCommand.RESOLVE,
                CollectionCommand.WATCH,
                CollectionCommand.WATCH,
            ):
                await record_resolution(
                    session,
                    command,
                    ResolutionKind.ID,
                    ResolutionOutcome.CHANNEL,
                )
            await session.commit()
    finally:
        await database.dispose()


def test_a_ranked_channel_is_printed_with_its_evidence(
    database_url: str,
) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["rising"])

    assert result.exit_code == 0, result.output
    assert (
        f"  1. @fake_rising  Fake Rising — 2 families in the window, "
        f"1 before; first in the window {FIRST:%Y-%m-%d %H:%M} UTC"
    ) in result.output


def test_what_awaits_is_counted_and_never_named(database_url: str) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["rising"])

    assert result.exit_code == 0, result.output
    assert "awaiting a resolution: 2" in result.output
    assert "secret" not in result.output
    assert str(FORWARDED) not in result.output


def test_the_cap_is_reported_by_command(database_url: str) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["rising"])

    assert (
        "resolutions in the last 24 hours: 5 of 10 — resolve 3, watch 2"
        in result.output
    )


def test_an_empty_database_says_so(database_url: str) -> None:
    result = runner.invoke(app, ["rising"])

    assert result.exit_code == 0, result.output
    assert "no channel with a username is rising" in result.output
    assert "awaiting a resolution: 0" in result.output
    assert (
        "left out: 0 chat, 0 no username, 0 not a channel, 0 failed"
        in result.output
    )
    assert "resolutions in the last 24 hours: 0 of 10\n" in result.output


def test_it_runs_while_the_loop_holds_the_lease(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Held for real, on a loop of its own and on the session file the
    command would claim: a command that wanted the lease would fail here,
    and one that wanted a client would raise."""
    session_file = tmp_path / "fake.session"
    monkeypatch.setattr(settings, "telegram_session", session_file)
    asyncio.run(_arrange(database_url))
    loop = asyncio.new_event_loop()
    lease = SessionLease("watch", url=database_url, session_path=session_file)
    loop.run_until_complete(lease.acquire())
    try:
        result = runner.invoke(app, ["rising"])
    finally:
        loop.run_until_complete(lease.release())
        loop.close()

    assert result.exit_code == 0, result.output
    assert "@fake_rising" in result.output
