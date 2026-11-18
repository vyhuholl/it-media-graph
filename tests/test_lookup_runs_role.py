"""The lookup runs' grants, on a database the migrations built.

A database of its own, for the reasons `test_alert_roles` gives: what
is under test is what the revision grants, and the suite's shared
schema comes from `create_all`, which grants nothing. The exporter
reads a run's moments and nothing else — not its counts, not how it
ended.
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
# The revision before the one that creates `yt_lookup_runs`.
BEFORE = "f6308589c4d1"

SEED = """
INSERT INTO yt_lookup_runs
    (started_at, finished_at, outcome,
     tg_requests, tg_awaiting, mention_requests, mention_awaiting)
VALUES ('2026-11-18 03:00+00', '2026-11-18 03:02+00', 'complete', 10, 4, 6, 0)
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_lookup_runs_test"
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
    asyncio.run(_run(url, SEED))
    try:
        yield url
    finally:
        asyncio.run(recreate(create=False))


async def _run(url: URL, script: str, *, role: str | None = None) -> None:
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


def test_the_exporter_reads_when_a_run_ended(migrated: URL) -> None:
    asyncio.run(
        _run(
            migrated,
            "SELECT max(finished_at) FROM yt_lookup_runs",
            role=METRICS,
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT outcome FROM yt_lookup_runs",
        "SELECT tg_requests FROM yt_lookup_runs",
        "SELECT tg_awaiting FROM yt_lookup_runs",
        "SELECT mention_requests FROM yt_lookup_runs",
        "SELECT mention_awaiting FROM yt_lookup_runs",
        "SELECT error FROM yt_lookup_runs",
    ],
)
def test_the_exporter_cannot_read_a_run_s_counts_or_outcome(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(_run(migrated, statement, role=METRICS))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO yt_lookup_runs (started_at, finished_at, outcome) "
            "VALUES (now(), now(), 'complete')"
        ),
        "UPDATE yt_lookup_runs SET outcome = 'failed'",
        "DELETE FROM yt_lookup_runs",
    ],
)
def test_the_exporter_writes_nothing(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(_run(migrated, statement, role=METRICS))


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('yt_lookup_runs')"))
        is None
    )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(
        _run(
            migrated,
            "SELECT max(finished_at) FROM yt_lookup_runs",
            role=METRICS,
        )
    )
