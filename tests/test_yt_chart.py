"""The chart reading: the request, the paging, and where readings live.

The request asks for each video's id, its title, its channel's id and
the token for the next page, and nothing else; the pages gather into
one reading in chart order; a reading is appended, never rewritten.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from ytfakes import FakeYouTube, channel_id, error_body, no_sleep, use_key

from itgraph.config import settings
from itgraph.db.models import YtCommand, YtRawChart
from itgraph.db.session import Database
from itgraph.db.yt_chart import charts_of_the_day, store_chart
from itgraph.yt import chart
from itgraph.yt.client import youtube

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


async def _read(
    fake: FakeYouTube,
    database: Database,
    region: str = "US",
    category: int | None = None,
) -> dict[str, object] | None:
    async with youtube(
        database, YtCommand.REFRESH, transport=fake.transport(), sleep=no_sleep
    ) as client:
        return await chart.read_chart(
            client,
            region,
            category=category
            if category is not None
            else settings.youtube_chart_category,
        )


def _chart_of(count: int, channel_offset: int = 0) -> list[tuple[str, str]]:
    """`count` chart entries, each video of its own channel."""
    return [
        (f"fakevideo{i:04d}", channel_id(channel_offset + i))
        for i in range(count)
    ]


# --- the request -------------------------------------------------------


async def test_the_chart_is_asked_for_ids_titles_and_the_token_only(
    database: Database,
) -> None:
    fake = FakeYouTube(charts={"US": _chart_of(3)})

    await _read(fake, database)

    [asked] = fake.calls("videos")
    assert asked == {
        "part": "snippet",
        "fields": "nextPageToken,items(id,snippet(channelId,title))",
        "chart": "mostPopular",
        "regionCode": "US",
        "videoCategoryId": "28",
        "maxResults": "50",
    }
    # No description, counts, category or publication time.
    for field in ("description", "statistics", "publishedAt"):
        assert field not in asked["part"] + asked["fields"]


async def test_the_defaults_are_two_regions_of_category_28() -> None:
    assert settings.youtube_chart_regions == ("US", "RU")
    assert settings.youtube_chart_category == 28


# --- the reading -------------------------------------------------------


async def test_the_pages_gather_into_one_reading_in_order(
    database: Database,
) -> None:
    """Four pages of fifty, 200 videos, as the probe of 2026-11-18
    measured: five requests for one reading, in chart order."""
    fake = FakeYouTube(charts={"US": _chart_of(200)})

    reading = await _read(fake, database)
    assert reading is not None

    assert len(fake.calls("videos")) == 4
    items = reading["items"]
    assert [item["snippet"]["channelId"] for item in items] == [
        channel_id(i) for i in range(200)
    ]
    # Asked for, so carried: each video's title travels with the
    # reading, for a card to name the charting video by.
    assert items[0]["snippet"]["title"] == "Fake video fakevideo0000"
    # The tokens are spent, not kept: nothing of the paging survives.
    assert "nextPageToken" not in reading


async def test_an_empty_chart_is_an_empty_reading(database: Database) -> None:
    fake = FakeYouTube(charts={"RU": []})

    reading = await _read(fake, database, region="RU")

    assert reading == {"items": []}


async def test_a_chart_youtube_names_none_is_no_reading(
    database: Database,
) -> None:
    """404 on the chart endpoint: the region is refused, not empty."""
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(404, json=error_body(404, "videoChartNotFound"))
    ]

    assert await _read(fake, database) is None


# --- where the readings live -------------------------------------------


async def _charts(database: Database) -> list[YtRawChart]:
    async with database.session() as session:
        return list(
            await session.scalars(
                select(YtRawChart).order_by(
                    YtRawChart.region, YtRawChart.fetched_at
                )
            )
        )


async def test_two_regions_two_readings(database: Database) -> None:
    async with database.session() as session:
        await store_chart(session, "US", {"items": []}, fetched_at=NOW)
        await store_chart(session, "RU", {"items": []}, fetched_at=NOW)

    [ru, us] = await _charts(database)  # RU sorts before US
    assert (us.region, ru.region) == ("US", "RU")


async def test_a_reading_is_never_rewritten(database: Database) -> None:
    async with database.session() as session:
        await store_chart(
            session,
            "RU",
            {"items": [{"id": "fake-old-video"}]},
            fetched_at=NOW - timedelta(days=1),
        )
        await store_chart(
            session,
            "RU",
            {"items": [{"id": "fake-new-video"}]},
            fetched_at=NOW,
        )

    [older, newer] = await _charts(database)
    assert older.fetched_at == NOW - timedelta(days=1)
    assert older.payload == {"items": [{"id": "fake-old-video"}]}
    assert newer.payload == {"items": [{"id": "fake-new-video"}]}


async def test_the_day_reads_each_regions_newest_of_the_day(
    database: Database,
) -> None:
    async with database.session() as session:
        await store_chart(
            session, "US", {"items": []}, fetched_at=NOW - timedelta(days=2)
        )
        await store_chart(
            session,
            "US",
            {"items": [{"id": "fake-morning"}]},
            fetched_at=NOW - timedelta(hours=2),
        )
        await store_chart(
            session,
            "US",
            {"items": [{"id": "fake-evening"}]},
            fetched_at=NOW - timedelta(hours=1),
        )
        await store_chart(
            session,
            "RU",
            {"items": [{"id": "fake-of-yesterday"}]},
            fetched_at=NOW - timedelta(days=1),
        )

    async with database.session() as session:
        of_the_day = await charts_of_the_day(
            session, ("US", "RU"), day_start=NOW - timedelta(hours=3)
        )

    # The day's newest of US, and RU's reading was the day before.
    assert set(of_the_day) == {"US"}
    assert of_the_day["US"] == {"items": [{"id": "fake-evening"}]}
