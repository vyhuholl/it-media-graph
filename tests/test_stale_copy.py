"""A copy that receives no new data: the passes stay quiet, however old it is.

A test environment holds a restored copy and no collector, so its newest row
only ever gets older, and a pass that failed on that would raise an alert in the
test group every fifteen minutes between restores.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from itgraph.alerts.run import run_cascades
from itgraph.db.session import Database
from itgraph.derive.edges import derive_graph
from itgraph.scoring.refresh import refresh_baselines
from itgraph.scoring.run import run_scoring

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=60)


async def seed_old_rows(database: Database) -> None:
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO channels (tg_id, username, status, discovered_via, "
                "kind) VALUES (1, 'fake_old_channel', 'seed', 'manual', 'media')"
            )
        )
        for message in range(1, 30):
            await session.execute(
                text(
                    "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                    "VALUES (1, :id, cast(:payload as jsonb))"
                ),
                {
                    "id": message,
                    "payload": json.dumps(
                        {
                            "_": "Message",
                            "id": message,
                            "date": OLD.isoformat(),
                            "views": 10,
                        }
                    ),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO message_metrics (channel_id, msg_id, "
                    "observed_at, views) VALUES (1, :id, :at, 10)"
                ),
                {"id": message, "at": OLD + timedelta(hours=1)},
            )


async def test_every_pass_ends_normally_on_a_copy_60_days_old(
    database: Database,
) -> None:
    await seed_old_rows(database)

    await derive_graph(database)
    await run_cascades(database, now=NOW)
    await refresh_baselines(database, now=NOW)
    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 0


async def test_every_pass_ends_normally_on_an_empty_database(
    database: Database,
) -> None:
    await derive_graph(database)
    await run_cascades(database, now=NOW)
    await refresh_baselines(database, now=NOW)
    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 0


async def test_youtube_s_passes_end_normally_on_a_copy_60_days_old(
    database: Database,
) -> None:
    from ytscoring import a_channel

    from itgraph.scoring.yt_refresh import refresh_yt_baselines
    from itgraph.scoring.yt_run import run_yt_scoring

    await a_channel(database, 1, now=OLD)

    refreshed = await refresh_yt_baselines(database, now=NOW)
    scored = await run_yt_scoring(database, now=NOW)

    assert refreshed.run_id
    assert scored.raised == 0
    async with database.session() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM yt_alerts")) == 0
        )
