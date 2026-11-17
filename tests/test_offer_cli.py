"""`itgraph offer` end to end, against a `_test` database.

Offline by construction: no Telegram client can be had, and the session
lease is held by someone else while it runs. Ids and names are fake and
spelled so.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.edges import EdgeRow, insert_edges
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    EdgeKind,
)
from itgraph.db.session import Database
from itgraph.db.session_lease import SessionLease
from itgraph.tg import client as tg_client

runner = CliRunner()

SEEDS = (1000000001, 1000000002, 1000000003)
RISING = tuple(2000000001 + n for n in range(7))
NOW = datetime.now(UTC).replace(microsecond=0)
TODAY = NOW.astimezone(ZoneInfo("Europe/Moscow")).date()


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))

    @asynccontextmanager
    async def refuse(command: str) -> AsyncIterator[None]:
        raise AssertionError("itgraph offer opened a Telegram client")
        yield  # pragma: no cover - unreachable by design

    monkeypatch.setattr(tg_client, "connected", refuse)


async def _arrange(url: str, *, rising: int = 7) -> None:
    """Three seeds; the first ``rising`` channels referred to by two or
    three of them, the earlier ones by more."""
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
                    *(
                        DiscoveredChannel(
                            tg_id, f"fake_rising_{n}", None, False
                        )
                        for n, tg_id in enumerate(RISING)
                    ),
                ],
                discovered_via=DiscoverySource.FORWARD,
            )
            for seed in SEEDS:
                await mark_channel(
                    session,
                    seed,
                    status=ChannelStatus.SEED,
                    kind=ChannelKind.PERSONAL,
                )
            edges = []
            msg_id = 0
            for n, tg_id in enumerate(RISING[:rising]):
                for seed in SEEDS[: 3 if n < 3 else 2]:
                    msg_id += 1
                    edges.append(
                        EdgeRow(
                            seed,
                            tg_id,
                            EdgeKind.FORWARD,
                            msg_id,
                            NOW - timedelta(days=1),
                        )
                    )
            await insert_edges(session, edges)
            await session.commit()
    finally:
        await database.dispose()


async def _offers(url: str) -> list[tuple[date, int, int]]:
    database = Database(url)
    try:
        async with database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT day, channel_id, rank FROM tg_rising_offers "
                    "ORDER BY day, rank"
                )
            )
            return [(row[0], row[1], row[2]) for row in rows]
    finally:
        await database.dispose()


async def _days(url: str) -> list[date]:
    database = Database(url)
    try:
        async with database.session() as session:
            return list(
                await session.scalars(
                    text("SELECT day FROM tg_rising_offer_days ORDER BY day")
                )
            )
    finally:
        await database.dispose()


def test_the_head_is_offered_and_named_nowhere(database_url: str) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["offer"])

    assert result.exit_code == 0, result.output
    offers = asyncio.run(_offers(database_url))
    assert [rank for _, _, rank in offers] == [1, 2, 3, 4, 5]
    assert {channel for _, channel, _ in offers} <= set(RISING)
    # The three with three families come first.
    assert {channel for _, channel, rank in offers if rank <= 3} == set(
        RISING[:3]
    )
    assert f"{TODAY}: 7 rising with a username, 5 offered, 2 left" in (
        result.output
    )
    assert "fake_" not in result.output


def test_the_overflow_is_offered_the_next_day(database_url: str) -> None:
    asyncio.run(_arrange(database_url))
    runner.invoke(app, ["offer"])

    tomorrow = (NOW + timedelta(days=1)).isoformat()
    result = runner.invoke(app, ["offer", "--now", tomorrow])

    assert result.exit_code == 0, result.output
    offers = asyncio.run(_offers(database_url))
    assert len(offers) == 7
    assert len({channel for _, channel, _ in offers}) == 7


def test_a_day_is_selected_once(database_url: str) -> None:
    asyncio.run(_arrange(database_url))
    runner.invoke(app, ["offer"])

    result = runner.invoke(app, ["offer"])

    assert result.exit_code == 0, result.output
    assert f"{TODAY} has its selection; nothing written" in result.output
    assert len(asyncio.run(_offers(database_url))) == 5


def test_an_empty_day_is_recorded(database_url: str) -> None:
    asyncio.run(_arrange(database_url, rising=0))

    result = runner.invoke(app, ["offer"])

    assert result.exit_code == 0, result.output
    assert asyncio.run(_days(database_url)) == [TODAY]
    assert asyncio.run(_offers(database_url)) == []


def test_a_preview_names_the_offers_and_writes_nothing(
    database_url: str,
) -> None:
    asyncio.run(_arrange(database_url))

    result = runner.invoke(app, ["offer", "--preview"])

    assert result.exit_code == 0, result.output
    assert "(preview, nothing written)" in result.output
    assert result.output.count("@fake_rising_") == 5
    assert asyncio.run(_days(database_url)) == []


def test_it_runs_while_the_loop_holds_the_lease(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_file = tmp_path / "fake.session"
    monkeypatch.setattr(settings, "telegram_session", session_file)
    asyncio.run(_arrange(database_url))
    loop = asyncio.new_event_loop()
    lease = SessionLease("watch", url=database_url, session_path=session_file)
    loop.run_until_complete(lease.acquire())
    try:
        result = runner.invoke(app, ["offer"])
    finally:
        loop.run_until_complete(lease.release())
        loop.close()

    assert result.exit_code == 0, result.output
    assert len(asyncio.run(_offers(database_url))) == 5
