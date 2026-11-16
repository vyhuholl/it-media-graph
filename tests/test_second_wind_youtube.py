"""The second wind of YouTube's weekly lists, computed by their pass.

The norm is read from the run the pass reads, so a test sets its paces as
shares of whatever the small world's baselines give.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from test_yt_weekly import (
    NOW,
    START,
    listed,
    published,
    shipped,  # noqa: F401 - the autouse fixture, applied here too
    wednesday,
    world,
)
from ytfakes import channel_id, video_id
from ytscoring import Video, seed_videos

from itgraph.config import Settings, settings
from itgraph.db.models import YtAlertRoute, YtMetric, YtVideoFormat
from itgraph.db.session import Database
from itgraph.db.yt_baselines import load_scope, load_yt_baselines
from itgraph.scoring.yt_weekly import run_weekly

PUBLISHED = START - timedelta(days=60)


@pytest.fixture(autouse=True)
def shipped_second_wind(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "youtube_weekly_second_wind_enabled",
        "youtube_weekly_second_wind_acceleration",
        "youtube_weekly_second_wind_norm_share",
        "youtube_weekly_second_wind_size",
        "youtube_backfill_days",
    ):
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )


async def norm_of(database: Database, channel: int) -> float:
    async with database.session() as session:
        baselines = await load_yt_baselines(
            session, kinds=await load_scope(session)
        )
    assert baselines is not None
    baseline = baselines.for_video(
        channel_id(channel), YtVideoFormat.LONG, YtMetric.VIEWS
    )
    assert baseline is not None
    return baseline.reference * baseline.factor


async def paced(
    database: Database,
    number: int,
    channel: int,
    *,
    previous: float,
    now: float,
    duration: str = "PT20M",
) -> None:
    """An older video read a week before the week, at its start and five
    days in, at these views a day."""
    start = 5000 + 7 * previous
    await seed_videos(
        database,
        [
            Video(
                number=number,
                channel=channel,
                published=PUBLISHED,
                duration=duration,
                readings=[
                    (START - timedelta(days=8), 5000, None, None),
                    (START - timedelta(days=1), round(start), None, None),
                    (
                        START + timedelta(days=5),
                        round(start + 6 * now),
                        None,
                        None,
                    ),
                ],
            )
        ],
    )


async def stored(database: Database) -> list[tuple[str, str, bool]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT l.route::text, r.video_id, l.second_wind_shown "
                "FROM yt_weekly_revivals r "
                "JOIN yt_weekly_lists l ON l.id = r.list_id "
                "ORDER BY l.route, r.rank"
            )
        )
    return [(route, video, shown) for route, video, shown in rows]


async def test_a_talk_that_sped_up_is_in_its_language_s_shadow_section(
    database: Database,
) -> None:
    await world(database)
    norm = await norm_of(database, 1)
    await paced(database, 900, 1, previous=0.02 * norm, now=0.2 * norm)

    summary = await run_weekly(database, now=NOW)

    [en, ru] = summary.routes
    [revival] = en.revivals
    assert revival.video_id == video_id(900)
    assert revival.acceleration == pytest.approx(10, rel=0.05)
    assert revival.share == pytest.approx(1.4, rel=0.05)
    assert ru.revivals == []
    assert await stored(database) == [("en", video_id(900), False)]


async def test_a_short_that_sped_up_is_not_judged(database: Database) -> None:
    await world(database)
    norm = await norm_of(database, 1)
    await paced(
        database,
        900,
        1,
        previous=0.02 * norm,
        now=0.2 * norm,
        duration="PT40S",
    )

    summary = await run_weekly(database, now=NOW)

    assert all(route.second_wind_judged == 0 for route in summary.routes)


async def test_a_slow_burn_video_is_not_a_second_wind(
    database: Database,
) -> None:
    """Gaining twice its channel's norm a week, as it did the week before."""
    await world(database)
    norm = await norm_of(database, 1)
    await paced(database, 900, 1, previous=0.3 * norm, now=0.3 * norm)

    summary = await run_weekly(database, now=NOW)

    [en, _] = summary.routes
    assert en.second_wind_judged == 1
    assert en.revivals == []


async def test_one_per_channel_the_largest_share_first_up_to_the_size(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_weekly_second_wind_size", 1)
    await world(database)
    first = await norm_of(database, 1)
    second = await norm_of(database, 2)
    await paced(database, 900, 1, previous=0.02 * first, now=0.2 * first)
    await paced(database, 901, 2, previous=0.02 * second, now=0.4 * second)

    summary = await run_weekly(database, now=NOW)

    [en, _] = summary.routes
    assert en.second_wind_channels == 2
    [revival] = en.revivals
    assert (revival.channel_id, revival.video_id) == (
        channel_id(2),
        video_id(901),
    )
    assert revival.share == pytest.approx(2.8, rel=0.05)


async def test_a_channel_in_both_sections(database: Database) -> None:
    """Its new video on the list, its older talk in the second wind."""
    await world(database)
    norm = await norm_of(database, 1)
    await seed_videos(database, [published(950, 1, wednesday(), boost=8)])
    await paced(database, 900, 1, previous=0.02 * norm, now=0.2 * norm)

    summary = await run_weekly(database, now=NOW)

    [en, _] = summary.routes
    assert listed(summary, YtAlertRoute.EN) == [video_id(950)]
    assert [(r.channel_id, r.video_id) for r in en.revivals] == [
        (channel_id(1), video_id(900))
    ]


async def test_the_language_is_the_label(database: Database) -> None:
    await world(database, lang="ru")
    norm = await norm_of(database, 1)
    await paced(database, 900, 1, previous=0.02 * norm, now=0.2 * norm)

    summary = await run_weekly(database, now=NOW)

    by_route = {route.route: route for route in summary.routes}
    assert [r.video_id for r in by_route[YtAlertRoute.RU].revivals] == [
        video_id(900)
    ]
    assert by_route[YtAlertRoute.EN].revivals == []


async def test_switched_on_the_lists_record_it(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_weekly_second_wind_enabled", True)
    await world(database)
    norm = await norm_of(database, 1)
    await paced(database, 900, 1, previous=0.02 * norm, now=0.2 * norm)

    summary = await run_weekly(database, now=NOW)

    assert await stored(database) == [("en", video_id(900), True)]
    assert "shown" in summary.line()


async def test_a_preview_prints_each_section(database: Database) -> None:
    await world(database)
    norm = await norm_of(database, 1)
    await paced(database, 900, 1, previous=0.02 * norm, now=0.2 * norm)

    summary = await run_weekly(database, now=NOW, preview=True)

    lines = summary.preview()
    assert lines.count("  second wind (in the shadow):") == 2
    assert any(video_id(900) in line and "x10" in line for line in lines)
    assert await stored(database) == []
