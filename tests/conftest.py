"""Shared fixtures.

No network: Telethon is mocked. Postgres is real, but always a throwaway
database whose name ends in ``_test``.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

# itgraph.config validates on import, so the environment has to be complete
# before anything from the package is imported. Values already present
# (a developer's shell) win — these are only fallbacks.
#
# `.env` is read first, and it has to be: the fallback below is the
# compose file's *default* password, which is what a laptop running
# `docker compose up` with no `.env` gets. A deployed host has a real
# password, and a fallback set before pydantic-settings reads the file
# takes priority over the file — so on that host every test failed to
# connect while the working database sat there answering. Nothing here
# weakens `test_database_url`: whatever URL arrives, the suffix rail
# still redirects it at the `_test` sibling.
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

os.environ.setdefault("TELEGRAM_API_ID", "0")
os.environ.setdefault("TELEGRAM_API_HASH", "test-api-hash")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://itgraph:itgraph@localhost:5433/itgraph",
)

from fakes import FakeTelegramClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from itgraph.config import settings

# Imported for its side effect: it attaches the view DDL to
# `Base.metadata`, so `create_all` below builds it the way a migration
# would. Without this the test schema is missing `channel_families` and
# every family query fails on a database that looks otherwise complete.
from itgraph.db import session_lease as session_lease_module
from itgraph.db import views as _views  # noqa: F401
from itgraph.db.models import Base
from itgraph.db.session import Database

MAINTENANCE_DATABASE = "postgres"
FIXTURES = Path(__file__).parent / "fixtures"


def test_database_url() -> URL:
    """The configured URL, redirected at the ``_test`` database.

    The suffix check is the safety rail that keeps a mistyped
    ``DATABASE_URL`` from dropping the real database. Never weaken it.

    Under xdist every worker gets a database of its own. Tests empty the
    tables between runs rather than dropping the database, and shared
    tables would mean one worker's cleanup wiping another worker's rows
    mid-test. The suffix is stripped and re-added rather than appended
    to, so a `DATABASE_URL` already pointing at a `_test` database yields
    `itgraph_gw0_test` and not `itgraph_test_gw0_test`.
    """
    url = make_url(str(settings.database_url))
    name = url.database or ""
    name = name.removesuffix("_test")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    name = f"{name}_{worker}_test" if worker else f"{name}_test"
    if not name.endswith("_test"):  # pragma: no cover - defensive
        raise RuntimeError(f"refusing to use database {name!r} for tests")
    return url.set(database=name)


@pytest.fixture(autouse=True)
def no_session_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop any test from taking the real session lease.

    The lease is deliberately hard to bypass in production — every
    networked command goes through `connected`, which takes it — and that
    would otherwise make every CLI test open a connection to whatever
    `DATABASE_URL` points at, which is the *working* database, not the
    throwaway one. Neutralized here for the suite as a whole so no test
    has to remember.

    `SessionLease` itself is exercised directly in
    ``tests/test_session_lease.py``, against the test database, and that
    module opts out of this fixture.
    """

    @asynccontextmanager
    async def nothing(command: str, **kwargs: Any) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(session_lease_module, "session_lease", nothing)


@pytest.fixture(scope="session")
def _test_schema() -> Iterator[URL]:
    """The throwaway database and its schema, built once for the run.

    Once, not per test, because building it costs 417 ms against the
    137 ms of emptying it — eighteen tables, their enums and the view,
    rebuilt for each of the ~340 tests that touch Postgres, was two
    thirds of the suite's wall clock.

    A run that is killed leaves its databases behind. That is why the
    drop below runs before the create as well as after: the next run
    reclaims what the last one could not.

    Setup runs its own ``asyncio.run`` rather than being an async
    fixture: a CLI test drives the app through ``asyncio.run`` too, and
    that cannot start while a fixture's loop is still running.
    """
    url = test_database_url()
    name = url.database
    assert name is not None and name.endswith("_test")
    admin_url = url.set(database=MAINTENANCE_DATABASE)
    # FORCE, because a CLI test may have left a connection behind.
    drop = text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    async def create() -> None:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(drop)
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await admin.dispose()

        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    async def teardown() -> None:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(drop)
        finally:
            await admin.dispose()

    try:
        asyncio.run(create())
    except (OSError, SQLAlchemyError) as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")

    try:
        yield url
    finally:
        asyncio.run(teardown())


@pytest.fixture
def database_url(_test_schema: URL) -> Iterator[str]:
    """An empty ``*_test`` database: the project's schema, no rows.

    The same thing a freshly created database gave every test before,
    including sequences back at their start — a test that asserts on a
    generated id must not depend on how many tests ran before it.

    Emptied on the way in rather than on the way out, so a failed test
    leaves its rows behind to be looked at.
    """
    url = _test_schema
    tables = ", ".join(
        f'"{table.name}"' for table in Base.metadata.sorted_tables
    )
    empty = text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    # What DROP DATABASE ... WITH (FORCE) did implicitly, and had to: a
    # CLI test can leave a connection behind, and it holds locks that
    # TRUNCATE would then wait on until the suite gave up.
    evict = text(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid()"
    )

    async def clean() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(evict)
                await conn.execute(empty)
        finally:
            await engine.dispose()

    asyncio.run(clean())
    yield url.render_as_string(hide_password=False)


@pytest.fixture
async def database(database_url: str) -> AsyncIterator[Database]:
    """A ``Database`` on the throwaway test database."""
    db = Database(database_url)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
def dialog_records() -> list[dict[str, Any]]:
    """Anonymized dialog list. No real channel appears here."""
    with (FIXTURES / "dialogs.json").open(encoding="utf-8") as handle:
        records: list[dict[str, Any]] = json.load(handle)
    return records


@pytest.fixture
def telegram(dialog_records: list[dict[str, Any]]) -> FakeTelegramClient:
    return FakeTelegramClient(dialog_records)
