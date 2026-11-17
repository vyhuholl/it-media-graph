"""The migrations and the models name the same things.

Production's schema is built by the Alembic revisions; the suite's by
`Base.metadata.create_all`. Nothing else compares the two, and a rename is
exactly where they could part without anyone seeing it: production would
run one set of names and every other test another. So this builds a
database of its own with `alembic upgrade head` and compares what it holds
in `public` with the suite's schema, by name: tables, views, constraints,
indexes, enum types and sequences.

Names, not the full DDL: what a revision can get wrong while every other
test stays green is what something is called. Every Alembic run goes
through `conftest.alembic`, which holds the cluster-wide migration lock.
"""

import asyncio
from collections.abc import Iterator

import pytest
from conftest import MAINTENANCE_DATABASE, alembic
from conftest import test_database_url as the_test_database_url
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

# What only a migrated database holds, by construction: the revision
# table Alembic keeps for itself.
ALEMBIC_ONLY = {
    ("table", "", "alembic_version"),
    ("index", "", "alembic_version_pkc"),
    ("constraint", "alembic_version", "alembic_version_pkc"),
}

# Each kind of name, read from the catalog of `public`. A constraint is
# named with its table, since a constraint's name is unique only there.
# `n` is left out: from PostgreSQL 18 a NOT NULL is a constraint with a
# generated name, which no revision and no model names.
NAMES = text(
    """
    SELECT CASE c.relkind
             WHEN 'r' THEN 'table' WHEN 'p' THEN 'table'
             WHEN 'v' THEN 'view' WHEN 'i' THEN 'index'
             WHEN 'S' THEN 'sequence'
           END AS kind,
           '' AS owner,
           c.relname AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
    WHERE c.relkind IN ('r', 'p', 'v', 'i', 'S')
    UNION ALL
    SELECT 'constraint', r.relname, k.conname
    FROM pg_constraint k
    JOIN pg_class r ON r.oid = k.conrelid
    JOIN pg_namespace n ON n.oid = r.relnamespace AND n.nspname = 'public'
    WHERE k.contype <> 'n'
    UNION ALL
    SELECT 'enum', '', t.typname
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace AND n.nspname = 'public'
    WHERE t.typtype = 'e'
    """
)


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_schema_names_test"
    url = base.set(database=name)
    assert name.endswith("_test")
    admin_url = base.set(database=MAINTENANCE_DATABASE)

    async def recreate(*, build: bool) -> None:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
                if build:
                    await conn.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await admin.dispose()

    try:
        asyncio.run(recreate(build=True))
    except OSError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    done = alembic(url, "upgrade", "head")
    assert done.returncode == 0, done.stdout + done.stderr
    try:
        yield url
    finally:
        asyncio.run(recreate(build=False))


async def names_in(url: URL) -> set[tuple[str, str, str]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(NAMES)).all()
    finally:
        await engine.dispose()
    return {(row.kind, row.owner, row.name) for row in rows}


def describe(names: set[tuple[str, str, str]]) -> str:
    return "\n".join(
        f"  {kind} {f'{owner}.' if owner else ''}{name}"
        for kind, owner, name in sorted(names)
    )


def test_the_migrations_and_the_models_name_the_same_things(
    migrated: URL, _test_schema: URL
) -> None:
    from_revisions = asyncio.run(names_in(migrated)) - ALEMBIC_ONLY
    from_models = asyncio.run(names_in(_test_schema))
    # Two empty reads would agree too.
    assert ("table", "", "yt_channels") in from_models & from_revisions

    only_migrated = from_revisions - from_models
    only_modelled = from_models - from_revisions

    assert not only_migrated and not only_modelled, (
        "the migrated schema and the models disagree on names\n"
        f"only in the migrated database:\n{describe(only_migrated)}\n"
        f"only in the models' schema:\n{describe(only_modelled)}"
    )


def test_the_revision_table_is_the_only_thing_set_aside(migrated: URL) -> None:
    from_revisions = asyncio.run(names_in(migrated))

    assert ALEMBIC_ONLY <= from_revisions
