"""The refresh runs' revision, on a database the migrations built.

A database of its own, for the reasons `test_alert_roles` gives: what is
under test is what the revision grants, and the suite's shared schema
comes from `create_all`, which grants nothing.
"""

import asyncio
from collections.abc import Iterator

import pytest
from conftest import MAINTENANCE_DATABASE, alembic
from conftest import test_database_url as the_test_database_url
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

METRICS = "itgraph_metrics"
BEFORE = "549774d4e09c"

SEED = """
INSERT INTO yt_refresh_runs
    (started_at, finished_at, outcome, late_due, late_read)
VALUES ('2026-11-17 02:00+00', '2026-11-17 02:09+00', 'complete', 120, 120);
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_refresh_runs_test"
    url = base.set(database=name)
    assert name.endswith("_test")
    admin_url = base.set(database=MAINTENANCE_DATABASE)

    async def recreate(create: bool) -> None:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
                if create:
                    await conn.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await admin.dispose()

    try:
        asyncio.run(recreate(create=True))
    except OSError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    done = alembic(url, "upgrade", "head")
    assert done.returncode == 0, done.stdout + done.stderr
    asyncio.run(run(url, SEED))
    try:
        yield url
    finally:
        asyncio.run(recreate(create=False))


async def run(url: URL, script: str, *, role: str | None = None) -> None:
    """Statements in one transaction, as ``role`` if given, rolled back
    when a role is given so a test leaves the rows as they were."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            if role:
                await conn.execute(text(f"SET LOCAL ROLE {role}"))
            for statement in filter(
                None, (s.strip() for s in script.split(";"))
            ):
                await conn.execute(text(statement))
            if role:
                await transaction.rollback()
            else:
                await transaction.commit()
    finally:
        await engine.dispose()


async def scalar(url: URL, query: str) -> object:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text(query))
    finally:
        await engine.dispose()


def test_the_exporter_reads_when_a_run_ended_and_how(migrated: URL) -> None:
    asyncio.run(
        run(
            migrated,
            "SELECT max(finished_at) FROM yt_refresh_runs "
            "WHERE outcome = 'complete'",
            role=METRICS,
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT late_due FROM yt_refresh_runs",
        "SELECT late_read FROM yt_refresh_runs",
        "SELECT error FROM yt_refresh_runs",
        "SELECT started_at FROM yt_refresh_runs",
    ],
)
def test_the_exporter_cannot_read_a_run_s_counts(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO yt_refresh_runs (started_at, finished_at, outcome) "
            "VALUES (now(), now(), 'complete')"
        ),
        "UPDATE yt_refresh_runs SET outcome = 'failed'",
        "DELETE FROM yt_refresh_runs",
    ],
)
def test_the_exporter_writes_nothing(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('yt_refresh_runs')"))
        is None
    )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(
        run(
            migrated,
            "SELECT max(finished_at) FROM yt_refresh_runs",
            role=METRICS,
        )
    )
