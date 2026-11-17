"""Late readings change nothing Telegram's scoring produces.

The poll loop now keeps readings of posts past the tracking horizon, at
3 to 30 days of age (`add-late-readings`). Every fit and score finds a
reading's band by its age, and the bands end at the alerting window, so
none of them should see those readings — and the mature medians read
`tg_raw_messages`, not `tg_message_metrics`. This holds the claim to the
characterisation inventory of `test_telegram_scoring_record`: the same
refresh, replay and live pass, with and without late readings, agree.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from test_telegram_scoring_record import (
    FLOORS,
    NOW,
    PINNED,
    _differences,
    _record,
    _seed,
)

from itgraph.config import Settings, settings
from itgraph.db.session import Database

# Every late age, in days, that a recent post of the inventory has passed.
LATE_DAYS = (3, 4, 5, 7, 10, 14)

# What Telegram's scoring writes; emptied between the two records.
DERIVED = (
    "tg_alerts",
    "tg_channel_baselines",
    "tg_metric_baselines",
    "tg_curve_points",
    "tg_baseline_runs",
)


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PINNED:
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )
    for name, value in FLOORS.items():
        monkeypatch.setattr(settings, name, value)


async def _add_late_readings(database: Database) -> int:
    """A reading at every late age a post has passed, each far above the
    post's last: if any pass read one, it would move a number."""
    added = 0
    async with database.session() as session:
        for days in LATE_DAYS:
            result = await session.execute(
                text(
                    "INSERT INTO tg_message_metrics (channel_id, msg_id, "
                    "observed_at, views, forwards, reactions, comments) "
                    "SELECT m.channel_id, m.msg_id, "
                    "  (r.payload->>'date')::timestamptz "
                    "    + make_interval(days => :days), "
                    "  max(m.views) * 10, max(m.forwards) * 10 + 5, "
                    "  CASE WHEN bool_and(m.reactions IS NULL) THEN NULL "
                    "       ELSE jsonb_build_object('👍', 9999) END, "
                    "  max(m.comments) + 50 "
                    "FROM tg_message_metrics m "
                    "JOIN tg_raw_messages r "
                    "  ON r.channel_id = m.channel_id AND r.msg_id = m.msg_id "
                    "WHERE (r.payload->>'date')::timestamptz "
                    "      + make_interval(days => :days) <= :now "
                    "  AND m.observed_at - (r.payload->>'date')::timestamptz"
                    "      < interval '49 hours' "
                    "GROUP BY m.channel_id, m.msg_id, r.payload->>'date'"
                ),
                {"days": days, "now": NOW},
            )
            added += result.rowcount or 0
    return added


async def _forget_what_scoring_wrote(database: Database) -> None:
    async with database.session() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(DERIVED)} RESTART IDENTITY CASCADE")
        )


async def test_late_readings_change_no_score(database: Database) -> None:
    await _seed(database)
    without = await _record(database)

    await _forget_what_scoring_wrote(database)
    assert await _add_late_readings(database) > 100
    with_late = await _record(database)

    # The record is not empty: a refresh fitted, and the pass raised.
    assert with_late["channel_baselines"]
    assert with_late["live"]["raised"] > 0
    assert _differences(with_late, without) == []


async def test_late_readings_are_where_the_test_puts_them(
    database: Database,
) -> None:
    """Guard on the test itself: the readings land past the horizon."""
    await _seed(database)
    await _add_late_readings(database)

    async with database.session() as session:
        youngest = await session.scalar(
            text(
                "SELECT min(m.observed_at - (r.payload->>'date')::timestamptz)"
                " FROM tg_message_metrics m JOIN tg_raw_messages r "
                "  ON r.channel_id = m.channel_id AND r.msg_id = m.msg_id "
                "WHERE m.observed_at - (r.payload->>'date')::timestamptz "
                "      >= interval '49 hours'"
            )
        )
    assert youngest == timedelta(days=LATE_DAYS[0])
