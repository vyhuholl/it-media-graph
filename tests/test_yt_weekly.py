"""The weekly list's pass, against a real Postgres.

The synthetic world of the scoring tests (`ytscoring`): channels with a
fortnight of videos before the week and their baselines refreshed just
before it starts, then the week's videos each test is about. The week is
Monday 16 November 2026 in Moscow, computed at 10:00 on the Monday after.
"""

from datetime import UTC, date, datetime, timedelta
from functools import partial

import pytest
import ytscoring
from sqlalchemy import text
from ytfakes import channel_id, video_id
from ytscoring import Video, grown, seed_videos

from itgraph.config import Settings, settings
from itgraph.db.models import YtAlertRoute, YtMetric
from itgraph.db.session import Database
from itgraph.scoring.score import Best, Score
from itgraph.scoring.yt_refresh import refresh_yt_baselines
from itgraph.scoring.yt_run import run_yt_scoring
from itgraph.scoring.yt_weekly import (
    WeeklySummary,
    WeekRefused,
    check_week,
    last_ended_week,
    rank_week,
    run_weekly,
)

ZONE = "Europe/Moscow"
WEEK = date(2026, 11, 16)
# Monday 00:00 in Moscow, and the next one.
START = datetime(2026, 11, 15, 21, 0, tzinfo=UTC)
END = START + timedelta(days=7)
# Monday 10:00 in Moscow.
NOW = END + timedelta(hours=10)
# The baselines, refreshed an hour before the week begins.
BEFORE = START - timedelta(hours=1)

a_channel = partial(ytscoring.a_channel, now=BEFORE)

FLOORS = {
    "youtube_baseline_min_band_samples": 3,
    "youtube_baseline_min_band_spread_posts": 5,
    "youtube_baseline_min_channel_videos": 3,
    "youtube_baseline_recent_videos": 5,
    "youtube_baseline_recent_min_videos": 3,
}
PINNED = (
    "youtube_alert_spike_metrics",
    "youtube_alert_spike_comments_min",
    "youtube_scoring_short_max_seconds",
    "youtube_baseline_window_days",
    "youtube_baseline_mature_days",
    "youtube_baseline_mature_max_days",
    "youtube_baseline_recent_max_days",
    "youtube_alert_timezone",
    "youtube_weekly_list_z",
    "youtube_weekly_list_size",
)


@pytest.fixture(autouse=True)
def shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The code's defaults, not the host's, and floors a small world fits."""
    for name in PINNED:
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )
    for name, value in FLOORS.items():
        monkeypatch.setattr(settings, name, value)


async def world(database: Database, *, lang: str = "en") -> None:
    """Three channels of a thousand views a day, refreshed before the week."""
    for channel in (1, 2, 3):
        await a_channel(database, channel, lang=lang)
    await refresh_yt_baselines(database, now=BEFORE)


def published(
    number: int,
    channel: int,
    at: datetime,
    *,
    boost: float = 1.0,
    duration: str = "PT12M30S",
    broadcast: bool = False,
    until: datetime = NOW,
) -> Video:
    """A video published at a moment, read along the shape until ``until``."""
    return Video(
        number=number,
        channel=channel,
        published=at,
        duration=duration,
        broadcast=broadcast,
        readings=grown(
            at,
            until=until,
            views=1000,
            likes=50,
            comments=5,
            boost=boost,
        ),
    )


def wednesday(hours: float = 0) -> datetime:
    return START + timedelta(days=2, hours=hours)


async def lists(database: Database) -> list[tuple[str, int, int]]:
    """(route, entries, channels above) of every stored list."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT l.route::text, count(e.rank), l.channels_above "
                "FROM yt_weekly_lists l "
                "LEFT JOIN yt_weekly_entries e ON e.list_id = l.id "
                "GROUP BY l.id ORDER BY l.route"
            )
        )
    return [(route, entries, above) for route, entries, above in rows]


def listed(summary: WeeklySummary, route: YtAlertRoute) -> list[str]:
    """The video ids a route's list holds, in rank order."""
    (found,) = [found for found in summary.routes if found.route is route]
    return [entry.video_id for entry in found.entries]


# --- the week ----------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "week"),
    [
        (NOW, WEEK),
        # Monday 00:30 in Moscow: the week ended half an hour ago.
        (END + timedelta(minutes=30), WEEK),
        # Sunday 23:59 in Moscow: the week has not ended.
        (END - timedelta(minutes=1), WEEK - timedelta(days=7)),
        # Wednesday after: still the week that ended on Monday.
        (END + timedelta(days=2), WEEK),
    ],
)
def test_the_last_week_that_has_ended(moment: datetime, week: date) -> None:
    assert last_ended_week(moment, zone=ZONE) == week


@pytest.mark.parametrize(
    "week", [WEEK + timedelta(days=7), WEEK + timedelta(days=1)]
)
def test_a_week_not_ended_or_not_a_monday_is_refused(week: date) -> None:
    with pytest.raises(WeekRefused):
        check_week(week, moment=NOW, zone=ZONE)


async def test_a_refused_week_reads_nothing(database: Database) -> None:
    with pytest.raises(WeekRefused, match="not a Monday"):
        await run_weekly(database, week=WEEK + timedelta(days=2), now=NOW)
    assert await lists(database) == []


async def test_sunday_evening_is_in_the_week_and_monday_is_not(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(
        database,
        [
            # Sunday 23:00 in Moscow, and Monday 00:30.
            published(900, 1, END - timedelta(hours=1), boost=6),
            published(901, 2, END + timedelta(minutes=30), boost=6),
        ],
    )

    summary = await run_weekly(database, now=NOW, preview=True)

    assert listed(summary, YtAlertRoute.EN) == [video_id(900)]


async def test_a_week_is_computed_once(database: Database) -> None:
    await world(database)
    await seed_videos(database, [published(900, 1, wednesday(), boost=5)])

    first = await run_weekly(database, now=NOW)
    again = await run_weekly(database, now=NOW + timedelta(days=1))

    assert first.stored
    assert again.already and not again.stored
    assert "already has its lists" in again.line()
    assert await lists(database) == [("en", 1, 1), ("ru", 0, 0)]


async def test_a_preview_writes_nothing(database: Database) -> None:
    await world(database)
    await seed_videos(database, [published(900, 1, wednesday(), boost=5)])

    summary = await run_weekly(database, now=NOW, preview=True)

    assert not summary.stored
    assert listed(summary, YtAlertRoute.EN) == [video_id(900)]
    assert await lists(database) == []
    preview = "\n".join(summary.preview())
    assert video_id(900) in preview and channel_id(1) in preview


# --- scored as the live pass scored it --------------------------------


async def test_each_reading_is_scored_against_the_run_in_force(
    database: Database,
) -> None:
    """The live pass sees a video against the run it has; a run completed
    later, for which the channel's videos grew tenfold, judges only the
    readings taken after it."""
    await world(database)
    at = wednesday()
    await seed_videos(database, [published(900, 1, at, boost=4)])

    # The live pass, past the 36-hour reading and before the next run.
    live_at = at + timedelta(hours=37)
    live = await run_yt_scoring(database, now=live_at, threshold=-1000.0)
    (spike,) = [s for s in live.spikes if s.post_key[1] == video_id(900)]

    # The channel's newest videos before the week ten times as watched, and
    # a run that sees them.
    later = at + timedelta(hours=40)
    await seed_videos(
        database,
        [
            Video(
                number=950 + index,
                channel=1,
                published=START - timedelta(days=2, hours=index),
                readings=grown(
                    START - timedelta(days=2, hours=index),
                    until=later,
                    views=10_000,
                    likes=500,
                    comments=50,
                ),
            )
            for index in range(5)
        ],
    )
    await refresh_yt_baselines(database, now=later)

    summary = await run_weekly(database, now=NOW, preview=True)
    (route,) = [r for r in summary.routes if r.route is YtAlertRoute.EN]
    (standing,) = [s for s in route.standings if s.video_id == video_id(900)]

    assert summary.runs == 2
    assert standing.best.scored_at < later
    assert standing.best.score.z == pytest.approx(spike.score.z)
    assert standing.best.score.metric == spike.score.metric

    # Against the newest run alone, the same video would have scored far
    # lower: the run in force is what made the difference.
    newest = await run_yt_scoring(database, now=NOW, threshold=-1000.0)
    (today,) = [s for s in newest.spikes if s.post_key[1] == video_id(900)]
    assert today.score.z < spike.score.z - 1


async def test_readings_before_any_run_are_not_scored(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    await seed_videos(database, [published(900, 1, wednesday(), boost=5)])

    summary = await run_weekly(database, now=NOW)

    assert summary.runs == 0
    assert summary.unscored_readings == len(
        grown(wednesday(), until=NOW, views=1)
    )
    assert "before any baseline run" in summary.line()
    assert await lists(database) == [("en", 0, 0), ("ru", 0, 0)]


async def test_a_broadcast_is_counted_and_not_listed(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(
        database, [published(900, 1, wednesday(), boost=6, broadcast=True)]
    )

    summary = await run_weekly(database, now=NOW, preview=True)

    assert listed(summary, YtAlertRoute.EN) == []
    assert summary.broadcasts == 1


async def test_too_few_comments_are_not_a_video_s_score(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await world(database)
    await seed_videos(database, [published(900, 1, wednesday(), boost=5)])
    monkeypatch.setattr(settings, "youtube_alert_spike_comments_min", 10**9)

    summary = await run_weekly(database, now=NOW, preview=True)

    for route in summary.routes:
        for entry in route.entries:
            assert entry.metric is not YtMetric.COMMENTS


# --- one entry per channel, above a floor ----------------------------


async def test_a_channel_is_listed_once_by_its_best_video(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(
        database,
        [
            published(900, 1, wednesday(), boost=3),
            published(901, 1, wednesday(5), boost=8),
            published(902, 1, wednesday(9), boost=4),
            published(903, 2, wednesday(), boost=1),
        ],
    )

    summary = await run_weekly(database, now=NOW)

    (route,) = [r for r in summary.routes if r.route is YtAlertRoute.EN]
    (entry,) = route.entries
    assert entry.video_id == video_id(901)
    assert entry.channel_id == channel_id(1)
    assert entry.others == 2
    assert entry.expected > 0
    assert route.videos_above == 3


async def test_the_list_holds_its_size_best_first(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await world(database)
    await seed_videos(
        database,
        [
            published(900, 1, wednesday(), boost=3),
            published(901, 2, wednesday(), boost=8),
            published(902, 3, wednesday(), boost=5),
        ],
    )
    monkeypatch.setattr(settings, "youtube_weekly_list_size", 2)

    summary = await run_weekly(database, now=NOW)

    assert listed(summary, YtAlertRoute.EN) == [video_id(901), video_id(902)]
    assert await lists(database) == [("en", 2, 3), ("ru", 0, 0)]


async def test_a_short_competes_with_long_form(database: Database) -> None:
    await world(database)
    for channel in (4, 5, 6):
        await a_channel(database, channel, duration="PT45S")
    await refresh_yt_baselines(database, now=BEFORE)
    await seed_videos(
        database,
        [
            published(900, 4, wednesday(), boost=8, duration="PT45S"),
            published(901, 1, wednesday(), boost=4),
        ],
    )

    summary = await run_weekly(database, now=NOW, preview=True)

    assert listed(summary, YtAlertRoute.EN) == [video_id(900), video_id(901)]


async def test_each_language_has_its_list_by_today_s_label(
    database: Database,
) -> None:
    await world(database)
    await a_channel(database, 4, lang="ru")
    await a_channel(database, 5, lang="ru")
    await a_channel(database, 6, lang="ru")
    await refresh_yt_baselines(database, now=BEFORE)
    await seed_videos(
        database,
        [
            published(900, 1, wednesday(), boost=5),
            published(901, 4, wednesday(), boost=5),
            published(902, 5, wednesday(), boost=5),
        ],
    )
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE yt_channels SET lang = 'other' WHERE channel_id = :id"
            ),
            {"id": channel_id(5)},
        )

    summary = await run_weekly(database, now=NOW, preview=True)

    assert listed(summary, YtAlertRoute.EN) == [video_id(900)]
    assert listed(summary, YtAlertRoute.RU) == [video_id(901)]


async def test_an_ordinary_week_lists_nothing_and_is_stored(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(
        database,
        [published(900 + n, 1 + n % 3, wednesday(n)) for n in range(6)],
    )

    summary = await run_weekly(database, now=NOW)

    (route,) = [r for r in summary.routes if r.route is YtAlertRoute.EN]
    assert route.entries == [] and route.videos_scored == 6
    assert await lists(database) == [("en", 0, 0), ("ru", 0, 0)]


async def test_the_pass_changes_nothing_but_the_lists(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [published(900, 1, wednesday(), boost=5)])

    async def counts() -> tuple[int, ...]:
        async with database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT (SELECT count(*) FROM yt_raw_videos), "
                        "(SELECT count(*) FROM yt_raw_video_stats), "
                        "(SELECT count(*) FROM yt_channels), "
                        "(SELECT count(*) FROM yt_requests), "
                        "(SELECT count(*) FROM yt_quota_days), "
                        "(SELECT count(*) FROM yt_baseline_runs), "
                        "(SELECT count(*) FROM yt_alerts), "
                        "(SELECT count(*) FROM alerts)"
                    )
                )
            ).one()
        return tuple(row)

    before = await counts()
    await run_weekly(database, now=NOW)

    assert await counts() == before
    assert await lists(database) == [("en", 1, 1), ("ru", 0, 0)]


# --- the ranking, pure ------------------------------------------------


def best(z: float, *, crossed_hours: float = 0) -> Best:
    moment = START + timedelta(hours=crossed_hours)
    return Best(
        score=Score(
            metric="views",
            age=timedelta(hours=24),
            observed=1.0,
            expected=1.0,
            z=z,
        ),
        crossed_at=moment if z >= 2.0 else None,
        scored_at=moment,
    )


def test_three_videos_of_a_channel_are_one_standing() -> None:
    found = rank_week(
        {
            ("A", "a1"): best(4.0),
            ("A", "a2"): best(2.6),
            ("A", "a3"): best(2.1),
            ("A", "a4"): best(1.9),
        },
        floor=2.0,
    )

    assert [(s.video_id, s.others) for s in found] == [("a1", 2)]


def test_channels_rank_by_their_best_then_by_crossing_then_id() -> None:
    found = rank_week(
        {
            ("A", "a1"): best(3.0, crossed_hours=5),
            ("B", "b1"): best(3.0, crossed_hours=1),
            ("C", "c2"): best(5.0),
            ("D", "d9"): best(3.0, crossed_hours=1),
            ("E", "e1"): best(1.5),
        },
        floor=2.0,
    )

    assert [s.video_id for s in found] == ["c2", "b1", "d9", "a1"]
