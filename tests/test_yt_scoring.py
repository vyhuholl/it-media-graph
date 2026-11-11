"""The YouTube scoring pass and its replay, against a real Postgres.

A world of synthetic channels (`ytscoring`), their baselines refreshed an
hour before the pass, and then the videos each test is about. The fitting
arithmetic is Telegram's and tested there; what is tested here is what the
YouTube pass decides: what is scored, what crosses, what is raised and
folded, what the replay reports — and that it asks YouTube nothing.
"""

from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
import ytscoring
from sqlalchemy import text
from ytfakes import channel_id, video_id
from ytscoring import SHAPE, Video, grown, seed_channel, seed_videos

from itgraph.bot.destinations import Destination
from itgraph.config import Settings, settings
from itgraph.db import yt_alerts
from itgraph.db.models import (
    Platform,
    YtAlertKind,
    YtAlertRoute,
    YtMetric,
    YtVideoFormat,
)
from itgraph.db.session import Database
from itgraph.db.yt_baselines import load_scope, load_yt_baselines
from itgraph.scoring.score import Score
from itgraph.scoring.yt_refresh import refresh_yt_baselines
from itgraph.scoring.yt_run import (
    Outcome,
    YtReplay,
    replay_yt_scoring,
    run_yt_scoring,
    sweep_yt_scoring,
)

NOW = datetime(2026, 12, 10, 12, 0, tzinfo=UTC)
REFRESHED = NOW - timedelta(hours=1)
a_channel = partial(ytscoring.a_channel, now=NOW)
EVERYTHING = -1000.0
# Past what an ordinary video of the synthetic world reaches when the
# replay refits it day by day on six videos a channel, and far short of a
# video at five times its channel's norm.
CLEAR = 6.0

# A day's comments on a quiet channel's videos, in turn: about one, and
# spread as discussions are, not evenly.
QUIET = (0, 3, 1, 0, 2, 1)

FLOORS = {
    "youtube_baseline_min_band_samples": 3,
    # Five, not three: a late band reached by four videos would otherwise
    # keep a spread of its own narrow enough to make an ordinary one cross.
    "youtube_baseline_min_band_spread_posts": 5,
    "youtube_baseline_min_channel_videos": 3,
    "youtube_baseline_recent_videos": 5,
    "youtube_baseline_recent_min_videos": 3,
}
PINNED = (
    "youtube_alert_spike_z",
    "youtube_alert_spike_metrics",
    "youtube_alert_spike_comments_min",
    "youtube_alert_spike_fold_hours",
    "youtube_scoring_short_max_seconds",
    "youtube_baseline_window_days",
    "youtube_baseline_mature_days",
    "youtube_baseline_mature_max_days",
    "youtube_baseline_recent_max_days",
    "youtube_baseline_refresh_days",
    "youtube_alert_timezone",
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


async def world(database: Database) -> None:
    """Three English channels of a thousand views a day, refreshed."""
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    await refresh_yt_baselines(database, now=REFRESHED)


def fresh(
    number: int,
    channel: int,
    *,
    hours: float = 26,
    views: float = 1000,
    likes: float | None = 50,
    comments: float | None = 5,
    boost: float = 1.0,
    duration: str = "PT12M30S",
    broadcast: bool = False,
) -> Video:
    """A video published ``hours`` ago, read along the shape until now."""
    published = NOW - timedelta(hours=hours)
    return Video(
        number=number,
        channel=channel,
        published=published,
        duration=duration,
        broadcast=broadcast,
        readings=grown(
            published,
            until=NOW,
            views=views,
            likes=likes,
            comments=comments,
            boost=boost,
        ),
    )


async def alerts(database: Database) -> list[tuple[str, str, int | None]]:
    """(kind, video, head's alert id) for every YouTube alert."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT kind::text, video_id, folded_into FROM yt_alerts "
                "ORDER BY id"
            )
        )
    return [(kind, video, head) for kind, video, head in rows]


async def scores(database: Database) -> dict[str, Score]:
    """Every scored video's best score, by raising everything."""
    summary = await run_yt_scoring(database, now=NOW, threshold=EVERYTHING)
    return {spike.post_key[1]: spike.score for spike in summary.spikes}


# --- measured against its own channel, format and age ----------------


async def test_a_video_far_above_its_channel_raises_one_alert(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])

    summary = await run_yt_scoring(database, now=NOW)

    assert [video for _, video, _ in await alerts(database)] == [video_id(900)]
    assert summary.raised == 1


async def test_an_ordinary_video_scores_near_zero(database: Database) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1)])

    found = await scores(database)

    assert abs(found[video_id(900)].z) < 1


async def test_the_same_reach_scores_higher_on_a_smaller_channel(
    database: Database,
) -> None:
    for channel in (1, 2):
        await a_channel(database, channel)
    await a_channel(database, 5, views=300)
    await refresh_yt_baselines(database, now=REFRESHED)
    await seed_videos(
        database,
        [
            fresh(900, 1, views=600, likes=None, comments=None),
            fresh(901, 5, views=600, likes=None, comments=None),
        ],
    )

    found = await scores(database)

    assert found[video_id(901)].z > found[video_id(900)].z


async def test_the_same_reach_scores_higher_on_a_younger_video(
    database: Database,
) -> None:
    await world(database)
    young = NOW - timedelta(hours=8, minutes=10)
    old = NOW - timedelta(hours=72, minutes=10)
    await seed_videos(
        database,
        [
            Video(900, 1, young, readings=[(NOW, 1000, None, None)]),
            Video(901, 1, old, readings=[(NOW, 1000, None, None)]),
        ],
    )

    found = await scores(database)

    assert found[video_id(900)].z > 0 > found[video_id(901)].z


async def test_a_short_is_scored_against_the_channel_s_shorts(
    database: Database,
) -> None:
    await a_channel(database, 1)
    await seed_videos(
        database,
        [
            ytscoring.watched(
                170 + index,
                1,
                now=NOW,
                days_ago=2 + index,
                views=100,
                duration="PT40S",
                boost=ytscoring.SPREAD[index],
            )
            for index in range(5)
        ]
        + [
            ytscoring.settled(
                180 + index,
                1,
                now=NOW,
                days_ago=40 + index,
                views=150,
                duration="PT40S",
            )
            for index in range(4)
        ],
    )
    for channel in (2, 3):
        await a_channel(database, channel)
    await refresh_yt_baselines(database, now=REFRESHED)
    # A Short reaching a talk's ordinary views is ten times its own norm.
    await seed_videos(
        database,
        [
            fresh(
                900, 1, views=1000, likes=None, comments=None, duration="PT40S"
            )
        ],
    )

    summary = await run_yt_scoring(database, now=NOW)

    assert [video for _, video, _ in await alerts(database)] == [video_id(900)]
    assert summary.raised == 1


# --- what is not scored ----------------------------------------------


async def test_a_broadcast_raises_nothing_and_is_counted(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=20, broadcast=True)])

    summary = await run_yt_scoring(database, now=NOW)

    assert await alerts(database) == []
    assert summary.broadcasts == 1
    assert "1 broadcast(s)" in summary.line()


async def test_a_video_whose_length_cannot_be_read_is_counted(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=20, duration="P0D")])

    summary = await run_yt_scoring(database, now=NOW)

    assert await alerts(database) == []
    assert summary.unreadable == 1


async def test_another_language_is_not_scored(database: Database) -> None:
    await world(database)
    await a_channel(database, 7, lang="other")
    await seed_videos(database, [fresh(900, 7, boost=20)])

    found = await scores(database)

    assert video_id(900) not in found


async def test_a_thin_channel_is_not_scored_and_is_counted(
    database: Database,
) -> None:
    await world(database)
    await seed_channel(database, 8)
    await seed_videos(database, [fresh(900, 8, boost=20)])

    summary = await run_yt_scoring(database, now=NOW)

    assert await alerts(database) == []
    assert summary.channels_in_scope == 4
    assert summary.channels_with_baseline == 3
    assert "1 of 4 channel(s) have too little history" in summary.line()


async def test_a_reading_before_three_hours_is_not_scored(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, hours=2.5, boost=20)])

    assert video_id(900) not in await scores(database)


async def test_a_reading_past_eight_and_a_half_days_raises_nothing(
    database: Database,
) -> None:
    await world(database)
    published = NOW - timedelta(days=10)
    await seed_videos(
        database,
        [Video(900, 1, published, readings=[(NOW, 50_000, None, None)])],
    )

    await run_yt_scoring(database, now=NOW)

    assert await alerts(database) == []


async def test_a_channel_that_hides_likes_is_scored_on_views(
    database: Database,
) -> None:
    await world(database)
    await seed_channel(database, 6)
    await seed_videos(
        database,
        [
            Video(
                600 + index,
                6,
                NOW - timedelta(days=2 + 2 * index),
                readings=[
                    (moment, views, None, comments)
                    for moment, views, _, comments in grown(
                        NOW - timedelta(days=2 + 2 * index),
                        until=NOW,
                        views=1000 * ytscoring.SPREAD[index],
                        likes=50,
                        comments=5,
                    )
                ],
            )
            for index in range(5)
        ],
    )
    await refresh_yt_baselines(database, now=REFRESHED)

    async with database.session() as session:
        baselines = await load_yt_baselines(
            session, kinds=await load_scope(session)
        )

    assert baselines is not None
    long = YtVideoFormat.LONG
    assert baselines.for_video(channel_id(6), long, YtMetric.VIEWS) is not None
    assert (channel_id(6), long, YtMetric.LIKES) not in baselines.medians


# --- one alert per signal, one message per event ---------------------


async def test_a_video_strong_on_two_signals_raises_its_best(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, views=3000, likes=1000)])

    await run_yt_scoring(database, now=NOW)

    assert [kind for kind, _, _ in await alerts(database)] == ["like_spike"]


async def test_the_alert_names_the_reading_its_best_score_came_from(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])

    summary = await run_yt_scoring(database, now=NOW)

    spike = summary.spikes[0]
    async with database.session() as session:
        named = (
            await session.execute(
                text(
                    "SELECT a.scored_at, s.views FROM yt_alerts a JOIN "
                    "yt_raw_video_stats s ON s.video_id = a.video_id AND "
                    "s.observed_at = a.scored_at"
                )
            )
        ).one()
    assert named[0] == spike.scored_at
    assert named[1] is not None


async def test_scoring_again_raises_nothing(database: Database) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])
    await run_yt_scoring(database, now=NOW)

    again = await run_yt_scoring(database, now=NOW + timedelta(minutes=15))

    assert again.raised == 0
    assert len(await alerts(database)) == 1


async def test_a_later_crossing_on_another_signal_is_an_alert_of_its_own(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, views=5000, likes=50)])
    await run_yt_scoring(database, now=NOW)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO yt_raw_video_stats (video_id, observed_at, views, "
                "likes, comments) VALUES (:v, :at, 5100, 2000, 5)"
            ),
            {"v": video_id(900), "at": NOW + timedelta(minutes=10)},
        )

    await run_yt_scoring(database, now=NOW + timedelta(minutes=15))

    kinds = [kind for kind, _, _ in await alerts(database)]
    assert sorted(kinds) == ["like_spike", "views_spike"]


async def test_a_batch_of_talks_is_one_message(database: Database) -> None:
    await world(database)
    await seed_videos(
        database,
        [
            fresh(900 + index, 2, hours=26 + index / 6, boost=5)
            for index in range(6)
        ],
    )

    summary = await run_yt_scoring(database, now=NOW)

    found = await alerts(database)
    assert len(found) == 6
    assert sum(1 for _, _, head in found if head is None) == 1
    assert summary.folded == 5


async def test_a_crossing_a_day_and_more_after_the_head_is_a_new_head(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])
    await run_yt_scoring(database, now=NOW)
    async with database.session() as session:
        await session.execute(
            text("UPDATE yt_alerts SET raised_at = :at"),
            {"at": NOW - timedelta(hours=30)},
        )
    await seed_videos(database, [fresh(901, 1, hours=27, boost=5)])

    await run_yt_scoring(database, now=NOW + timedelta(minutes=15))

    assert [head for _, _, head in await alerts(database)] == [None, None]


async def test_a_crossing_inside_the_day_folds_into_the_head_in_the_queue(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])
    await run_yt_scoring(database, now=NOW)
    # The queue dates a raise by the database's clock; this test's is NOW.
    async with database.session() as session:
        await session.execute(
            text("UPDATE yt_alerts SET raised_at = :at"), {"at": NOW}
        )
    await seed_videos(database, [fresh(901, 1, hours=27, boost=5)])

    await run_yt_scoring(database, now=NOW + timedelta(minutes=15))

    (_, _, first), (_, _, second) = await alerts(database)
    assert first is None
    assert second is not None


# --- comments, counted -----------------------------------------------


async def test_many_comments_where_there_are_usually_few_raise_an_alert(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel, views=200, comments=QUIET)
    await refresh_yt_baselines(database, now=REFRESHED)
    await seed_videos(
        database,
        [
            fresh(
                900,
                1,
                views=200,
                likes=10,
                comments=40 / SHAPE["comments"][24],
            )
        ],
    )

    await run_yt_scoring(database, now=NOW)

    assert [kind for kind, _, _ in await alerts(database)] == ["comment_spike"]
    async with database.session() as session:
        expected = await session.scalar(text("SELECT expected FROM yt_alerts"))
    assert expected is not None


async def test_comments_under_the_minimum_raise_nothing(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel, views=200, comments=QUIET)
    await refresh_yt_baselines(database, now=REFRESHED)
    await seed_videos(
        database,
        [
            Video(
                900,
                1,
                NOW - timedelta(hours=25),
                readings=[(NOW - timedelta(minutes=30), 200, 10, 9)],
            )
        ],
    )

    await run_yt_scoring(database, now=NOW)

    assert await alerts(database) == []


# --- reading only what was collected ---------------------------------


async def test_no_baselines_raise_nothing_and_say_so(
    database: Database,
) -> None:
    await a_channel(database, 1)
    await seed_videos(database, [fresh(900, 1, boost=20)])

    summary = await run_yt_scoring(database, now=NOW)

    assert await alerts(database) == []
    assert "itgraph yt baselines" in summary.line()


async def test_stale_baselines_are_named(database: Database) -> None:
    await world(database)

    summary = await run_yt_scoring(database, now=NOW + timedelta(days=3))

    assert "baselines are 3d old" in summary.line()


async def test_the_pass_changes_nothing_but_the_queue(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])

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
                        "(SELECT count(*) FROM alerts)"
                    )
                )
            ).one()
        return tuple(row)

    before = await counts()
    await run_yt_scoring(database, now=NOW)

    assert await counts() == before
    assert len(await alerts(database)) == 1


async def test_a_raised_alert_renders_as_change_6_s_message(
    database: Database,
) -> None:
    from itgraph.bot.queues import YoutubeQueue

    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])
    await run_yt_scoring(database, now=NOW)
    async with database.session() as session:
        (pending,) = await yt_alerts.claim(
            session, YtAlertRoute.EN, held=False, limit=None
        )
    destination = Destination(
        key="youtube_en",
        platform=Platform.YOUTUBE,
        chat_id=-100,
        topic_id=1,
        setting="YOUTUBE_ALERT_CHAT_ID",
        daily_cap=10,
        quiet_from=2,
        quiet_to=7,
        digest_hour=9,
        zone="Europe/Moscow",
        route=YtAlertRoute.EN,
    )

    rendered = await YoutubeQueue().render(
        database, pending, destination, now=NOW
    )

    assert rendered is not None
    assert "YouTube" in rendered
    assert video_id(900) in rendered
    assert "рассчитана itgraph" in rendered
    assert pending.kind in (YtAlertKind.VIEWS_SPIKE, YtAlertKind.LIKE_SPIKE)


# --- replay ----------------------------------------------------------


async def test_a_replay_writes_nothing(database: Database) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])

    found = await replay_yt_scoring(database, days=2, now=NOW, threshold=CLEAR)

    assert [spike.post_key[1] for spike in found.spikes] == [video_id(900)]
    assert await alerts(database) == []
    async with database.session() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM yt_baseline_runs"))
            == 1
        )


async def test_a_replay_of_the_present_names_what_the_pass_named(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel)
    day_start = datetime(2026, 12, 10, 0, 0, tzinfo=UTC).replace(
        hour=21, day=9
    )  # midnight in Moscow
    await refresh_yt_baselines(database, now=day_start)
    await seed_videos(
        database,
        [
            fresh(900, 1, hours=14, boost=5),
            fresh(901, 2, hours=13, boost=4),
            fresh(902, 3, hours=13),
        ],
    )

    live = await run_yt_scoring(database, now=NOW)
    replayed = await replay_yt_scoring(
        database, days=(NOW - day_start) / timedelta(days=1), now=NOW
    )

    assert {spike.post_key[1] for spike in replayed.spikes} == {
        spike.post_key[1] for spike in live.spikes
    }
    assert {spike.post_key[1] for spike in live.spikes} == {
        video_id(900),
        video_id(901),
    }


async def test_a_replay_scores_a_day_against_that_day_s_fit(
    database: Database,
) -> None:
    """A channel that grew tenfold on day three: a video of day five is
    ordinary for the channel as it was on day five, not on day one."""
    for channel in (2, 3):
        await a_channel(database, channel)
    await seed_channel(database, 1)
    start = NOW - timedelta(days=6)
    videos = []
    for index in range(8):
        published = start - timedelta(days=12) + timedelta(days=index * 1.5)
        level = 100 if index < 5 else 1000
        videos.append(
            Video(
                100 + index,
                1,
                published,
                readings=grown(
                    published,
                    until=NOW,
                    views=level * ytscoring.SPREAD[index % 6],
                    likes=None,
                    comments=None,
                ),
            )
        )
    await seed_videos(database, videos)
    day_five = start + timedelta(days=4, hours=10)
    await seed_videos(
        database,
        [
            Video(
                900,
                1,
                day_five,
                readings=grown(
                    day_five,
                    until=NOW,
                    views=1000,
                    likes=None,
                    comments=None,
                ),
            )
        ],
    )

    found = await replay_yt_scoring(database, days=6, now=NOW)

    assert video_id(900) not in {spike.post_key[1] for spike in found.spikes}


async def test_a_replay_reports_per_language_and_folds(
    database: Database,
) -> None:
    await world(database)
    await a_channel(database, 4, lang="ru")
    await refresh_yt_baselines(database, now=REFRESHED)
    await seed_videos(
        database,
        [
            fresh(900 + index, 2, hours=26 + index / 6, boost=5)
            for index in range(3)
        ]
        + [fresh(910, 4, boost=5)],
    )

    found = await replay_yt_scoring(database, days=3, now=NOW, threshold=CLEAR)

    totals = found.per_language()
    assert totals["en"] == (3, 1, 2)
    assert totals["ru"] == (1, 1, 0)
    lines = "\n".join(found.lines())
    assert "en: 1 message(s)" in lines
    assert "ru: 1 message(s)" in lines
    assert "gaps between one channel's crossings" in lines


# --- did it take off: what became of a crossing ----------------------


def _shaped(
    number: int,
    channel: int,
    published: datetime,
    *,
    boosts: dict[int, float],
) -> Video:
    """Views along the shape, each age at its own multiple of the norm."""
    return Video(
        number,
        channel,
        published,
        readings=[
            (
                published + timedelta(hours=hours),
                round(1000 * boost * SHAPE["views"][hours]),
                None,
                None,
            )
            for hours, boost in boosts.items()
            if published + timedelta(hours=hours) <= NOW
        ],
    )


async def test_a_crossing_that_stays_high_held(database: Database) -> None:
    await world(database)
    published = NOW - timedelta(days=5, hours=2)
    await seed_videos(
        database,
        [
            _shaped(
                900,
                1,
                published,
                boosts=dict.fromkeys((4, 8, 12, 18, 24, 72, 96, 120), 5.0),
            )
        ],
    )

    found = await replay_yt_scoring(database, days=6, now=NOW, threshold=CLEAR)

    [spike] = [s for s in found.spikes if s.post_key[1] == video_id(900)]
    assert found.outcome(spike) == Outcome.HELD


async def test_a_crossing_that_falls_back_faded(database: Database) -> None:
    """The counter caught up, or a burst died: high early, ordinary later."""
    await world(database)
    published = NOW - timedelta(days=5, hours=2)
    boosts = {
        4: 8.0,
        8: 8.0,
        12: 1.0,
        18: 1.0,
        24: 1.0,
        72: 1.0,
        96: 1.0,
        120: 1.0,
    }
    await seed_videos(database, [_shaped(900, 1, published, boosts=boosts)])

    found = await replay_yt_scoring(database, days=6, now=NOW, threshold=CLEAR)

    [spike] = [s for s in found.spikes if s.post_key[1] == video_id(900)]
    assert found.outcome(spike) == Outcome.FADED
    assert "faded 1" in "\n".join(found.summary_lines())


async def test_a_crossing_younger_than_three_days_is_too_early(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(database, [fresh(900, 1, boost=5)])

    found = await replay_yt_scoring(database, days=2, now=NOW, threshold=CLEAR)

    [spike] = [s for s in found.spikes if s.post_key[1] == video_id(900)]
    assert found.outcome(spike) == Outcome.EARLY


async def test_several_thresholds_are_replayed_from_one_fit(
    database: Database,
) -> None:
    await world(database)
    await seed_videos(
        database,
        [fresh(900, 1, boost=3), fresh(901, 2, boost=12)],
    )

    low, high = await sweep_yt_scoring(
        database, days=2, now=NOW, thresholds=[CLEAR, 12.0]
    )

    assert {s.post_key[1] for s in high.spikes} <= {
        s.post_key[1] for s in low.spikes
    }
    assert video_id(901) in {s.post_key[1] for s in high.spikes}
    assert (low.threshold, high.threshold) == (CLEAR, 12.0)


async def test_a_replay_can_lower_the_comment_minimum(
    database: Database,
) -> None:
    for channel in (1, 2, 3):
        await a_channel(database, channel, views=200, comments=QUIET)
    await refresh_yt_baselines(database, now=REFRESHED)
    await seed_videos(
        database,
        [
            Video(
                900,
                1,
                NOW - timedelta(hours=25),
                readings=[(NOW - timedelta(minutes=30), 200, 10, 9)],
            )
        ],
    )

    shipped = await replay_yt_scoring(database, days=2, now=NOW)
    lowered = await replay_yt_scoring(
        database, days=2, now=NOW, comments_min=5
    )

    def commented(found: YtReplay) -> set[str]:
        return {
            s.post_key[1] for s in found.spikes if s.score.metric == "comments"
        }

    assert video_id(900) not in commented(shipped)
    assert video_id(900) in commented(lowered)
    assert lowered.comments_min == 5


async def test_late_settled_readings_score_the_days_before_them(
    database: Database,
) -> None:
    """The past uploads were first read yesterday, as by the one-off pass.

    Without them no factor can be fitted, so a day before them scores
    nothing; with `late_settled` that day's fit takes them, and a video
    that crossed five days ago crosses on its own day.
    """
    read_late = NOW - timedelta(days=1)
    for channel in (1, 2, 3):
        await a_channel(database, channel, settled_videos=0)
        await seed_videos(
            database,
            [
                ytscoring.settled(
                    channel * 100 + 50 + index,
                    channel,
                    now=NOW,
                    days_ago=45 + index,
                    read_at_days=(44 + index,),
                )
                for index in range(4)
            ],
        )
    published = NOW - timedelta(days=5, hours=2)
    await seed_videos(
        database,
        [
            _shaped(
                900,
                1,
                published,
                boosts=dict.fromkeys((4, 8, 12, 18, 24, 72, 96, 120), 5.0),
            )
        ],
    )

    strict = await replay_yt_scoring(
        database, days=6, now=NOW, threshold=CLEAR
    )
    late = await replay_yt_scoring(
        database, days=6, now=NOW, threshold=CLEAR, late_settled=True
    )

    [early] = [s for s in late.spikes if s.post_key[1] == video_id(900)]
    assert early.crossed_at < published + timedelta(days=1)
    assert all(
        s.crossed_at > read_late
        for s in strict.spikes
        if s.post_key[1] == video_id(900)
    )
    assert "settled readings taken later" in late.summary_lines()[0]
    assert "settled readings taken later" not in strict.summary_lines()[0]
