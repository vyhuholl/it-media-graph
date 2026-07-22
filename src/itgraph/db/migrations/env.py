import asyncio
import logging
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from alembic.util import CommandError
from itgraph.config import settings
from itgraph.db.backup import BackupError, full_kind, run_backup
from itgraph.db.guard import (
    SKIP_BACKUP,
    DestructiveMigrationError,
    check_downgrade_allowed,
    is_scratch_database,
)
from itgraph.db.models import Base

log = logging.getLogger("alembic.env")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL lives in the environment, not in alembic.ini — there is exactly
# one place that knows how to reach the database. '%' is escaped because
# configparser would otherwise read it as interpolation.
config.set_main_option(
    "sqlalchemy.url", str(settings.database_url).replace("%", "%%")
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


def direction() -> str:
    """``"upgrade"``, ``"downgrade"``, or something else entirely.

    The direction is the function ``alembic.command`` hands to the
    environment, reachable only through the proxy's private handle on the
    live ``EnvironmentContext`` — Alembic publishes no accessor for it.
    That is a deliberate trade: this reads the actual direction rather
    than pattern-matching a command line, and ``tests/test_guard.py``
    drives a real ``alembic downgrade`` through it, so a future Alembic
    moving this attribute fails the suite instead of leaving the barrier
    quietly open.

    Commands that are neither — ``check``, ``revision``, ``stamp`` — name
    their function something else and fall through both branches below.
    """
    proxy = getattr(context, "_proxy", None)
    if proxy is None:  # pragma: no cover - no environment to act on
        return ""
    return str(getattr(proxy.context_opts.get("fn"), "__name__", ""))


def guard_downgrade(url: str) -> None:
    try:
        check_downgrade_allowed(url)
    except DestructiveMigrationError as exc:
        # Alembic renders CommandError as a plain message; letting the
        # original escape buries the instructions under a traceback of
        # its own internals.
        raise CommandError(str(exc)) from exc


def backup_before_upgrade(url: str) -> None:
    """Dump the database before changing its shape.

    A migration is exactly when structural damage happens, and it is rare
    and deliberate enough to be worth the wait. Fails closed: if the dump
    cannot be taken, the migration does not run either.
    """
    if os.environ.get(SKIP_BACKUP) == "1":
        log.warning("%s=1 — migrating without a backup", SKIP_BACKUP)
        return

    log.info("backing up %s before migrating", make_url(url).database)
    try:
        taken = run_backup(kinds=[full_kind()])
    except BackupError as exc:
        raise CommandError(
            f"refusing to migrate: the backup failed ({exc}).\n"
            "Fix the backup, or set "
            f"{SKIP_BACKUP}=1 for this one command if you accept the risk."
        ) from exc
    for item in taken:
        log.info("backed up to %s (%d bytes)", item.path, item.size)


# Offline mode only prints SQL, so there is nothing to guard or to lose.
# A scratch database is disposable by definition and gets neither.
if not context.is_offline_mode() and not is_scratch_database(
    str(settings.database_url)
):
    if direction() == "downgrade":
        guard_downgrade(str(settings.database_url))
    elif direction() == "upgrade":
        backup_before_upgrade(str(settings.database_url))

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
