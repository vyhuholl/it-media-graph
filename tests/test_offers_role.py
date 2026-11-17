"""The rising offers' revision, on a database the migrations built.

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

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"
BEFORE = "b13d2bd31424"

SEED = """
INSERT INTO tg_channels (tg_id, username, discovered_via)
VALUES (2000000001, 'fake_rising', 'forward');
INSERT INTO tg_rising_offer_days (day, computed_at, size)
VALUES ('2026-11-18', '2026-11-18 08:00+00', 5);
INSERT INTO tg_rising_offers
    (day, channel_id, rank, families, families_before, offered_at)
VALUES ('2026-11-18', 2000000001, 1, 2, 0, '2026-11-18 08:00+00');
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_offers_test"
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


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM tg_rising_offer_days",
        "SELECT * FROM tg_rising_offers",
        "UPDATE tg_rising_offers SET delivered_at = now()",
        (
            "UPDATE tg_rising_offers SET attempts = attempts + 1, "
            "last_error = 'fake'"
        ),
        (
            "UPDATE tg_rising_offers SET decision = 'seed', kind = 'media', "
            "decided_at = now() WHERE decision IS NULL"
        ),
        (
            "UPDATE tg_rising_offers SET decision = 'rejected', "
            "reject_reason = 'crypto', decided_at = now()"
        ),
    ],
)
def test_the_bot_records_a_delivery_and_a_decision(
    migrated: URL, statement: str
) -> None:
    asyncio.run(run(migrated, statement, role=BOT))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO tg_rising_offer_days (day, computed_at, size) "
            "VALUES ('2026-11-19', now(), 5)"
        ),
        (
            "INSERT INTO tg_rising_offers "
            "(day, channel_id, rank, families, families_before, offered_at) "
            "VALUES ('2026-11-18', 2000000001, 2, 2, 0, now())"
        ),
        "UPDATE tg_rising_offers SET channel_id = 2000000002",
        "UPDATE tg_rising_offers SET rank = 2",
        "UPDATE tg_rising_offers SET families = 9",
        "UPDATE tg_rising_offers SET applied_at = now(), applied = true",
        "UPDATE tg_rising_offer_days SET computed_at = now()",
        "UPDATE tg_channels SET status = 'seed', kind = 'media'",
        "DELETE FROM tg_rising_offers",
    ],
)
def test_the_bot_cannot_write_an_offer_or_apply_a_decision(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=BOT))


def test_the_exporter_reads_the_moments(migrated: URL) -> None:
    asyncio.run(
        run(
            migrated,
            "SELECT max(computed_at) FROM tg_rising_offer_days; "
            "SELECT min(offered_at) FROM tg_rising_offers "
            "WHERE delivered_at IS NULL; "
            "SELECT min(decided_at) FROM tg_rising_offers "
            "WHERE decided_at IS NOT NULL AND applied_at IS NULL",
            role=METRICS,
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT channel_id FROM tg_rising_offers",
        "SELECT rank FROM tg_rising_offers",
        "SELECT families FROM tg_rising_offers",
        "SELECT decision FROM tg_rising_offers",
        "SELECT kind FROM tg_rising_offers",
        "SELECT reject_reason FROM tg_rising_offers",
        "SELECT day FROM tg_rising_offers",
        "SELECT day FROM tg_rising_offer_days",
    ],
)
def test_the_exporter_cannot_read_what_was_offered_or_decided(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('tg_rising_offers')"))
        is None
    )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(
        run(
            migrated,
            "SELECT max(computed_at) FROM tg_rising_offer_days",
            role=METRICS,
        )
    )
