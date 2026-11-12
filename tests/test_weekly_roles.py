"""The weekly lists' revision, on a database the migrations built.

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
BEFORE = "5e7cef4aa5d0"

CHANNEL = "UCfakeWeeklyChannel00001"
VIDEO = "fakeWeek001"
READ_AT = "2026-11-18 10:00+00"

SEED = f"""
INSERT INTO yt_channels (channel_id, status, kind, lang, discovered_via)
VALUES ('{CHANNEL}', 'seed', 'personal', 'ru', 'manual');
INSERT INTO yt_raw_videos (video_id, payload, fetched_at, discovered_at, discovered_via)
VALUES ('{VIDEO}', '{{"snippet": {{"channelId": "{CHANNEL}"}}}}', now(), now(), 'count');
INSERT INTO yt_raw_video_stats (video_id, observed_at, views)
VALUES ('{VIDEO}', '{READ_AT}', 100);
INSERT INTO yt_weekly_lists
    (week_start, route, computed_at, threshold, size, comments_min,
     videos_scored, channels_above)
VALUES ('2026-11-16', 'ru', '2026-11-23 07:00+00', 2.0, 10, 10, 5, 1);
INSERT INTO yt_weekly_entries
    (list_id, rank, channel_id, video_id, scored_at, metric, value,
     expected, others)
SELECT id, 1, '{CHANNEL}', '{VIDEO}', '{READ_AT}', 'views', 4.2, 20, 0
FROM yt_weekly_lists WHERE week_start = '2026-11-16';
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_weekly_roles_test"
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
        "SELECT * FROM yt_weekly_lists",
        "SELECT * FROM yt_weekly_entries",
        "UPDATE yt_weekly_lists SET delivered_at = now()",
        "UPDATE yt_weekly_lists SET attempts = attempts + 1, last_error = 'x'",
        "UPDATE yt_weekly_entries SET delivered_at = now()",
        "SELECT id FROM yt_weekly_lists FOR UPDATE SKIP LOCKED",
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
            "INSERT INTO yt_weekly_lists (week_start, route, computed_at, "
            "threshold, size, comments_min, videos_scored, channels_above) "
            "VALUES ('2026-11-09', 'en', now(), 2, 10, 10, 0, 0)"
        ),
        (
            "INSERT INTO yt_weekly_entries (list_id, rank, channel_id, "
            "video_id, scored_at, metric, value, expected, others) "
            f"SELECT id, 2, '{CHANNEL}', '{VIDEO}', '{READ_AT}', 'likes', "
            "3, 1, 0 FROM yt_weekly_lists"
        ),
        "UPDATE yt_weekly_lists SET route = 'en'",
        "UPDATE yt_weekly_lists SET videos_scored = 0",
        "UPDATE yt_weekly_entries SET rank = 5",
        "UPDATE yt_weekly_entries SET value = 99",
        "UPDATE yt_weekly_entries SET video_id = 'fakeWeek002'",
        "UPDATE yt_weekly_entries SET metric = 'likes'",
        "DELETE FROM yt_weekly_entries",
        "DELETE FROM yt_weekly_lists",
    ],
)
def test_the_bot_cannot_write_a_list(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=BOT))


def test_the_exporter_reads_a_list_s_language_and_moments(
    migrated: URL,
) -> None:
    asyncio.run(
        run(
            migrated,
            "SELECT route, max(computed_at), max(delivered_at) "
            "FROM yt_weekly_lists GROUP BY route",
            role=METRICS,
        )
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT videos_scored FROM yt_weekly_lists",
        "SELECT threshold FROM yt_weekly_lists",
        "SELECT week_start FROM yt_weekly_lists",
        "SELECT video_id FROM yt_weekly_entries",
        "SELECT value FROM yt_weekly_entries",
        "SELECT count(*) FROM yt_weekly_entries",
    ],
)
def test_the_exporter_cannot_read_what_a_list_holds(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


def test_one_list_per_week_and_language(migrated: URL) -> None:
    with pytest.raises(DBAPIError, match="uq_yt_weekly_lists_week"):
        asyncio.run(
            run(
                migrated,
                "INSERT INTO yt_weekly_lists (week_start, route, computed_at, "
                "threshold, size, comments_min, videos_scored, "
                "channels_above) "
                "VALUES ('2026-11-16', 'ru', now(), 2, 10, 10, 0, 0)",
            )
        )


def test_an_entry_names_a_reading_that_exists(migrated: URL) -> None:
    with pytest.raises(
        DBAPIError, match="fk_yt_weekly_entries_video_id_yt_raw_video_stats"
    ):
        asyncio.run(
            run(
                migrated,
                "INSERT INTO yt_weekly_lists (week_start, route, "
                "computed_at, threshold, size, comments_min, videos_scored, "
                "channels_above) "
                "VALUES ('2026-11-02', 'ru', now(), 2, 10, 10, 1, 1);"
                "INSERT INTO yt_weekly_entries (list_id, rank, channel_id, "
                "video_id, scored_at, metric, value, expected, others) "
                f"SELECT id, 1, '{CHANNEL}', '{VIDEO}', "
                "'2026-11-18 11:00+00', 'views', 3, 1, 0 "
                "FROM yt_weekly_lists WHERE week_start = '2026-11-02'",
            )
        )


def test_an_entry_goes_with_its_reading_and_the_list_stays(
    migrated: URL,
) -> None:
    other = "fakeWeek002"
    asyncio.run(
        run(
            migrated,
            f"""
            INSERT INTO yt_weekly_lists
                (week_start, route, computed_at, threshold, size,
                 comments_min, videos_scored, channels_above)
            VALUES ('2026-11-09', 'ru', now(), 2, 10, 10, 1, 1);
            INSERT INTO yt_raw_videos (video_id, payload, fetched_at, discovered_at, discovered_via)
            VALUES ('{other}', '{{}}', now(), now(), 'count');
            INSERT INTO yt_raw_video_stats (video_id, observed_at, views)
            VALUES ('{other}', '{READ_AT}', 1);
            INSERT INTO yt_weekly_entries
                (list_id, rank, channel_id, video_id, scored_at, metric,
                 value, expected, others)
            SELECT id, 1, '{CHANNEL}', '{other}', '{READ_AT}', 'views',
                   3, 1, 0
            FROM yt_weekly_lists WHERE week_start = '2026-11-09'
            """,
        )
    )

    asyncio.run(
        run(migrated, f"DELETE FROM yt_raw_videos WHERE video_id = '{other}'")
    )

    entries = asyncio.run(
        scalar(
            migrated,
            "SELECT count(*) FROM yt_weekly_entries e "
            "JOIN yt_weekly_lists l ON l.id = e.list_id "
            "WHERE l.week_start = '2026-11-09'",
        )
    )
    lists = asyncio.run(
        scalar(
            migrated,
            "SELECT count(*) FROM yt_weekly_lists "
            "WHERE week_start = '2026-11-09'",
        )
    )
    assert (entries, lists) == (0, 1)


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('yt_weekly_lists')"))
        is None
    )
    # What the revision reused is still there for the alerts.
    asyncio.run(run(migrated, "SELECT route FROM yt_alerts", role=BOT))

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(run(migrated, "SELECT * FROM yt_weekly_entries", role=BOT))
