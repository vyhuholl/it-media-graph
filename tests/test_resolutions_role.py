"""The rising candidates' revision, on a database the migrations built.

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
BEFORE = "f07160617ce5"

SEED = """
INSERT INTO tg_resolutions (requested_at, command, kind, outcome)
VALUES (now() - interval '1 hour', 'watch', 'username', 'not_channel');
"""

# What the series asks, as the exporter.
SERIES = (
    "SELECT command, count(*) FROM tg_resolutions "
    "WHERE requested_at > now() - interval '24 hours' GROUP BY command"
)


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_resolutions_test"
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


def test_the_exporter_reads_when_and_by_which_command(migrated: URL) -> None:
    asyncio.run(run(migrated, SERIES, role=METRICS))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT kind FROM tg_resolutions",
        "SELECT outcome FROM tg_resolutions",
        "SELECT id FROM tg_resolutions",
        "SELECT * FROM tg_resolutions",
    ],
)
def test_the_exporter_cannot_read_what_a_resolution_found(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO tg_resolutions (command, kind, outcome) "
            "VALUES ('watch', 'id', 'channel')"
        ),
        "UPDATE tg_resolutions SET command = 'add'",
        "DELETE FROM tg_resolutions",
    ],
)
def test_the_exporter_writes_nothing(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('tg_resolutions')"))
        is None
    )
    assert (
        asyncio.run(
            scalar(
                migrated,
                "SELECT count(*) FROM pg_type "
                "WHERE typname LIKE 'tg_resolution%'",
            )
        )
        == 0
    )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(run(migrated, SERIES, role=METRICS))
