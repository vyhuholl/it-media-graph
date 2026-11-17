"""The dump `alembic upgrade` takes first, and the tier it goes to.

In-process, through Alembic's own command dispatch, so that what runs is
`env.py`'s wiring. Nothing reaches a database: the backup layer's two
calls are replaced, and so is the engine, before it could connect. The
URL names a database that is not a scratch one — a scratch database gets
no dump at all — and that does not exist, so that if the engine were
ever built after all, there would be nothing behind it to migrate.
"""

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

from itgraph.config import settings
from itgraph.db import backup as backup_module
from itgraph.db.backup import BackupError, BackupKind, premigration_kind
from itgraph.db.guard import SKIP_BACKUP

MIGRATIONS = (
    Path(__file__).parent.parent / "src" / "itgraph" / "db" / "migrations"
)

PROBE = (
    "postgresql+asyncpg://itgraph:itgraph@localhost:5433/itgraph_no_such_db"
)


class EngineReachedError(Exception):
    """The upgrade got past its dump and asked for a connection."""


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[list[BackupKind]]:
    """The tiers each backup was asked for; no engine is ever built."""
    calls: list[list[BackupKind]] = []

    def run_backup(*, kinds: list[BackupKind], **_: Any) -> list[Any]:
        calls.append(list(kinds))
        return []

    def no_engine(*args: Any, **kwargs: Any) -> Any:
        raise EngineReachedError

    monkeypatch.setattr(settings, "database_url", PROBE)
    monkeypatch.setattr(backup_module, "run_backup", run_backup)
    monkeypatch.setattr(backup_module, "is_empty_database", lambda: False)
    monkeypatch.setattr(
        sqlalchemy_asyncio, "async_engine_from_config", no_engine
    )
    monkeypatch.delenv(SKIP_BACKUP, raising=False)
    return calls


def upgrade() -> None:
    """``alembic upgrade head``, from a configuration with no file.

    No file on purpose: given one, `env.py` hands the process's logging
    to ``fileConfig``, which disables every logger already created — the
    ones other tests in this worker capture included.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("path_separator", "os")
    command.upgrade(config, "head")


def test_an_upgrade_takes_the_premigration_tier(
    asked: list[list[BackupKind]],
) -> None:
    """Its own tier, so the full tier's schedule, bounds, pointer and
    off-site copying never see it."""
    with pytest.raises(EngineReachedError):
        upgrade()

    assert asked == [[premigration_kind()]]


def test_a_failed_dump_refuses_the_migration(
    asked: list[list[BackupKind]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*, kinds: list[BackupKind], **_: Any) -> list[Any]:
        raise BackupError("disk full")

    monkeypatch.setattr(backup_module, "run_backup", failing)

    with pytest.raises(CommandError, match="refusing to migrate") as caught:
        upgrade()

    assert SKIP_BACKUP in str(caught.value)


def test_skipping_the_dump_asks_for_none(
    asked: list[list[BackupKind]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SKIP_BACKUP, "1")

    with pytest.raises(EngineReachedError):
        upgrade()

    assert asked == []
