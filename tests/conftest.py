"""Shared fixtures.

No network: Telethon is mocked. Postgres is real, but always a throwaway
database whose name ends in ``_test``.
"""

import os
from collections.abc import AsyncIterator

import pytest

# itgraph.config validates on import, so the environment has to be complete
# before anything from the package is imported. Values already present
# (CI, a developer's shell) win — these are only fallbacks.
os.environ.setdefault("TELEGRAM_API_ID", "0")
os.environ.setdefault("TELEGRAM_API_HASH", "test-api-hash")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://itgraph:itgraph@localhost:5433/itgraph",
)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from itgraph.config import settings  # noqa: E402
from itgraph.db import Database  # noqa: E402
from itgraph.db.models import Base  # noqa: E402

MAINTENANCE_DATABASE = "postgres"


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


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    """A freshly created ``*_test`` database, dropped afterwards."""
    url = test_database_url()
    name = url.database
    assert name is not None and name.endswith("_test")

    admin = create_async_engine(
        url.set(database=MAINTENANCE_DATABASE),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    except (OSError, SQLAlchemyError) as exc:
        await admin.dispose()
        pytest.skip(f"Postgres is not reachable: {exc}")

    db = Database(url.render_as_string(hide_password=False))
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield db
    finally:
        await db.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()
