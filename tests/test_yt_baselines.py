"""YouTube's baselines, read and refreshed against a real Postgres.

What the refresh measures — a channel's own reference, its settled
fallback, a kind's curve — on synthetic channels whose videos grow along a
known shape (`ytscoring`). The fitting arithmetic is Telegram's, tested in
`test_scoring.py`; what is tested here is what YouTube adds: formats, the
scope, one settled reading per video, and runs.
"""

from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
import ytscoring
from sqlalchemy import text
from ytfakes import channel_id, video_id
from ytscoring import SPREAD, Video, grown, seed_channel, seed_videos

from itgraph.config import Settings, settings
from itgraph.db.models import (
    BaselineReference,
    YtBaselineRun,
    YtChannelKind,
    YtMetric,
    YtVideoFormat,
)
from itgraph.db.session import Database
from itgraph.db.yt_baselines import (
    RunInForce,
    load_scope,
    load_videos,
    load_yt_baselines,
    runs_in_force,
)
from itgraph.scoring.yt_refresh import refresh_yt_baselines

NOW = datetime(2026, 12, 10, 12, 0, tzinfo=UTC)
LONG = YtVideoFormat.LONG
SHORT = YtVideoFormat.SHORT

# Lowered so that a few synthetic channels fit every band.
FLOORS = {
    "youtube_baseline_min_band_samples": 3,
    "youtube_baseline_min_band_spread_posts": 3,
    "youtube_baseline_min_channel_videos": 3,
    "youtube_baseline_recent_videos": 5,
    "youtube_baseline_recent_min_videos": 3,
}
PINNED = (
    "youtube_scoring_short_max_seconds",
    "youtube_baseline_window_days",
    "youtube_baseline_mature_days",
    "youtube_baseline_mature_max_days",
    "youtube_baseline_recent_max_days",
)


@pytest.fixture(autouse=True)
def floors(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PINNED:
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )
    for name, value in FLOORS.items():
        monkeypatch.setattr(settings, name, value)


watched = partial(ytscoring.watched, now=NOW)
settled = partial(ytscoring.settled, now=NOW)
a_channel = partial(ytscoring.a_channel, now=NOW)


async def references(
    database: Database,
) -> dict[tuple[str, str, str], tuple[float, int, str]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT channel_id, format::text, metric::text, median, "
                "samples, reference::text FROM yt_channel_baselines"
            )
        )
    return {
        (channel, fmt, metric): (median, samples, reference)
        for channel, fmt, metric, median, samples, reference in rows
    }


# --- what is read ----------------------------------------------------


async def test_the_scope_is_seeds_labelled_english_or_russian(
    database: Database,
) -> None:
    await seed_channel(database, 1, lang="en")
    await seed_channel(database, 2, lang="ru", kind="company")
    await seed_channel(database, 3, lang="other")
    await seed_channel(database, 4, status="candidate", kind=None, lang=None)
    await seed_channel(database, 5, status="rejected")

    async with database.session() as session:
        scope = await load_scope(session)

    assert scope == {
        channel_id(1): YtChannelKind.PERSONAL,
        channel_id(2): YtChannelKind.COMPANY,
    }


async def test_videos_of_channels_out_of_scope_are_not_read(
    database: Database,
) -> None:
    await seed_channel(database, 1)
    await seed_channel(database, 3, lang="other")
    await seed_videos(
        database,
        [
            watched(10, 1, days_ago=2),
            watched(30, 3, days_ago=2),
        ],
    )

    async with database.session() as session:
        videos = await load_videos(
            session,
            since=NOW - timedelta(days=30),
            until=NOW,
            short_max_seconds=180,
        )

    assert set(videos) == {video_id(10)}


async def test_a_video_s_format_is_read_from_its_payload(
    database: Database,
) -> None:
    await seed_channel(database, 1)
    published = NOW - timedelta(days=1)
    await seed_videos(
        database,
        [
            Video(10, 1, published, duration="PT45S"),
            Video(11, 1, published, duration="PT45M"),
            Video(12, 1, published, duration="PT1H", broadcast=True),
            Video(13, 1, published, duration=None),
        ],
    )

    async with database.session() as session:
        videos = await load_videos(
            session,
            since=NOW - timedelta(days=2),
            until=NOW,
            short_max_seconds=180,
        )

    assert [
        (videos[video_id(n)].format, videos[video_id(n)].broadcast)
        for n in (10, 11, 12, 13)
    ] == [(SHORT, False), (LONG, False), (None, True), (None, False)]


async def test_a_reading_after_the_moment_is_not_read(
    database: Database,
) -> None:
    await seed_channel(database, 1)
    await seed_videos(database, [watched(10, 1, days_ago=3)])

    async with database.session() as session:
        videos = await load_videos(
            session,
            since=NOW - timedelta(days=5),
            until=NOW - timedelta(days=2),
            short_max_seconds=180,
        )

    latest = videos[video_id(10)].readings[-1][0]
    assert latest <= NOW - timedelta(days=2)


async def test_a_counter_youtube_did_not_report_stays_absent(
    database: Database,
) -> None:
    await seed_channel(database, 1)
    published = NOW - timedelta(days=1)
    await seed_videos(
        database,
        [Video(10, 1, published, readings=[(NOW, 500, None, None)])],
    )

    async with database.session() as session:
        videos = await load_videos(
            session,
            since=NOW - timedelta(days=2),
            until=NOW,
            short_max_seconds=180,
        )

    counters = videos[video_id(10)].readings[0][1]
    assert (counters.views, counters.likes, counters.comments) == (
        500,
        None,
        None,
    )


# --- the refresh -----------------------------------------------------


async def test_a_channel_with_enough_newest_videos_is_its_own_reference(
    database: Database,
) -> None:
    await a_channel(database, 1, views=1000)

    await refresh_yt_baselines(database, now=NOW)

    found = await references(database)
    assert found[(channel_id(1), "long", "views")] == (1000.0, 5, "recent")


async def test_a_channel_that_has_grown_is_its_newest_videos(
    database: Database,
) -> None:
    await seed_channel(database, 1)
    await seed_videos(
        database,
        [
            watched(100 + index, 1, days_ago=2 + index, views=1000)
            for index in range(5)
        ]
        + [
            watched(150 + index, 1, days_ago=20 + index, views=100)
            for index in range(10)
        ],
    )

    await refresh_yt_baselines(database, now=NOW)

    assert (await references(database))[(channel_id(1), "long", "views")][
        0
    ] == 1000.0


async def test_too_few_newest_videos_fall_back_to_the_settled_ones(
    database: Database,
) -> None:
    await a_channel(database, 1)
    await a_channel(database, 2, watched_videos=2, settled_videos=4, views=400)

    await refresh_yt_baselines(database, now=NOW)

    found = await references(database)
    median, samples, reference = found[(channel_id(2), "long", "views")]
    assert reference == "mature"
    assert samples == 4
    assert median == pytest.approx(600 * 1.35)
    # Comments take no fallback.
    assert (channel_id(2), "long", "comments") not in found


async def test_a_settled_video_counts_once_at_its_latest_reading(
    database: Database,
) -> None:
    await a_channel(database, 1)
    await seed_channel(database, 2)
    await seed_videos(
        database,
        [
            settled(
                200 + index,
                2,
                days_ago=100,
                views=1000,
                read_at_days=(40, 65, 90),
            )
            for index in range(3)
        ],
    )

    await refresh_yt_baselines(database, now=NOW)

    median, samples, _ = (await references(database))[
        (channel_id(2), "long", "views")
    ]
    assert samples == 3
    assert median == round(1000 * 1.9)


async def test_a_channel_s_shorts_and_talks_are_two_references(
    database: Database,
) -> None:
    await a_channel(database, 1, views=1000)
    await seed_videos(
        database,
        [
            watched(
                170 + index,
                1,
                days_ago=1.5 + index,
                views=200,
                duration="PT40S",
                boost=SPREAD[index],
            )
            for index in range(5)
        ],
    )

    await refresh_yt_baselines(database, now=NOW)

    found = await references(database)
    assert found[(channel_id(1), "long", "views")][0] == 1000.0
    assert found[(channel_id(1), "short", "views")][0] == 200.0


async def test_a_broadcast_takes_no_part(database: Database) -> None:
    await a_channel(database, 1, views=1000)
    published = NOW - timedelta(days=3)
    await seed_videos(
        database,
        [
            Video(
                170 + index,
                1,
                published - timedelta(hours=index),
                broadcast=True,
                readings=grown(published, until=NOW, views=50_000),
            )
            for index in range(5)
        ],
    )

    await refresh_yt_baselines(database, now=NOW)

    assert (await references(database))[(channel_id(1), "long", "views")][
        0
    ] == 1000.0


async def test_a_kind_too_thin_borrows_its_format_s_pool(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    await a_channel(database, 4, kind="media", watched_videos=1)

    summary = await refresh_yt_baselines(database, now=NOW)

    assert (
        YtChannelKind.MEDIA,
        LONG,
        YtMetric.VIEWS,
    ) in summary.found.borrowed
    async with database.session() as session:
        borrowed = await session.scalar(
            text(
                "SELECT borrowed FROM yt_metric_baselines WHERE kind = 'media' "
                "AND format = 'long' AND metric = 'views'"
            )
        )
    assert borrowed is True


async def test_a_band_seen_on_few_videos_keeps_no_spread_of_its_own(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "youtube_baseline_min_band_spread_posts", 1000
    )
    for channel in (1, 2, 3):
        await a_channel(database, channel)

    await refresh_yt_baselines(database, now=NOW)

    async with database.session() as session:
        spreads = (
            await session.execute(
                text("SELECT count(*), count(spread) FROM yt_curve_points")
            )
        ).one()
    assert spreads[0] > 0
    assert spreads[1] == 0


async def test_the_curve_is_named_by_youtube_s_bands(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)

    await refresh_yt_baselines(database, now=NOW)

    async with database.session() as session:
        bands = set(
            await session.scalars(
                text(
                    "SELECT band FROM yt_curve_points WHERE kind = 'personal' "
                    "AND format = 'long' AND metric = 'views'"
                )
            )
        )
    assert "1260m" in bands
    assert "180m" in bands
    assert not bands & {"10m", "420m"}


async def test_a_run_records_its_parameters_and_counts(
    database: Database,
) -> None:
    await a_channel(database, 1)
    await a_channel(database, 2, watched_videos=2)
    await seed_channel(database, 9)

    await refresh_yt_baselines(database, now=NOW)

    async with database.session() as session:
        run = await session.scalar(text("SELECT id FROM yt_baseline_runs"))
        row = (
            await session.execute(
                text(
                    "SELECT window_days, mature_days, mature_max_days, "
                    "recent_videos, channels_in_scope, channels_with_baseline, "
                    "channels_on_fallback, completed_at FROM yt_baseline_runs "
                    "WHERE id = :id"
                ),
                {"id": run},
            )
        ).one()
    assert row == (28, 29, 120, 5, 3, 2, 1, NOW)


async def test_an_interrupted_refresh_is_never_read(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    first = await refresh_yt_baselines(database, now=NOW)
    async with database.session() as session:
        session.add(
            YtBaselineRun(
                window_days=28,
                mature_days=29,
                mature_max_days=120,
                min_channel_videos=3,
                recent_videos=5,
                recent_min_videos=3,
                recent_max_days=180,
                min_band_samples=3,
                min_band_spread_posts=3,
                channels_in_scope=0,
                channels_with_baseline=0,
                channels_on_fallback=0,
            )
        )

    async with database.session() as session:
        baselines = await load_yt_baselines(
            session, kinds=await load_scope(session)
        )

    assert baselines is not None
    assert baselines.run_id == first.run_id
    found = baselines.for_video(channel_id(1), LONG, YtMetric.VIEWS)
    assert found is not None
    assert found.reference == 1000.0
    assert found.factor == 1.0


async def test_a_baseline_on_the_fallback_takes_its_kind_s_factor(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    await a_channel(database, 4, watched_videos=2, views=400)
    await refresh_yt_baselines(database, now=NOW)

    async with database.session() as session:
        baselines = await load_yt_baselines(
            session, kinds=await load_scope(session)
        )

    assert baselines is not None
    found = baselines.for_video(channel_id(4), LONG, YtMetric.VIEWS)
    assert found is not None
    assert (
        found.factor
        == baselines.factors[(YtChannelKind.PERSONAL, LONG, YtMetric.VIEWS)]
    )
    assert baselines.medians[(channel_id(4), LONG, YtMetric.VIEWS)][1] is (
        BaselineReference.MATURE
    )
    assert baselines.fallback_channels == 1


async def test_a_refresh_with_nothing_to_read_completes(
    database: Database,
) -> None:
    summary = await refresh_yt_baselines(database, now=NOW)

    assert "0 of 0 channel(s) have a baseline" in summary.line()
    assert "nothing published it" in summary.line()
    async with database.session() as session:
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM yt_baseline_runs WHERE completed_at IS NOT NULL"
                )
            )
            == 1
        )


# --- runs read by the weekly list ------------------------------------


def _unfinished() -> YtBaselineRun:
    return YtBaselineRun(
        window_days=28,
        mature_days=29,
        mature_max_days=120,
        min_channel_videos=3,
        recent_videos=5,
        recent_min_videos=3,
        recent_max_days=180,
        min_band_samples=3,
        min_band_spread_posts=3,
        channels_in_scope=0,
        channels_with_baseline=0,
        channels_on_fallback=0,
    )


async def test_an_older_run_is_read_by_its_id(database: Database) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    first = await refresh_yt_baselines(database, now=NOW - timedelta(days=1))
    second = await refresh_yt_baselines(database, now=NOW)

    async with database.session() as session:
        kinds = await load_scope(session)
        newest = await load_yt_baselines(session, kinds=kinds)
        older = await load_yt_baselines(
            session, kinds=kinds, run_id=first.run_id
        )

    assert newest is not None and newest.run_id == second.run_id
    assert older is not None and older.run_id == first.run_id
    assert older.computed_at == NOW - timedelta(days=1)
    assert older.for_video(channel_id(1), LONG, YtMetric.VIEWS) is not None


async def test_a_run_in_progress_is_not_read_by_its_id(
    database: Database,
) -> None:
    async with database.session() as session:
        run = _unfinished()
        session.add(run)
        await session.flush()
        unfinished = run.id

    async with database.session() as session:
        assert (
            await load_yt_baselines(session, kinds={}, run_id=unfinished)
            is None
        )
        assert await runs_in_force(session, since=NOW, until=NOW) == []


async def test_the_runs_in_force_over_a_period(database: Database) -> None:
    moments = [NOW - timedelta(days=days) for days in (10, 8, 3, 1)]
    for moment in moments:
        await refresh_yt_baselines(database, now=moment)
    async with database.session() as session:
        session.add(_unfinished())

    since = NOW - timedelta(days=7)
    async with database.session() as session:
        found = await runs_in_force(session, since=since, until=NOW)

    # The run of eight days ago was in force when the period began; the one
    # of ten was replaced before it.
    assert [run.completed_at for run in found] == moments[1:]
    assert all(isinstance(run, RunInForce) for run in found)


async def test_a_period_before_any_run_has_none_in_force_at_its_start(
    database: Database,
) -> None:
    await refresh_yt_baselines(database, now=NOW - timedelta(days=2))

    async with database.session() as session:
        found = await runs_in_force(
            session, since=NOW - timedelta(days=7), until=NOW
        )
        none = await runs_in_force(
            session,
            since=NOW - timedelta(days=14),
            until=NOW - timedelta(days=7),
        )

    assert [run.completed_at for run in found] == [NOW - timedelta(days=2)]
    assert none == []
