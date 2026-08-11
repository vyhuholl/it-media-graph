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
    """
    url = make_url(str(settings.database_url))
    name = url.database or ""
    if not name.endswith("_test"):
        name = f"{name}_test"
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


@pytest.fixture
def database_url() -> Iterator[str]:
    """A freshly created ``*_test`` database, dropped afterwards.

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
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(teardown())


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
