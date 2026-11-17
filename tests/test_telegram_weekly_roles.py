"""Telegram's weekly lists' revision, on a database the migrations built.

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
BEFORE = "065cd92181b4"

CHANNEL = 4000000001
READ_AT = "2026-11-18 10:00+00"

SEED = f"""
INSERT INTO tg_channels (tg_id, username, title, discovered_via, status)
VALUES ({CHANNEL}, 'fake_weekly_channel', 'Fake', 'manual', 'seed');
INSERT INTO tg_raw_messages (channel_id, msg_id, payload)
VALUES ({CHANNEL}, 10, '{{"_": "Message", "id": 10}}'),
       ({CHANNEL}, 11, '{{"_": "Message", "id": 11}}');
INSERT INTO tg_message_metrics (channel_id, msg_id, observed_at, views)
VALUES ({CHANNEL}, 10, '{READ_AT}', 100), ({CHANNEL}, 11, '{READ_AT}', 50);
INSERT INTO tg_weekly_lists
    (week_start, computed_at, threshold, size, comments_min,
     posts_scored, channels_above)
VALUES ('2026-11-16', '2026-11-23 07:00+00', 3.7, 10, 10, 50, 1);
INSERT INTO tg_weekly_entries
    (list_id, rank, channel_id, msg_id, scored_at, metric, value,
     observed, expected, others)
SELECT id, 1, {CHANNEL}, 10, '{READ_AT}', 'views', 4.2, 100, 20, 0
FROM tg_weekly_lists WHERE week_start = '2026-11-16';
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_tg_weekly_roles_test"
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
        "SELECT * FROM tg_weekly_lists",
        "SELECT * FROM tg_weekly_entries",
        "UPDATE tg_weekly_lists SET delivered_at = now()",
        "UPDATE tg_weekly_lists SET attempts = attempts + 1, last_error = 'x'",
        "UPDATE tg_weekly_entries SET delivered_at = now()",
        "SELECT id FROM tg_weekly_lists FOR UPDATE SKIP LOCKED",
    ],
)
def test_the_bot_records_a_list_s_delivery(
    migrated: URL, statement: str
) -> None:
    asyncio.run(run(migrated, statement, role=BOT))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO tg_weekly_lists (week_start, computed_at, threshold, "
            "size, comments_min, posts_scored, channels_above) "
            "VALUES ('2026-11-09', now(), 3.7, 10, 10, 0, 0)"
        ),
        (
            "INSERT INTO tg_weekly_entries (list_id, rank, channel_id, msg_id, "
            "scored_at, metric, value, observed, expected, others) "
            f"SELECT id, 2, {CHANNEL}, 11, '{READ_AT}', 'views', 3, 1, 1, 0 "
            "FROM tg_weekly_lists"
        ),
        "UPDATE tg_weekly_lists SET posts_scored = 0",
        "UPDATE tg_weekly_lists SET week_start = '2026-11-09'",
        "UPDATE tg_weekly_entries SET rank = 5",
        "UPDATE tg_weekly_entries SET value = 99",
        "UPDATE tg_weekly_entries SET observed = 99",
        "UPDATE tg_weekly_entries SET msg_id = 11",
        "UPDATE tg_weekly_entries SET metric = 'reactions'",
        f"UPDATE tg_weekly_entries SET channel_id = {CHANNEL}",
        f"UPDATE tg_weekly_entries SET scored_at = '{READ_AT}'",
        "UPDATE tg_weekly_entries SET expected = 99",
        "UPDATE tg_weekly_entries SET others = 9",
        "DELETE FROM tg_weekly_entries",
        "DELETE FROM tg_weekly_lists",
    ],
)
def test_the_bot_cannot_write_a_list(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=BOT))


def test_the_exporter_reads_a_list_s_moments(migrated: URL) -> None:
    asyncio.run(
        run(
            migrated,
            "SELECT max(computed_at), max(delivered_at) FROM tg_weekly_lists",
            role=METRICS,
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT week_start FROM tg_weekly_lists",
        "SELECT posts_scored FROM tg_weekly_lists",
        "SELECT threshold FROM tg_weekly_lists",
        "SELECT msg_id FROM tg_weekly_entries",
        "SELECT value FROM tg_weekly_entries",
        "SELECT count(*) FROM tg_weekly_entries",
    ],
)
def test_the_exporter_cannot_read_what_a_list_holds(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO tg_weekly_lists (week_start, computed_at, threshold, "
            "size, comments_min, posts_scored, channels_above) "
            "VALUES ('2026-11-02', now(), 3.7, 10, 10, 0, 0)"
        ),
        "UPDATE tg_weekly_lists SET delivered_at = now()",
        "DELETE FROM tg_weekly_lists",
    ],
)
def test_the_exporter_writes_nothing(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


def test_one_list_per_week(migrated: URL) -> None:
    with pytest.raises(DBAPIError, match="uq_tg_weekly_lists_week"):
        asyncio.run(
            run(
                migrated,
                "INSERT INTO tg_weekly_lists (week_start, computed_at, "
                "threshold, size, comments_min, posts_scored, "
                "channels_above) "
                "VALUES ('2026-11-16', now(), 3.7, 10, 10, 0, 0)",
            )
        )


def test_an_entry_names_a_reading_that_exists(migrated: URL) -> None:
    with pytest.raises(
        DBAPIError, match="fk_tg_weekly_entries_channel_id_tg_message_metrics"
    ):
        asyncio.run(
            run(
                migrated,
                "INSERT INTO tg_weekly_lists (week_start, computed_at, "
                "threshold, size, comments_min, posts_scored, "
                "channels_above) "
                "VALUES ('2026-11-02', now(), 3.7, 10, 10, 1, 1);"
                "INSERT INTO tg_weekly_entries (list_id, rank, channel_id, "
                "msg_id, scored_at, metric, value, observed, expected, "
                f"others) SELECT id, 1, {CHANNEL}, 10, "
                "'2026-11-18 11:00+00', 'views', 3, 1, 1, 0 "
                "FROM tg_weekly_lists WHERE week_start = '2026-11-02'",
            )
        )


def test_an_entry_goes_with_its_reading_and_the_list_stays(
    migrated: URL,
) -> None:
    asyncio.run(
        run(
            migrated,
            f"""
            INSERT INTO tg_weekly_lists
                (week_start, computed_at, threshold, size, comments_min,
                 posts_scored, channels_above)
            VALUES ('2026-11-09', now(), 3.7, 10, 10, 1, 1);
            INSERT INTO tg_weekly_entries
                (list_id, rank, channel_id, msg_id, scored_at, metric,
                 value, observed, expected, others)
            SELECT id, 1, {CHANNEL}, 11, '{READ_AT}', 'views', 3, 50, 10, 0
            FROM tg_weekly_lists WHERE week_start = '2026-11-09'
            """,
        )
    )

    asyncio.run(
        run(
            migrated,
            "DELETE FROM tg_message_metrics "
            f"WHERE channel_id = {CHANNEL} AND msg_id = 11",
        )
    )

    entries = asyncio.run(
        scalar(
            migrated,
            "SELECT count(*) FROM tg_weekly_entries e "
            "JOIN tg_weekly_lists l ON l.id = e.list_id "
            "WHERE l.week_start = '2026-11-09'",
        )
    )
    lists = asyncio.run(
        scalar(
            migrated,
            "SELECT count(*) FROM tg_weekly_lists WHERE week_start = '2026-11-09'",
        )
    )
    assert (entries, lists) == (0, 1)


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('weekly_lists')"))
        is None
    )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(
        run(
            migrated,
            "SELECT max(computed_at) FROM tg_weekly_lists",
            role=METRICS,
        )
    )
