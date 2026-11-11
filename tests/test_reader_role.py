"""The reader's role, on databases the migrations built.

Databases of their own, because this is about what the migrations grant
and the suite's shared schema comes from `create_all`, which grants
nothing. `SET ROLE` rather than logging in: the role has no password, and
giving it one would be a change to the cluster outside any `_test`
database. That has a consequence worth stating: a role's settings apply at
login, not on `SET ROLE`, so here a session is not read-only, every write
meets the grants directly, and the settings are read from the catalog.
How a session that logs in behaves is the end-to-end suite's.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from conftest import MAINTENANCE_DATABASE, alembic
from conftest import test_database_url as the_test_database_url
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

ROLE = "itgraph_reader"
# The revision before the reader's: a downgrade to it removes the reader.
BEFORE_READER = "ea870c053ba7"

GUARDS = {
    "default_transaction_read_only=on",
    "work_mem=16MB",
    "statement_timeout=10min",
    "idle_in_transaction_session_timeout=5min",
}


async def _admin(statement: str) -> None:
    base = the_test_database_url()
    admin = create_async_engine(
        base.set(database=MAINTENANCE_DATABASE), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await admin.dispose()


@contextmanager
def database_at_head(suffix: str) -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + f"_{suffix}_test"
    assert name.endswith("_test")
    url = base.set(database=name)
    drop = f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'

    try:
        asyncio.run(_admin(drop))
    except OSError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        done = alembic(url, "upgrade", "head")
        assert done.returncode == 0, done.stdout + done.stderr
        yield url
    finally:
        asyncio.run(_admin(drop))


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    with database_at_head("reader_role") as url:
        yield url


async def _rows(url: URL, statement: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(statement))
            return [tuple(row) for row in result]
    finally:
        await engine.dispose()


def rows(url: URL, statement: str) -> list[tuple[object, ...]]:
    return asyncio.run(_rows(url, statement))


def relations(url: URL, kinds: tuple[str, ...]) -> list[str]:
    listed = ", ".join(f"'{kind}'" for kind in kinds)
    return [
        str(row[0])
        for row in rows(
            url,
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = 'public' AND table_type IN ({listed}) "
            "ORDER BY 1",
        )
    ]


async def _as_reader(url: URL, statements: list[str]) -> dict[str, str | None]:
    """Each statement in a transaction of its own, under the role.

    What each was refused with, or `None` where it ran. Rolled back either
    way, so nothing a statement did outlives it.
    """
    outcome: dict[str, str | None] = {}
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            for statement in statements:
                transaction = await conn.begin()
                try:
                    await conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
                    await conn.execute(text(statement))
                    outcome[statement] = None
                except DBAPIError as exc:
                    outcome[statement] = str(exc.orig)
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()
    return outcome


def as_reader(url: URL, statements: list[str]) -> dict[str, str | None]:
    return asyncio.run(_as_reader(url, statements))


def test_every_table_and_view_is_readable(migrated: URL) -> None:
    readable = relations(migrated, ("BASE TABLE", "VIEW"))
    # The tables that hold API data, the operator's notes and the view of
    # families are read like any other.
    assert {"yt_raw_channels", "channels", "channel_families"} <= set(readable)

    outcome = as_reader(
        migrated, [f'SELECT * FROM "{t}" LIMIT 1' for t in readable]
    )

    assert {s: why for s, why in outcome.items() if why is not None} == {}


async def _owner(url: URL, statement: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


def test_a_table_created_after_the_migration_is_readable(
    migrated: URL,
) -> None:
    # By the role that ran the migrations, with no grant of its own; dropped
    # after, so the module's other tests see the schema the revisions built.
    asyncio.run(
        _owner(migrated, "CREATE TABLE created_later (id integer PRIMARY KEY)")
    )
    try:
        assert as_reader(migrated, ["SELECT * FROM created_later"]) == {
            "SELECT * FROM created_later": None
        }
    finally:
        asyncio.run(_owner(migrated, "DROP TABLE created_later"))


def writes(url: URL) -> list[str]:
    first_column = {
        str(table): str(column)
        for table, column in rows(
            url,
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND ordinal_position = 1",
        )
    }
    statements = []
    for table in relations(url, ("BASE TABLE",)):
        column = first_column[table]
        statements += [
            f'INSERT INTO "{table}" DEFAULT VALUES',
            f'UPDATE "{table}" SET "{column}" = "{column}"',
            f'DELETE FROM "{table}"',
            f'TRUNCATE "{table}"',
        ]
    return statements


def test_no_table_can_be_written(migrated: URL) -> None:
    statements = writes(migrated)
    assert statements

    outcome = as_reader(migrated, statements)

    assert {
        s: why
        for s, why in outcome.items()
        if why is None or "permission denied" not in why
    } == {}


def test_no_sequence_and_no_new_table(migrated: URL) -> None:
    sequences = [
        str(row[0])
        for row in rows(
            migrated,
            "SELECT sequence_name FROM information_schema.sequences "
            "WHERE sequence_schema = 'public'",
        )
    ]
    assert "alerts_id_seq" in sequences
    statements = [f"SELECT nextval('\"{s}\"')" for s in sequences]
    statements.append("CREATE TABLE made_by_the_reader (id integer)")

    outcome = as_reader(migrated, statements)

    assert {
        s: why
        for s, why in outcome.items()
        if why is None or "permission denied" not in why
    } == {}


def test_the_catalog_says_select_and_nothing_else(migrated: URL) -> None:
    granted = rows(
        migrated,
        "SELECT c.relname, a.privilege_type "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, "
        "aclexplode(c.relacl) a "
        f"WHERE n.nspname = 'public' AND a.grantee = '{ROLE}'::regrole",
    )

    assert {privilege for _, privilege in granted} == {"SELECT"}
    assert {str(relation) for relation, _ in granted} >= set(
        relations(migrated, ("BASE TABLE", "VIEW"))
    )
    [(on_schema,)] = rows(
        migrated,
        "SELECT array_agg(a.privilege_type::text) FROM pg_namespace n, "
        "aclexplode(n.nspacl) a "
        f"WHERE n.nspname = 'public' AND a.grantee = '{ROLE}'::regrole",
    )
    assert on_schema == ["USAGE"]
    [(by_default,)] = rows(
        migrated,
        "SELECT array_agg(a.privilege_type::text) FROM pg_default_acl d, "
        "aclexplode(d.defaclacl) a "
        f"WHERE a.grantee = '{ROLE}'::regrole",
    )
    assert by_default == ["SELECT"]


def test_the_role_is_created_guarded_and_unable_to_log_in(
    migrated: URL,
) -> None:
    [(can_login, limit, settings)] = rows(
        migrated,
        "SELECT rolcanlogin, rolconnlimit, rolconfig FROM pg_roles "
        f"WHERE rolname = '{ROLE}'",
    )

    assert can_login is False
    assert limit == 4
    assert isinstance(settings, list)
    assert set(settings) == GUARDS


GRANTEE = f"a.grantee = '{ROLE}'::regrole"
# What in a database can refer to the reader: a privilege on a table, the
# schema or the database itself, and a default privilege.
REFERENCES = (
    f"SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a WHERE {GRANTEE}",
    f"SELECT count(*) FROM pg_namespace n, aclexplode(n.nspacl) a WHERE {GRANTEE}",
    (
        "SELECT count(*) FROM pg_database d, aclexplode(d.datacl) a "
        f"WHERE d.datname = current_database() AND {GRANTEE}"
    ),
    f"SELECT count(*) FROM pg_default_acl d, aclexplode(d.defaclacl) a WHERE {GRANTEE}",
)


def test_a_downgrade_leaves_nothing_and_keeps_a_role_still_used() -> None:
    with (
        database_at_head("reader_down") as down,
        database_at_head("reader_kept") as kept,
    ):
        done = alembic(down, "downgrade", BEFORE_READER)
        assert done.returncode == 0, done.stdout + done.stderr

        assert [rows(down, query) for query in REFERENCES] == [[(0,)]] * len(
            REFERENCES
        )
        assert rows(
            down, f"SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}'"
        ) == [(1,)]
        assert as_reader(kept, ["SELECT * FROM channels"]) == {
            "SELECT * FROM channels": None
        }
