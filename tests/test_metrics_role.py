"""The exporter's role, on a database the migrations built.

A database of its own, because this is about what the migrations grant and
the suite's shared schema comes from `create_all`, which grants nothing.
`SET ROLE` rather than logging in: the role has no password, and giving it
one would be a change to the cluster outside any `_test` database.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import MAINTENANCE_DATABASE
from conftest import test_database_url as the_test_database_url
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from itgraph.metrics.series import read_families

REPO_ROOT = Path(__file__).parent.parent
ROLE = "itgraph_metrics"
# Any number: it only has to be the same for every worker building one.
MIGRATION_LOCK = 720_306_941


def alembic(url: URL, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": url.render_as_string(hide_password=False),
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_metrics_role_test"
    url = base.set(database=name)
    assert name.endswith("_test")
    admin_url = base.set(database=MAINTENANCE_DATABASE)

    async def build() -> None:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
                # A role is the cluster's: two workers creating one at the
                # same moment would collide inside its `IF NOT EXISTS`.
                await conn.execute(
                    text(f"SELECT pg_advisory_lock({MIGRATION_LOCK})")
                )
                try:
                    done = await asyncio.to_thread(
                        alembic, url, "upgrade", "head"
                    )
                finally:
                    await conn.execute(
                        text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})")
                    )
                assert done.returncode == 0, done.stdout + done.stderr
        finally:
            await admin.dispose()

    async def drop() -> None:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
        finally:
            await admin.dispose()

    try:
        asyncio.run(build())
    except OSError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    try:
        yield url
    finally:
        asyncio.run(drop())


async def as_role(url: URL, statement: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def columns_of_raw_tables(url: URL) -> list[tuple[str, str]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name, column_name "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name LIKE 'yt\\_raw\\_%' "
                    "ORDER BY 1, 2"
                )
            )
            return [(row[0], row[1]) for row in rows]
    finally:
        await engine.dispose()


def test_the_series_read_through_the_role(migrated: URL) -> None:
    async def read() -> set[str]:
        engine = create_async_engine(migrated)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
                session = AsyncSession(bind=conn)
                read = await read_families(session, now=datetime.now(UTC))
                return {family.name for family in read}
        finally:
            await engine.dispose()

    assert len(asyncio.run(read())) == 12


def test_the_role_reads_when_and_nothing_else_of_api_data(
    migrated: URL,
) -> None:
    allowed = {
        ("yt_raw_channels", "fetched_at"),
        ("yt_raw_videos", "fetched_at"),
        ("yt_raw_video_stats", "observed_at"),
        ("yt_raw_lookups", "fetched_at"),
        ("yt_raw_channel_sections", "fetched_at"),
    }
    columns = asyncio.run(columns_of_raw_tables(migrated))
    assert ("yt_raw_channels", "payload") in columns

    for table, column in columns:
        statement = f'SELECT "{column}" FROM {table}'
        if (table, column) in allowed:
            asyncio.run(as_role(migrated, statement))
        else:
            with pytest.raises(DBAPIError, match="permission denied"):
                asyncio.run(as_role(migrated, statement))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT title FROM channels",
        "SELECT username FROM channels",
        "SELECT discovered_via FROM channels",
        "SELECT payload FROM raw_messages",
        "SELECT reject_note FROM yt_channels",
        "SELECT query FROM yt_search_queries",
    ],
)
def test_the_role_cannot_read_what_it_has_no_use_for(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(as_role(migrated, statement))


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM alerts",
        "UPDATE poll_state SET consecutive_failures = 0",
        "INSERT INTO yt_quota_days (day, pool, spent) VALUES (now(), 'watch', 1)",
        "UPDATE channels SET is_chat = false",
        "TRUNCATE flood_events",
    ],
)
def test_the_role_can_write_nothing(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(as_role(migrated, statement))


def test_the_migration_goes_down_and_up_again(migrated: URL) -> None:
    # By name, not "-1": a later revision sits on top of this one.
    down = alembic(migrated, "downgrade", "a89cb273f885")
    assert down.returncode == 0, down.stdout + down.stderr

    with pytest.raises(DBAPIError):
        asyncio.run(as_role(migrated, "SELECT count(*) FROM poll_state"))

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(as_role(migrated, "SELECT count(*) FROM poll_state"))
