"""The second wind revision, on a database the migrations built.

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
BEFORE = "1156f1425a66"

CHANNEL = 7000000001
YT_CHANNEL = "UCfakeSecondWindChan0001"
VIDEO = "fakeWind001"
P, A, Z = (
    "2026-11-09 02:00+00",
    "2026-11-16 02:00+00",
    "2026-11-22 02:00+00",
)

SEED = f"""
INSERT INTO channels (tg_id, username, title, discovered_via, status)
VALUES ({CHANNEL}, 'fake_wind_channel', 'Fake', 'manual', 'seed');
INSERT INTO raw_messages (channel_id, msg_id, payload)
VALUES ({CHANNEL}, 10, '{{"_": "Message", "id": 10}}');
INSERT INTO message_metrics (channel_id, msg_id, observed_at, views)
VALUES ({CHANNEL}, 10, '{P}', 100), ({CHANNEL}, 10, '{A}', 200),
       ({CHANNEL}, 10, '{Z}', 3000);
INSERT INTO weekly_lists
    (week_start, computed_at, threshold, size, comments_min,
     posts_scored, channels_above)
VALUES ('2026-11-16', '2026-11-23 07:00+00', 3.7, 10, 10, 50, 0);
INSERT INTO weekly_revivals
    (list_id, rank, channel_id, msg_id, before_at, start_at, end_at,
     previous_pace, pace, norm, others)
SELECT id, 1, {CHANNEL}, 10, '{P}', '{A}', '{Z}', 14, 466, 1000, 0
FROM weekly_lists WHERE week_start = '2026-11-16';

INSERT INTO yt_channels (channel_id, status, kind, lang, discovered_via)
VALUES ('{YT_CHANNEL}', 'seed', 'personal', 'en', 'manual');
INSERT INTO yt_raw_videos (video_id, payload, fetched_at, discovered_at, discovered_via)
VALUES ('{VIDEO}', '{{"snippet": {{"channelId": "{YT_CHANNEL}"}}}}', now(), now(), 'backfill');
INSERT INTO yt_raw_video_stats (video_id, observed_at, views)
VALUES ('{VIDEO}', '{P}', 1000), ('{VIDEO}', '{A}', 1350), ('{VIDEO}', '{Z}', 4150);
INSERT INTO yt_weekly_lists
    (week_start, route, computed_at, threshold, size, comments_min,
     videos_scored, channels_above)
VALUES ('2026-11-16', 'en', '2026-11-23 07:00+00', 2.0, 10, 10, 40, 0);
INSERT INTO yt_weekly_revivals
    (list_id, rank, channel_id, video_id, before_at, start_at, end_at,
     acceleration, share, others)
SELECT id, 1, '{YT_CHANNEL}', '{VIDEO}', '{P}', '{A}', '{Z}', 8, 1.4, 0
FROM yt_weekly_lists WHERE week_start = '2026-11-16';
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_second_wind_roles_test"
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
        "SELECT * FROM weekly_revivals",
        "SELECT * FROM yt_weekly_revivals",
        "UPDATE weekly_revivals SET delivered_at = now()",
        "UPDATE yt_weekly_revivals SET delivered_at = now()",
        "SELECT second_wind_shown FROM weekly_lists",
        "SELECT second_wind_shown FROM yt_weekly_lists",
    ],
)
def test_the_bot_reads_and_records_a_revival_s_delivery(
    migrated: URL, statement: str
) -> None:
    asyncio.run(run(migrated, statement, role=BOT))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO weekly_revivals (list_id, rank, channel_id, msg_id, "
            "before_at, start_at, end_at, pace, norm, others) "
            f"SELECT id, 2, {CHANNEL}, 10, '{P}', '{A}', '{Z}', 1, 1, 0 "
            "FROM weekly_lists"
        ),
        "UPDATE weekly_revivals SET rank = 3",
        "UPDATE weekly_revivals SET pace = 1",
        "UPDATE weekly_revivals SET norm = 1",
        "UPDATE weekly_revivals SET msg_id = 11",
        "UPDATE yt_weekly_revivals SET share = 9",
        "UPDATE yt_weekly_revivals SET acceleration = 9",
        "UPDATE yt_weekly_revivals SET start_at = end_at",
        "UPDATE weekly_lists SET second_wind_shown = true",
        "UPDATE yt_weekly_lists SET second_wind_shown = true",
        "DELETE FROM weekly_revivals",
        "DELETE FROM yt_weekly_revivals",
    ],
)
def test_the_bot_cannot_write_a_revival(migrated: URL, statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=BOT))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM weekly_revivals",
        "SELECT delivered_at FROM weekly_revivals",
        "SELECT count(*) FROM yt_weekly_revivals",
        "SELECT delivered_at FROM yt_weekly_revivals",
        "SELECT second_wind_shown FROM weekly_lists",
    ],
)
def test_the_exporter_reads_nothing_of_a_revival(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=METRICS))


def test_a_revival_goes_with_the_reading_that_ends_its_week(
    migrated: URL,
) -> None:
    asyncio.run(
        run(
            migrated,
            "DELETE FROM message_metrics "
            f"WHERE channel_id = {CHANNEL} AND observed_at = '{Z}'",
        )
    )
    revivals = asyncio.run(
        scalar(migrated, "SELECT count(*) FROM weekly_revivals")
    )
    lists = asyncio.run(scalar(migrated, "SELECT count(*) FROM weekly_lists"))
    assert (revivals, lists) == (0, 1)


def test_a_youtube_revival_goes_with_its_start_reading(migrated: URL) -> None:
    asyncio.run(
        run(
            migrated,
            "DELETE FROM yt_raw_video_stats "
            f"WHERE video_id = '{VIDEO}' AND observed_at = '{A}'",
        )
    )
    revivals = asyncio.run(
        scalar(migrated, "SELECT count(*) FROM yt_weekly_revivals")
    )
    lists = asyncio.run(
        scalar(migrated, "SELECT count(*) FROM yt_weekly_lists")
    )
    assert (revivals, lists) == (0, 1)


def test_the_revision_goes_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('weekly_revivals')"))
        is None
    )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(run(migrated, "SELECT * FROM yt_weekly_revivals", role=BOT))
