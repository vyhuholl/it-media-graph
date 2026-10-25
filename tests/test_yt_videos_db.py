"""The poll loop's videos and readings in the database.

What a reading is (counters at a moment, appended), what it is not (a
copy of the title), how a video is forgotten (all of it, at once), and
how long each piece lives — 30 days, or longer for readings only once
an acceptance date is recorded.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from ytfakes import channel_id, video_id, video_resource

from itgraph import yt_policy
from itgraph.db.models import (
    Base,
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtDiscoveryPath,
    YtLang,
    YtRawVideo,
    YtRawVideoStat,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels
from itgraph.db.yt_videos import (
    VideoWrite,
    discoveries_since,
    due_videos,
    known_video_ids,
    purge_expired_videos,
    remove_videos,
    store_videos,
)
from itgraph.derive.yt_videos import Counters

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
TITLE = "A Fake Video Title Nobody Else Has"


async def _seed(database: Database, *numbers: int) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in numbers])
        for n in numbers:
            record = await session.get(YtChannel, channel_id(n))
            assert record is not None
            record.status = ChannelStatus.SEED
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN


def _write(
    number: int,
    *,
    channel: int = 1,
    due: datetime | None = None,
    views: int | None = 100,
    reading: bool = True,
    via: YtDiscoveryPath = YtDiscoveryPath.FIRST,
) -> VideoWrite:
    payload = video_resource(
        number, published=NOW, channel=channel, title=TITLE, views=views
    )
    return VideoWrite(
        video_id=video_id(number),
        payload=payload,
        due_at=due,
        reading=Counters(views=views, likes=10, comments=3)
        if reading
        else None,
        discovered_via=via,
    )


async def _store(
    database: Database, *writes: VideoWrite, at: datetime = NOW
) -> int:
    async with database.session() as session:
        return await store_videos(session, writes, observed_at=at)


async def _readings(database: Database) -> list[YtRawVideoStat]:
    async with database.session() as session:
        rows = await session.scalars(
            select(YtRawVideoStat).order_by(YtRawVideoStat.observed_at)
        )
        return list(rows)


async def _anywhere(database: Database, needle: str) -> list[str]:
    """Every table holding ``needle`` anywhere in any row."""
    holding = []
    async with database.session() as session:
        for table in Base.metadata.sorted_tables:
            found = await session.scalar(
                text(
                    f'SELECT count(*) FROM "{table.name}" t '
                    "WHERE row_to_json(t)::text LIKE :needle"
                ),
                {"needle": f"%{needle}%"},
            )
            if found:
                holding.append(table.name)
    return holding


async def test_one_answer_is_one_moment(database: Database) -> None:
    await _seed(database, 1)

    stored = await _store(database, _write(1), _write(2))

    assert stored == 2
    readings = await _readings(database)
    assert {reading.observed_at for reading in readings} == {NOW}


async def test_a_second_reading_is_a_second_row(database: Database) -> None:
    await _seed(database, 1)
    await _store(database, _write(1, views=100))
    later = NOW + timedelta(hours=1)

    await _store(
        database, _write(1, views=250, via=YtDiscoveryPath.CHECK), at=later
    )

    readings = await _readings(database)
    assert [(r.observed_at, r.views) for r in readings] == [
        (NOW, 100),
        (later, 250),
    ]
    async with database.session() as session:
        video = await session.get(YtRawVideo, video_id(1))
    assert video is not None
    # The payload is the latest; how it was found is the first answer's.
    assert video.fetched_at == later
    assert video.payload["statistics"]["viewCount"] == "250"
    assert video.discovered_at == NOW
    assert video.discovered_via is YtDiscoveryPath.FIRST


async def test_an_absent_counter_is_stored_as_absent(
    database: Database,
) -> None:
    await _seed(database, 1)

    await _store(database, _write(1, views=None))

    [reading] = await _readings(database)
    assert reading.views is None
    assert reading.likes == 10


async def test_counts_beyond_two_billion_are_stored_exactly(
    database: Database,
) -> None:
    await _seed(database, 1)

    await _store(database, _write(1, views=3_000_000_000))

    [reading] = await _readings(database)
    assert reading.views == 3_000_000_000


async def test_an_upcoming_broadcast_stores_no_reading(
    database: Database,
) -> None:
    await _seed(database, 1)

    stored = await _store(database, _write(1, reading=False))

    assert stored == 0
    assert await _readings(database) == []
    async with database.session() as session:
        assert await known_video_ids(session, [video_id(1)]) == {video_id(1)}


async def test_a_reading_holds_counters_only(database: Database) -> None:
    await _seed(database, 1)
    await _store(database, _write(1))

    columns = set(YtRawVideoStat.__table__.columns.keys())
    assert columns == {"video_id", "observed_at", "views", "likes", "comments"}
    assert "yt_raw_video_stats" not in await _anywhere(database, TITLE)


async def test_removing_a_video_removes_everything_about_it(
    database: Database,
) -> None:
    await _seed(database, 1)
    await _store(database, _write(1, due=NOW + timedelta(hours=1)))
    await _store(database, _write(1), at=NOW + timedelta(hours=1))

    async with database.session() as session:
        removed = await remove_videos(session, [video_id(1), video_id(9)])

    assert removed == [video_id(1)]
    assert await _anywhere(database, TITLE) == []
    assert await _anywhere(database, video_id(1)) == []


async def test_only_seed_channels_have_due_videos(database: Database) -> None:
    await _seed(database, 1)
    async with database.session() as session:
        await create_channels(session, [channel_id(2)])
    due = NOW - timedelta(minutes=1)
    await _store(
        database,
        _write(1, channel=1, due=due),
        _write(2, channel=2, due=due),
        _write(3, channel=1, due=NOW + timedelta(hours=1)),
        _write(4, channel=1, due=None),
    )

    async with database.session() as session:
        found = await due_videos(session, now=NOW)

    assert [video.video_id for video in found] == [video_id(1)]


async def test_discoveries_are_counted_by_path(database: Database) -> None:
    await _seed(database, 1)
    await _store(
        database,
        _write(1, via=YtDiscoveryPath.COUNT),
        _write(2, via=YtDiscoveryPath.COUNT),
        _write(3, via=YtDiscoveryPath.CHECK),
    )

    async with database.session() as session:
        found = await discoveries_since(session, since=NOW - timedelta(days=7))

    assert found == {
        YtDiscoveryPath.FIRST: 0,
        YtDiscoveryPath.COUNT: 2,
        YtDiscoveryPath.CHECK: 1,
    }


async def test_expired_videos_and_readings_are_removed(
    database: Database,
) -> None:
    await _seed(database, 1)
    await _store(database, _write(1), at=NOW - timedelta(days=31))
    await _store(database, _write(2), at=NOW - timedelta(days=31))
    await _store(database, _write(2), at=NOW - timedelta(days=1))
    await _store(database, _write(3), at=NOW - timedelta(days=29))

    async with database.session() as session:
        videos, readings = await purge_expired_videos(session, now=NOW)

    # Video 1 expired whole. Video 2 lost its old reading and kept the
    # rest; video 3 is untouched.
    assert (videos, readings) == (1, 2)
    async with database.session() as session:
        assert await known_video_ids(
            session, [video_id(1), video_id(2), video_id(3)]
        ) == {video_id(2), video_id(3)}
        left = await session.scalar(
            select(func.count()).select_from(YtRawVideoStat)
        )
    assert left == 2


async def test_after_acceptance_readings_from_that_date_are_kept(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepted = (NOW - timedelta(days=31)).date()
    monkeypatch.setattr(yt_policy, "DERIVED_METRICS_ACCEPTED_ON", accepted)
    await _seed(database, 1)
    await _store(database, _write(1), at=NOW - timedelta(days=32))
    await _store(database, _write(1), at=NOW - timedelta(days=31))
    # Kept fetched, as `yt refresh` keeps it after acceptance.
    await _store(database, _write(1, reading=False), at=NOW)

    async with database.session() as session:
        await purge_expired_videos(session, now=NOW)

    readings = await _readings(database)
    assert [reading.observed_at for reading in readings] == [
        NOW - timedelta(days=31)
    ]


async def test_thirty_six_months_is_the_limit(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        yt_policy, "DERIVED_METRICS_ACCEPTED_ON", date(2023, 1, 1)
    )
    await _seed(database, 1)
    await _store(database, _write(1), at=datetime(2023, 10, 25, tzinfo=UTC))
    await _store(database, _write(1), at=datetime(2023, 10, 27, tzinfo=UTC))
    await _store(database, _write(1, reading=False), at=NOW)

    async with database.session() as session:
        await purge_expired_videos(session, now=NOW)

    readings = await _readings(database)
    assert [reading.observed_at for reading in readings] == [
        datetime(2023, 10, 27, tzinfo=UTC)
    ]


async def test_titles_do_not_follow_the_readings(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After acceptance a payload still goes at 30 days — and its readings
    with it, which is why refresh keeps such videos fetched."""
    monkeypatch.setattr(
        yt_policy, "DERIVED_METRICS_ACCEPTED_ON", date(2026, 9, 1)
    )
    await _seed(database, 1)
    await _store(database, _write(1), at=NOW - timedelta(days=31))

    async with database.session() as session:
        videos, _ = await purge_expired_videos(session, now=NOW)

    assert videos == 1
    assert await _anywhere(database, TITLE) == []
