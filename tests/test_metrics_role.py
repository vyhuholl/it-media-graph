"""The exporter's role, on a database the migrations built.

A database of its own, because this is about what the migrations grant and
the suite's shared schema comes from `create_all`, which grants nothing.
`SET ROLE` rather than logging in: the role has no password, and giving it
one would be a change to the cluster outside any `_test` database. Every
Alembic run goes through `conftest.alembic`, which holds the cluster-wide
migration lock: the revisions create and alter roles.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from conftest import MAINTENANCE_DATABASE, alembic
from conftest import test_database_url as the_test_database_url
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from itgraph.metrics.series import read_families

ROLE = "itgraph_metrics"


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
    done = alembic(url, "upgrade", "head")
    assert done.returncode == 0, done.stdout + done.stderr
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

    assert len(asyncio.run(read())) == 16


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


@pytest.mark.parametrize("table", ["baseline_runs", "yt_baseline_runs"])
def test_the_role_reads_when_a_run_completed_and_what_it_counted(
    migrated: URL, table: str
) -> None:
    asyncio.run(
        as_role(
            migrated,
            "SELECT completed_at, channels_in_scope, channels_with_baseline "
            f"FROM {table}",
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT median FROM channel_baselines",
        "SELECT median FROM yt_channel_baselines",
        "SELECT channel_id FROM yt_channel_baselines",
        "SELECT factor FROM metric_baselines",
        "SELECT factor FROM yt_metric_baselines",
        "SELECT fraction FROM curve_points",
        "SELECT fraction FROM yt_curve_points",
        "SELECT mature_days FROM baseline_runs",
        "SELECT channels_on_fallback FROM yt_baseline_runs",
    ],
)
def test_the_role_cannot_read_a_baseline(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(as_role(migrated, statement))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT video_id FROM yt_weekly_entries",
        "SELECT value FROM yt_weekly_entries",
        "SELECT videos_scored FROM yt_weekly_lists",
        "SELECT week_start FROM yt_weekly_lists",
    ],
)
def test_the_role_cannot_read_what_a_list_holds(
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
