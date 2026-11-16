"""Telegram's weekly list: the week, the run in force, the ranking.

Baselines are written by hand, so every score is arithmetic: a channel's
mature median of 1,000 views, a factor of 0.44 and half the curve at two
hours expect 220 views there, and a reading of k times that scores
ln k / 0.38.
"""

import json
import math
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from itgraph.config import Settings, settings
from itgraph.db.baselines import (
    count_in_scope,
    start_run,
    store_channel_medians,
    store_curves,
)
from itgraph.db.models import BaselineReference, ChannelKind, Metric
from itgraph.db.session import Database
from itgraph.scoring.curves import Curve
from itgraph.scoring.run import run_scoring
from itgraph.scoring.weekly import run_weekly
from itgraph.scoring.weeks import WeekRefused

WEEK = date(2026, 11, 16)
# Monday 16 November 00:00 Moscow time, and the list's moment: the Monday
# after, 10:00 Moscow time.
START = datetime(2026, 11, 15, 21, 0, tzinfo=UTC)
LISTED = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)
CHANNEL = 6000000001

CURVE = Curve(
    fractions={"15m": 0.17, "48m": 0.33, "105m": 0.50, "420m": 0.96},
    samples=dict.fromkeys(("15m", "48m", "105m", "420m"), 500),
)
COMMENTS_CURVE = Curve(
    fractions={"48m": 0.6, "105m": 1.0, "420m": 1.0},
    samples=dict.fromkeys(("48m", "105m", "420m"), 500),
)
SPREAD = 0.38
# Inside the band from 105 to 150 minutes, where half the curve is.
EARLY = timedelta(minutes=110)
LATE = timedelta(minutes=140)


def views_for(z: float, *, median: float = 1000.0) -> int:
    """The views at two hours that score ``z`` against a median."""
    return round(median * 0.44 * 0.50 * math.exp(z * SPREAD))


@pytest.fixture(autouse=True)
def shipped_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The code's own threshold, signals and sizes, not the host's."""
    for name in (
        "alert_spike_z",
        "alert_spike_metrics",
        "alert_spike_comments_min",
        "weekly_list_size",
        "watch_timezone",
        "scoring_burst_seconds",
    ):
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )


async def a_channel(database: Database, channel: int = CHANNEL) -> None:
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO channels (tg_id, username, title, "
                "discovered_via, status, kind) VALUES "
                "(:id, :name, 'Example', 'manual', 'seed', 'personal') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": channel, "name": f"fake_weekly_{channel}"},
        )


async def a_post(
    database: Database,
    published: datetime,
    *,
    msg_id: int = 100,
    channel: int = CHANNEL,
    album: int | None = None,
) -> None:
    await a_channel(database, channel)
    payload: dict[str, object] = {
        "_": "Message",
        "id": msg_id,
        "date": published.isoformat(),
        "message": "a post that did well",
    }
    if album is not None:
        payload["grouped_id"] = album
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                "VALUES (:c, :m, CAST(:p AS jsonb))"
            ),
            {"c": channel, "m": msg_id, "p": json.dumps(payload)},
        )


async def read(
    database: Database,
    at: datetime,
    *,
    msg_id: int = 100,
    channel: int = CHANNEL,
    views: int | None = None,
    forwards: int | None = None,
    comments: int | None = None,
) -> None:
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO message_metrics (channel_id, msg_id, "
                "observed_at, views, forwards, comments) "
                "VALUES (:c, :m, :at, :v, :f, :n)"
            ),
            {
                "c": channel,
                "m": msg_id,
                "at": at,
                "v": views,
                "f": forwards,
                "n": comments,
            },
        )


async def a_run(
    database: Database,
    completed_at: datetime,
    *,
    median: float = 1000.0,
    channels: tuple[int, ...] = (CHANNEL,),
    forwards: float | None = None,
    comments: float | None = None,
) -> int:
    """A completed baseline run covering these channels; its id."""
    for channel in channels:
        await a_channel(database, channel)
    async with database.session() as session:
        run = await start_run(
            session,
            mature_days=28,
            min_channel_posts=30,
            min_band_samples=20,
            channels_in_scope=await count_in_scope(session),
        )
        await store_channel_medians(
            session,
            run,
            Metric.VIEWS,
            {channel: (median, 40) for channel in channels},
            reference=BaselineReference.MATURE,
        )
        await store_curves(
            session,
            run,
            ChannelKind.PERSONAL,
            Metric.VIEWS,
            curve=CURVE,
            factor=0.44,
            spread=SPREAD,
            samples=500,
        )
        if forwards is not None:
            await store_channel_medians(
                session,
                run,
                Metric.FORWARDS,
                {channel: (forwards, 40) for channel in channels},
            )
            await store_curves(
                session,
                run,
                ChannelKind.PERSONAL,
                Metric.FORWARDS,
                curve=CURVE,
                factor=0.44,
                spread=SPREAD,
                samples=500,
            )
        if comments is not None:
            await store_channel_medians(
                session,
                run,
                Metric.COMMENTS,
                {channel: (comments, 20) for channel in channels},
                reference=BaselineReference.RECENT,
            )
            await store_curves(
                session,
                run,
                ChannelKind.PERSONAL,
                Metric.COMMENTS,
                curve=COMMENTS_CURVE,
                factor=1.0,
                spread=1.1,
                samples=500,
                dispersion=1.58,
            )
        run.completed_at = completed_at
        return run.id


async def lists(database: Database) -> int:
    async with database.session() as session:
        return int(
            await session.scalar(text("SELECT count(*) FROM weekly_lists"))
        )


WEDNESDAY = START + timedelta(days=2, hours=9)  # 18 Nov, 12:00 Moscow


# --- the week ------------------------------------------------------------


async def test_a_post_of_sunday_evening_is_in_the_week(
    database: Database,
) -> None:
    sunday = datetime(2026, 11, 22, 20, 0, tzinfo=UTC)  # 23:00 Moscow
    await a_run(database, START)
    await a_post(database, sunday)
    await read(database, sunday + EARLY, views=views_for(5.0))

    summary = await run_weekly(database, now=LISTED)

    assert summary.week_start == WEEK
    assert [(entry.channel_id, entry.msg_id) for entry in summary.entries] == [
        (CHANNEL, 100)
    ]


async def test_a_post_of_monday_after_midnight_is_in_no_list(
    database: Database,
) -> None:
    monday = datetime(2026, 11, 22, 21, 30, tzinfo=UTC)  # 23 Nov, 00:30
    await a_run(database, START)
    await a_post(database, monday)
    await read(database, monday + EARLY, views=views_for(5.0))

    summary = await run_weekly(database, now=LISTED)

    assert summary.entries == []
    assert summary.posts == 0


async def test_a_run_late_by_days_lists_the_week_that_ended(
    database: Database,
) -> None:
    summary = await run_weekly(
        database, now=LISTED + timedelta(days=2), preview=True
    )
    assert summary.week_start == WEEK


@pytest.mark.parametrize(
    "week", [date(2026, 11, 23), date(2026, 11, 17)], ids=["current", "tue"]
)
async def test_a_week_that_cannot_be_listed_is_refused(
    database: Database, week: date
) -> None:
    with pytest.raises(WeekRefused):
        await run_weekly(database, week=week, now=LISTED)
    assert await lists(database) == 0


async def test_a_week_is_computed_once(database: Database) -> None:
    first = await run_weekly(database, now=LISTED)
    again = await run_weekly(database, now=LISTED)

    assert first.stored and not first.already
    assert again.already and not again.stored
    assert "already has its list" in again.line()
    assert await lists(database) == 1


# --- scored as the live pass scored it -----------------------------------


async def test_each_reading_against_the_run_in_force_when_taken(
    database: Database,
) -> None:
    """A run of median 1,000 until a reading at 110 minutes, then one of
    100: the same views score 1.8 against the first and 7.9 against the
    second, so the post first crosses at the later reading."""
    first = await a_run(database, START, median=1000.0)
    await a_post(database, WEDNESDAY)
    second = await a_run(
        database, WEDNESDAY + timedelta(minutes=120), median=100.0
    )
    views = views_for(1.8)
    await read(database, WEDNESDAY + EARLY, views=views)
    await read(database, WEDNESDAY + LATE, views=views)

    summary = await run_weekly(database, now=LISTED, preview=True)

    [standing] = summary.standings
    assert standing.best.crossed_at == WEDNESDAY + LATE
    assert standing.best.scored_at == WEDNESDAY + LATE
    assert standing.best.score.z == pytest.approx(
        math.log(views / (100 * 0.44 * 0.5)) / SPREAD
    )
    # The summary names the runs used, oldest first.
    assert summary.runs == (first, second)
    assert f"against 2 baseline run(s) (#{first}, #{second})" in summary.line()


async def test_readings_before_the_first_run_are_counted_not_scored(
    database: Database,
) -> None:
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(5.0))
    await a_run(database, WEDNESDAY + timedelta(minutes=120))
    await read(database, WEDNESDAY + LATE, views=views_for(5.0))

    summary = await run_weekly(database, now=LISTED, preview=True)

    assert summary.unscored_readings == 1
    [standing] = summary.standings
    assert standing.best.scored_at == WEDNESDAY + LATE
    assert "1 reading(s) taken before any baseline run" in summary.line()


async def test_the_best_reading_and_signal_is_the_post_s_score(
    database: Database,
) -> None:
    await a_run(database, START, forwards=10.0)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(3.9), forwards=1)
    await read(database, WEDNESDAY + LATE, views=views_for(3.9), forwards=12)

    summary = await run_weekly(database, now=LISTED, preview=True)

    [entry] = summary.entries
    assert entry.metric is Metric.FORWARDS
    assert entry.value == pytest.approx(math.log(12 / 2.2) / SPREAD)
    assert (entry.observed, entry.expected) == (12, pytest.approx(2.2))


async def test_too_few_comments_are_not_the_post_s_score(
    database: Database,
) -> None:
    """Five comments where 0.2 are usual is far into the tail, and under
    the minimum of ten."""
    await a_run(database, START, comments=0.2)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(1.0), comments=5)

    summary = await run_weekly(database, now=LISTED, preview=True)

    assert summary.entries == []
    assert summary.posts_scored == 1
    assert summary.posts_above == 0


async def test_an_album_is_scored_once_as_its_first_message(
    database: Database,
) -> None:
    await a_run(database, START)
    for part in (100, 101, 102):
        await a_post(database, WEDNESDAY, msg_id=part, album=77)
        await read(
            database, WEDNESDAY + EARLY, msg_id=part, views=views_for(5.0)
        )

    summary = await run_weekly(database, now=LISTED, preview=True)

    assert [entry.msg_id for entry in summary.entries] == [100]
    assert summary.posts == 1


async def test_a_late_reading_scores_nothing(database: Database) -> None:
    """Read at four days by the poll loop's late readings: no band."""
    await a_run(database, START)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + timedelta(days=4), views=1_000_000)

    summary = await run_weekly(database, now=LISTED, preview=True)

    assert summary.entries == []
    assert summary.posts_scored == 0


async def test_the_score_is_the_live_pass_s(database: Database) -> None:
    """The same reading, against the same run, scores the same."""
    await a_run(database, START)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(4.6))

    listed = await run_weekly(database, now=LISTED, preview=True)
    live = await run_scoring(
        database, now=WEDNESDAY + EARLY + timedelta(minutes=1), dry_run=True
    )

    [entry] = listed.entries
    [spike] = live.spikes
    assert entry.value == pytest.approx(spike.score.z)
    assert entry.metric.value == spike.score.metric


# --- one entry per channel, at the alerts' threshold ----------------------


async def test_one_entry_per_channel_with_its_others(
    database: Database,
) -> None:
    await a_run(database, START)
    for msg_id, z in ((100, 4.5), (101, 4.0), (102, 3.8)):
        published = WEDNESDAY + timedelta(hours=msg_id - 100)
        await a_post(database, published, msg_id=msg_id)
        await read(
            database, published + EARLY, msg_id=msg_id, views=views_for(z)
        )

    summary = await run_weekly(database, now=LISTED)

    [entry] = summary.entries
    assert (entry.msg_id, entry.others) == (100, 2)
    assert entry.value == pytest.approx(4.5, abs=0.01)


async def test_the_size_holds_the_best_channels_in_order(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "weekly_list_size", 2)
    channels = (CHANNEL, CHANNEL + 1, CHANNEL + 2)
    await a_run(database, START, channels=channels)
    for channel, z in zip(channels, (4.0, 6.0, 5.0), strict=True):
        await a_post(database, WEDNESDAY, channel=channel)
        await read(
            database, WEDNESDAY + EARLY, channel=channel, views=views_for(z)
        )

    summary = await run_weekly(database, now=LISTED)

    assert [entry.channel_id for entry in summary.entries] == [
        CHANNEL + 1,
        CHANNEL + 2,
    ]
    assert [entry.rank for entry in summary.entries] == [1, 2]
    assert summary.channels_above == 3


async def test_the_threshold_moves_with_the_alerts(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "alert_spike_z", 4.0)
    await a_run(database, START)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(3.8))

    summary = await run_weekly(database, now=LISTED)

    assert summary.entries == []
    assert summary.threshold == 4.0


async def test_an_empty_week_is_stored(database: Database) -> None:
    summary = await run_weekly(database, now=LISTED)

    assert summary.stored
    assert summary.entries == []
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT posts_scored, channels_above, threshold "
                    "FROM weekly_lists"
                )
            )
        ).one()
    assert tuple(row) == (0, 0, 3.7)


async def test_a_stale_copy_lists_nothing_and_says_nothing_was_scored(
    database: Database,
) -> None:
    """A restored copy whose newest reading is sixty days old."""
    old = START - timedelta(days=60)
    await a_run(database, old - timedelta(days=1))
    await a_post(database, old)
    await read(database, old + EARLY, views=views_for(6.0))

    summary = await run_weekly(database, now=LISTED)

    assert summary.stored
    assert summary.entries == []
    assert (summary.posts, summary.posts_scored) == (0, 0)


async def everything_else(database: Database) -> tuple[int, ...]:
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM channels), "
                    "(SELECT count(*) FROM raw_messages), "
                    "(SELECT count(*) FROM message_metrics), "
                    "(SELECT count(*) FROM baseline_runs), "
                    "(SELECT count(*) FROM channel_baselines), "
                    "(SELECT count(*) FROM alerts), "
                    "(SELECT count(*) FROM yt_weekly_lists), "
                    "(SELECT count(*) FROM poll_state)"
                )
            )
        ).one()
    return tuple(row)


async def test_the_pass_changes_nothing_but_the_list(
    database: Database,
) -> None:
    await a_run(database, START)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(5.0))
    before = await everything_else(database)

    await run_weekly(database, now=LISTED, preview=True)
    assert await lists(database) == 0
    await run_weekly(database, now=LISTED)

    assert await everything_else(database) == before
    assert await lists(database) == 1


async def test_a_preview_writes_nothing(database: Database) -> None:
    await a_run(database, START)
    await a_post(database, WEDNESDAY)
    await read(database, WEDNESDAY + EARLY, views=views_for(5.0))

    summary = await run_weekly(database, now=LISTED, preview=True)

    assert not summary.stored
    assert await lists(database) == 0
    # The list's entry, then its second wind section, in the shadow.
    line, *section = summary.preview()
    assert f"{CHANNEL} 100" in line
    assert section == ["  second wind (in the shadow):", "    nothing sped up"]
    assert "preview, nothing written" in summary.line()
