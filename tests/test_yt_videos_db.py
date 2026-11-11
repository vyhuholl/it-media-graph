"""The poll loop's videos and readings in the database.

What a reading is (counters at a moment, appended), what it is not (a
copy of the title), and how a video is forgotten (all of it, at once).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from ytfakes import channel_id, video_id, video_resource

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
    FoundVideo,
    VideoWrite,
    discoveries_since,
    due_videos,
    known_video_ids,
    remove_videos,
    store_found_videos,
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


async def test_the_walk_s_finds_are_not_counted_among_the_loop_s(
    database: Database,
) -> None:
    await _seed(database, 1)
    await _store(database, _write(1, via=YtDiscoveryPath.COUNT))
    async with database.session() as session:
        await store_found_videos(
            session,
            [_found(n) for n in range(2, 12)],
            fetched_at=NOW,
        )
        found = await discoveries_since(session, since=NOW - timedelta(days=7))

    assert found == {
        YtDiscoveryPath.FIRST: 0,
        YtDiscoveryPath.COUNT: 1,
        YtDiscoveryPath.CHECK: 0,
    }


def _found(
    number: int, *, reading: bool = False, due: datetime | None = None
) -> FoundVideo:
    return FoundVideo(
        video_id=video_id(number),
        payload=video_resource(number, published=NOW - timedelta(days=90)),
        due_at=due,
        reading=Counters(views=7, likes=1, comments=0) if reading else None,
    )


async def test_a_walk_never_touches_a_video_already_stored(
    database: Database,
) -> None:
    """A video the poll loop tracks keeps its payload, schedule and path."""
    await _seed(database, 1)
    due = NOW + timedelta(hours=2)
    await _store(database, _write(1, due=due, via=YtDiscoveryPath.COUNT))
    later = NOW + timedelta(hours=1)

    async with database.session() as session:
        stored = await store_found_videos(
            session, [_found(1, reading=True), _found(2)], fetched_at=later
        )

    assert stored == [video_id(2)]
    async with database.session() as session:
        video = await session.get(YtRawVideo, video_id(1))
        walked = await session.get(YtRawVideo, video_id(2))
    assert video is not None and walked is not None
    assert video.payload["snippet"]["title"] == TITLE
    assert (video.fetched_at, video.due_at) == (NOW, due)
    assert video.discovered_via is YtDiscoveryPath.COUNT
    assert walked.discovered_via is YtDiscoveryPath.BACKFILL
    assert (walked.discovered_at, walked.fetched_at) == (later, later)
    assert walked.due_at is None
    # The reading offered for the video the loop owns went nowhere.
    assert [r.observed_at for r in await _readings(database)] == [NOW]


async def test_a_walk_s_reading_goes_in_with_its_video(
    database: Database,
) -> None:
    await _seed(database, 1)

    async with database.session() as session:
        await store_found_videos(
            session, [_found(1, reading=True), _found(2)], fetched_at=NOW
        )

    readings = await _readings(database)
    assert [(r.video_id, r.observed_at, r.views) for r in readings] == [
        (video_id(1), NOW, 7)
    ]
