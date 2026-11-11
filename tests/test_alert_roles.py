"""The two alert revisions, on a database the migrations built.

A database of its own, because what is under test is what the migrations
grant, and the suite's shared schema comes from `create_all`, which grants
nothing. `SET LOCAL ROLE` rather than logging in, as `test_metrics_role`
does, for the same reason: giving a role a password would change the
cluster outside any `_test` database.
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
BEFORE = "d629dc2e11d0"

CHANNEL = "UCfakeChannel00000000001"
VIDEO = "fakeVid0001"

SEED = f"""
INSERT INTO yt_channels (channel_id, status, kind, lang, discovered_via)
VALUES ('{CHANNEL}', 'seed', 'personal', 'en', 'manual');
INSERT INTO yt_raw_videos (video_id, payload, fetched_at, discovered_at, discovered_via)
VALUES ('{VIDEO}', '{{"snippet": {{"channelId": "{CHANNEL}"}}}}', now(), now(), 'count');
INSERT INTO yt_raw_video_stats (video_id, observed_at, views)
VALUES ('{VIDEO}', '2026-11-11 10:00+00', 100);
INSERT INTO yt_alerts (kind, video_id, scored_at, band, value)
VALUES ('views_spike', '{VIDEO}', '2026-11-11 10:00+00', 1, 4.2);
"""


@pytest.fixture(scope="module")
def migrated() -> Iterator[URL]:
    base = the_test_database_url()
    assert base.database is not None
    name = base.database.removesuffix("_test") + "_alert_roles_test"
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


def test_the_verdict_table_and_its_type_are_gone(migrated: URL) -> None:
    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('alert_feedback')"))
        is None
    )
    assert (
        asyncio.run(
            scalar(
                migrated,
                "SELECT count(*) FROM pg_type WHERE typname = 'alert_verdict'",
            )
        )
        == 0
    )
    asyncio.run(run(migrated, "SELECT held_at FROM alerts"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE yt_alerts SET held_at = now()",
        "UPDATE yt_alerts SET delivered_at = now(), delivery = 'excluded'",
        "UPDATE yt_alerts SET route = 'ru', attempts = attempts + 1, last_error = 'x'",
        "SELECT payload FROM yt_raw_videos",
        "SELECT payload FROM yt_raw_channels",
        "SELECT views FROM yt_raw_video_stats",
        "SELECT lang FROM yt_channels",
        "SELECT finished_at FROM yt_watch_passes",
        "SELECT a.id FROM yt_alerts a FOR UPDATE SKIP LOCKED",
    ],
)
def test_the_bot_delivers_youtube_alerts(
    migrated: URL, statement: str
) -> None:
    asyncio.run(run(migrated, statement, role=BOT))


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO yt_alerts (kind, video_id, scored_at, band, value) "
            f"VALUES ('like_spike', '{VIDEO}', '2026-11-11 10:00+00', 1, 9)"
        ),
        "UPDATE yt_alerts SET value = 99",
        "UPDATE yt_alerts SET kind = 'like_spike'",
        "UPDATE yt_alerts SET video_id = 'fakeVid0002'",
        "UPDATE yt_alerts SET scored_at = now()",
        "UPDATE yt_alerts SET folded_into = id",
        "DELETE FROM yt_alerts",
        "UPDATE yt_raw_videos SET payload = '{}'",
        "DELETE FROM yt_raw_video_stats",
        "UPDATE yt_channels SET lang = 'ru'",
    ],
)
def test_the_bot_cannot_raise_or_rewrite_a_youtube_alert(
    migrated: URL, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(run(migrated, statement, role=BOT))


def test_the_exporter_reads_when_and_whether(migrated: URL) -> None:
    asyncio.run(
        run(
            migrated,
            "SELECT count(*), min(raised_at) FROM yt_alerts "
            "WHERE delivered_at IS NULL AND folded_into IS NULL "
            "AND held_at IS NULL AND attempts >= 0 AND delivery IS NULL",
            role=METRICS,
        )
    )


@pytest.mark.parametrize(
    "column", ["video_id", "scored_at", "value", "expected", "route", "kind"]
)
def test_the_exporter_cannot_read_what_an_alert_is_about(
    migrated: URL, column: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(
            run(migrated, f"SELECT {column} FROM yt_alerts", role=METRICS)
        )


def test_an_alert_goes_with_its_reading(migrated: URL) -> None:
    """Expiry and a video YouTube stopped returning both delete the
    payload; the reading goes with it, and the alert with the reading."""
    other = "fakeVid0002"
    asyncio.run(
        run(
            migrated,
            f"""
            INSERT INTO yt_raw_videos (video_id, payload, fetched_at, discovered_at, discovered_via)
            VALUES ('{other}', '{{}}', now(), now(), 'count');
            INSERT INTO yt_raw_video_stats (video_id, observed_at, views)
            VALUES ('{other}', '2026-11-11 10:00+00', 1);
            INSERT INTO yt_alerts (kind, video_id, scored_at, band, value)
            VALUES ('views_spike', '{other}', '2026-11-11 10:00+00', 1, 4.2)
            """,
        )
    )

    asyncio.run(
        run(migrated, f"DELETE FROM yt_raw_videos WHERE video_id = '{other}'")
    )

    left = asyncio.run(
        scalar(
            migrated,
            f"SELECT count(*) FROM yt_alerts WHERE video_id = '{other}'",
        )
    )
    assert left == 0


def test_the_revisions_go_down_and_up_again(migrated: URL) -> None:
    down = alembic(migrated, "downgrade", BEFORE)
    assert down.returncode == 0, down.stdout + down.stderr

    assert (
        asyncio.run(scalar(migrated, "SELECT to_regclass('yt_alerts')"))
        is None
    )
    # The verdict table is back, empty, and the bot may write it again.
    asyncio.run(
        run(
            migrated,
            "INSERT INTO alert_feedback (alert_id, verdict) "
            "SELECT id, 'useful' FROM alerts LIMIT 1",
            role=BOT,
        )
    )
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(
            run(migrated, "SELECT payload FROM yt_raw_videos", role=BOT)
        )

    up = alembic(migrated, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    asyncio.run(run(migrated, "SELECT payload FROM yt_raw_videos", role=BOT))


def test_one_alert_per_kind_video_and_band(migrated: URL) -> None:
    """Raising again writes nothing: the schema, not the pass, says so.

    On a band of its own, so it holds whatever ran before it here.
    """
    raise_once = (
        "INSERT INTO yt_alerts (kind, video_id, scored_at, band, value) "
        f"VALUES ('comment_spike', '{VIDEO}', '2026-11-11 10:00+00', 7, 7)"
    )
    asyncio.run(run(migrated, raise_once))

    with pytest.raises(DBAPIError, match="uq_yt_alerts_video_band"):
        asyncio.run(run(migrated, raise_once))
    count = asyncio.run(
        scalar(migrated, "SELECT count(*) FROM yt_alerts WHERE band = 7")
    )
    assert count == 1


def test_an_alert_names_a_reading_that_exists(migrated: URL) -> None:
    with pytest.raises(
        DBAPIError, match="fk_yt_alerts_video_id_yt_raw_video_stats"
    ):
        asyncio.run(
            run(
                migrated,
                "INSERT INTO yt_alerts (kind, video_id, scored_at, band, value) "
                f"VALUES ('like_spike', '{VIDEO}', '2026-11-11 11:00+00', 1, 7)",
            )
        )
