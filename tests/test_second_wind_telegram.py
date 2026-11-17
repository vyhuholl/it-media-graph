"""The second wind of Telegram's weekly list, computed by its pass.

The channel's norm is its mature median of 1,000 views at eight hours
times the factor 0.44 (``test_telegram_weekly.a_run``): 440 views.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from test_telegram_weekly import (
    CHANNEL,
    EARLY,
    LISTED,
    START,
    WEDNESDAY,
    a_channel,
    a_post,
    a_run,
    read,
    shipped_settings,  # noqa: F401 - the autouse fixture, applied here too
    views_for,
)

from itgraph.config import Settings, settings
from itgraph.db.session import Database
from itgraph.scoring.weekly import run_weekly

NORM = 1000 * 0.44
PUBLISHED = START - timedelta(days=20)


@pytest.fixture(autouse=True)
def shipped_second_wind(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "weekly_second_wind_enabled",
        "weekly_second_wind_acceleration",
        "weekly_second_wind_norm_share",
        "weekly_second_wind_size",
        "watch_late_horizon_days",
    ):
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )


async def paced(
    database: Database,
    msg_id: int,
    previous: float,
    now: float,
    *,
    channel: int = CHANNEL,
    published: datetime = PUBLISHED,
    album: int | None = None,
) -> None:
    """A post read a week before the week, at its start and five days
    in, at these views a day."""
    await a_post(
        database, published, msg_id=msg_id, channel=channel, album=album
    )
    base = 1000
    start = base + 7 * previous
    for at, views in (
        (START - timedelta(days=8), base),
        (START - timedelta(days=1), start),
        (START + timedelta(days=5), start + 6 * now),
    ):
        await read(
            database, at, msg_id=msg_id, channel=channel, views=round(views)
        )


async def stored(database: Database) -> list[tuple[int, int, bool]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT r.msg_id, r.others, l.second_wind_shown "
                "FROM tg_weekly_revivals r JOIN tg_weekly_lists l ON l.id = r.list_id "
                "ORDER BY r.rank"
            )
        )
    return [(msg, others, shown) for msg, others, shown in rows]


async def test_an_older_post_that_sped_up_is_in_the_shadow_section(
    database: Database,
) -> None:
    """100 views a day, then 400: four times, and 2,800 a week against a
    norm of 440."""
    await a_run(database, START - timedelta(days=30))
    await paced(database, 200, 100, 400)

    summary = await run_weekly(database, now=LISTED)

    [revival] = summary.revivals
    assert (revival.msg_id, revival.others) == (200, 0)
    assert revival.pace == pytest.approx(400, rel=0.01)
    assert revival.norm == pytest.approx(NORM)
    assert await stored(database) == [(200, 0, False)]
    assert "in the shadow" in summary.line()
    assert summary.entries == []


async def test_ordinary_decay_is_not_a_second_wind(database: Database) -> None:
    await a_run(database, START - timedelta(days=30))
    await paced(database, 200, 100, 50)

    summary = await run_weekly(database, now=LISTED)

    assert summary.revivals == []
    assert summary.second_wind_judged == 1


async def test_a_post_of_the_previous_week_is_not_judged(
    database: Database,
) -> None:
    await a_run(database, START - timedelta(days=30))
    await paced(database, 200, 100, 400, published=START - timedelta(days=3))

    summary = await run_weekly(database, now=LISTED)

    assert summary.second_wind_judged == 0


async def test_an_album_is_judged_as_its_first_part(
    database: Database,
) -> None:
    await a_run(database, START - timedelta(days=30))
    for part in (300, 301, 302):
        await paced(database, part, 100, 400, album=7)

    summary = await run_weekly(database, now=LISTED)

    assert summary.second_wind_judged == 1
    assert [revival.msg_id for revival in summary.revivals] == [300]


async def test_a_channel_without_a_norm_is_not_judged(
    database: Database,
) -> None:
    other = CHANNEL + 9
    await a_run(database, START - timedelta(days=30))
    await a_channel(database, other)
    await paced(database, 200, 100, 400, channel=other)

    summary = await run_weekly(database, now=LISTED)

    assert summary.second_wind_judged == 0


async def test_one_per_channel_the_largest_share_first_up_to_the_size(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "weekly_second_wind_size", 1)
    second = CHANNEL + 1
    await a_run(
        database, START - timedelta(days=30), channels=(CHANNEL, second)
    )
    await paced(database, 200, 100, 400)
    await paced(database, 201, 20, 100)
    await paced(database, 300, 100, 900, channel=second)

    summary = await run_weekly(database, now=LISTED)

    assert summary.second_wind_channels == 2
    [revival] = summary.revivals
    assert (revival.channel_id, revival.msg_id) == (second, 300)


async def test_a_channel_in_both_sections(database: Database) -> None:
    """Its new post on the list, its older post in the second wind."""
    await a_run(database, START - timedelta(days=30))
    await a_post(database, WEDNESDAY, msg_id=100)
    await read(database, WEDNESDAY + EARLY, msg_id=100, views=views_for(5.0))
    await paced(database, 200, 100, 400)

    summary = await run_weekly(database, now=LISTED)

    assert [(e.channel_id, e.msg_id) for e in summary.entries] == [
        (CHANNEL, 100)
    ]
    assert [(r.channel_id, r.msg_id) for r in summary.revivals] == [
        (CHANNEL, 200)
    ]


async def test_the_others_of_a_channel_are_counted(database: Database) -> None:
    """An hour apart: posts seconds apart would be one burst."""
    await a_run(database, START - timedelta(days=30))
    await paced(database, 200, 100, 400)
    await paced(
        database, 201, 20, 100, published=PUBLISHED + timedelta(hours=1)
    )

    summary = await run_weekly(database, now=LISTED)

    [revival] = summary.revivals
    assert (revival.msg_id, revival.others) == (200, 1)


async def test_switched_on_the_list_records_it(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "weekly_second_wind_enabled", True)
    await a_run(database, START - timedelta(days=30))
    await paced(database, 200, 100, 400)

    summary = await run_weekly(database, now=LISTED)

    assert await stored(database) == [(200, 0, True)]
    assert "shown" in summary.line()
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT second_wind_acceleration, second_wind_norm_share, "
                    "second_wind_size FROM tg_weekly_lists"
                )
            )
        ).one()
    assert tuple(row) == (3.0, 0.5, 5)


async def test_a_preview_prints_the_section_and_writes_nothing(
    database: Database,
) -> None:
    await a_run(database, START - timedelta(days=30))
    await paced(database, 200, 100, 400)

    summary = await run_weekly(database, now=LISTED, preview=True)

    lines = summary.preview()
    assert "  second wind (in the shadow):" in lines
    assert any(f"{CHANNEL} 200" in line and "x4.0" in line for line in lines)
    assert await stored(database) == []
