"""One pass of the YouTube poll loop, against a fake API and the test database.

Every scenario of the poll loop's spec that a pass can show: which
channels are asked about, when uploads are listed, what counts as new,
when a video is read, what a reading records, and what happens to a
video YouTube stops returning. The clock is fixed, so a scenario states
a moment rather than waiting for one; time moves by passing another.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    upload_item,
    uploads_id,
    use_key,
    video_id,
    video_resource,
)

from itgraph.db.models import (
    Base,
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtChannelPoll,
    YtCommand,
    YtDiscoveryPath,
    YtLang,
    YtPassOutcome,
    YtRawChannel,
    YtRawVideo,
    YtRawVideoStat,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_videos import VideoWrite, store_videos
from itgraph.db.yt_watch import PassRecord, record_listings
from itgraph.derive.yt_videos import Counters
from itgraph.yt import watch as watch_module
from itgraph.yt.client import youtube
from itgraph.yt.watch import watch_pass

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


# --- arranging -------------------------------------------------------------


async def seeds(
    database: Database,
    *numbers: int,
    videos: int = 42,
    listed_at: datetime | None = None,
    lang: YtLang = YtLang.EN,
) -> None:
    """Seed channels with stored payloads, optionally already listed."""
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in numbers])
        await store_payloads(
            session,
            {
                n_id: channel_resource(n, videos=videos)
                for n, n_id in _ids(numbers)
            },
            fetched_at=NOW - timedelta(days=1),
        )
        for n in numbers:
            record = await session.get(YtChannel, channel_id(n))
            assert record is not None
            record.status = ChannelStatus.SEED
            record.kind = YtChannelKind.PERSONAL
            record.lang = lang
        if listed_at is not None:
            await record_listings(
                session, [channel_id(n) for n in numbers], now=listed_at
            )


def _ids(numbers: tuple[int, ...]) -> list[tuple[int, str]]:
    return [(n, channel_id(n)) for n in numbers]


async def tracked(
    database: Database,
    number: int,
    *,
    published: datetime,
    due: datetime | None,
    channel: int = 1,
    at: datetime | None = None,
    **resource: Any,
) -> None:
    """A video already being tracked, as an earlier pass would leave it."""
    async with database.session() as session:
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(number),
                    payload=video_resource(
                        number,
                        published=published,
                        channel=channel,
                        **resource,
                    ),
                    due_at=due,
                    reading=Counters(views=1),
                    discovered_via=YtDiscoveryPath.FIRST,
                )
            ],
            observed_at=at or published + timedelta(minutes=5),
        )


def api(
    *numbers: int,
    videos: int = 42,
    uploads: dict[int, list[dict[str, Any]]] | None = None,
    resources: list[dict[str, Any]] | None = None,
    batch_404: bool = False,
) -> FakeYouTube:
    """YouTube as these channels, their uploads and these videos."""
    return FakeYouTube(
        *(channel_resource(n, videos=videos) for n in numbers),
        uploads={uploads_id(n): items for n, items in (uploads or {}).items()},
        videos=resources or [],
        batch_404=batch_404,
    )


@asynccontextmanager
async def connected(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[Any]:
    async with youtube(
        database, YtCommand.WATCH, transport=fake.transport(), sleep=no_sleep
    ) as client:
        yield client


async def one_pass(
    database: Database, fake: FakeYouTube, *, at: datetime = NOW
) -> PassRecord:
    clock: Callable[[], datetime] = lambda: at
    async with connected(database, fake) as client:
        return await watch_pass(client, database, clock=clock)


# --- reading the outcome ---------------------------------------------------


async def video(database: Database, number: int) -> YtRawVideo | None:
    async with database.session() as session:
        return await session.get(YtRawVideo, video_id(number))


async def readings(database: Database, number: int) -> list[YtRawVideoStat]:
    async with database.session() as session:
        rows = await session.scalars(
            select(YtRawVideoStat)
            .where(YtRawVideoStat.video_id == video_id(number))
            .order_by(YtRawVideoStat.observed_at)
        )
        return list(rows)


async def poll(database: Database, number: int) -> YtChannelPoll | None:
    async with database.session() as session:
        return await session.get(YtChannelPoll, channel_id(number))


async def anywhere(database: Database, needle: str) -> list[str]:
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


# --- watched channels ------------------------------------------------------


async def test_seeds_of_every_language_are_watched(database: Database) -> None:
    await seeds(database, 1, listed_at=NOW, lang=YtLang.EN)
    await seeds(database, 2, listed_at=NOW, lang=YtLang.RU)
    await seeds(database, 3, listed_at=NOW, lang=YtLang.OTHER)
    fake = api(1, 2, 3)

    record = await one_pass(database, fake)

    assert record.channels == 3
    [asked] = fake.calls("channels")
    assert asked["id"].split(",") == [channel_id(n) for n in (1, 2, 3)]


async def test_undecided_and_rejected_channels_are_not_watched(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(
            session, [channel_id(1), channel_id(2), channel_id(3)]
        )
        maybe = await session.get(YtChannel, channel_id(2))
        rejected = await session.get(YtChannel, channel_id(3))
        assert maybe is not None and rejected is not None
        maybe.status = ChannelStatus.MAYBE
        rejected.status = ChannelStatus.REJECTED
        rejected.reject_reason = YtRejectReason.NOT_IT
    fake = api(1, 2, 3)

    record = await one_pass(database, fake)

    assert record.channels == 0
    assert fake.requests == []


async def test_a_channel_taken_out_of_scope_is_left_alone(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW - timedelta(hours=30))
    await tracked(
        database,
        1,
        published=NOW - timedelta(hours=3),
        due=NOW - timedelta(minutes=1),
    )
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.MAYBE
    fake = api(1, resources=[video_resource(1, published=NOW)])

    await one_pass(database, fake)

    assert fake.calls("playlistItems") == []
    assert fake.calls("videos") == []


# --- detection -------------------------------------------------------------


async def test_a_hundred_channels_share_two_requests(
    database: Database,
) -> None:
    await seeds(database, *range(100), listed_at=NOW)
    fake = api(*range(100))

    await one_pass(database, fake)

    assert len(fake.calls("channels")) == 2
    assert fake.calls("playlistItems") == []


async def test_a_risen_or_fallen_count_is_listed(database: Database) -> None:
    await seeds(database, 1, 2, 3, videos=42, listed_at=NOW)
    fake = FakeYouTube(
        channel_resource(1, videos=43),
        channel_resource(2, videos=41),
        channel_resource(3, videos=42),
        uploads={uploads_id(n): [] for n in (1, 2, 3)},
    )

    record = await one_pass(database, fake, at=NOW + timedelta(minutes=15))

    listed = {call["playlistId"] for call in fake.calls("playlistItems")}
    assert listed == {uploads_id(1), uploads_id(2)}
    assert record.listed == 2


async def test_a_channel_without_a_stored_count_is_listed(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    async with database.session() as session:
        stored = await session.get(YtRawChannel, channel_id(1))
        assert stored is not None
        await session.delete(stored)
    fake = api(1, uploads={1: []})

    await one_pass(database, fake, at=NOW + timedelta(minutes=15))

    assert len(fake.calls("playlistItems")) == 1


async def test_the_channel_data_is_refreshed_in_passing(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)

    await one_pass(database, api(1), at=NOW + timedelta(minutes=15))

    async with database.session() as session:
        stored = await session.get(YtRawChannel, channel_id(1))
    assert stored is not None
    assert stored.fetched_at == NOW + timedelta(minutes=15)


async def test_the_poll_loop_finds_a_channel_gone(database: Database) -> None:
    await seeds(database, 1, 2, listed_at=NOW)

    await one_pass(database, api(1), at=NOW + timedelta(minutes=15))

    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(2))
        payload = await session.get(YtRawChannel, channel_id(2))
    assert record is not None and record.gone_at is not None
    assert payload is None


# --- listing ---------------------------------------------------------------


async def test_a_quiet_channel_is_still_checked_daily(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW - timedelta(hours=25))
    published = NOW - timedelta(hours=20)
    fake = api(
        1,
        uploads={1: [upload_item(7, published=published)]},
        resources=[video_resource(7, published=published)],
    )

    record = await one_pass(database, fake)

    assert len(fake.calls("playlistItems")) == 1
    found = await video(database, 7)
    assert found is not None
    assert found.discovered_via is YtDiscoveryPath.CHECK
    assert found.discovered_at == NOW
    assert record.new_videos == 1


async def test_a_listing_caused_by_the_count_postpones_the_check(
    database: Database,
) -> None:
    await seeds(database, 1, videos=42, listed_at=NOW - timedelta(days=3))
    moved = FakeYouTube(
        channel_resource(1, videos=43), uploads={uploads_id(1): []}
    )
    await one_pass(database, moved, at=NOW - timedelta(hours=2))

    later = api(1, videos=43, uploads={1: []})
    await one_pass(database, later)

    assert later.calls("playlistItems") == []


async def test_a_first_listing_brings_the_recent_videos_only(
    database: Database,
) -> None:
    await seeds(database, 1)
    dates = {
        2: NOW - timedelta(days=2),
        10: NOW - timedelta(days=10),
        40: NOW - timedelta(days=40),
    }
    fake = api(
        1,
        uploads={1: [upload_item(n, published=d) for n, d in dates.items()]},
        resources=[
            video_resource(n, published=d, title=f"Title {n}")
            for n, d in dates.items()
        ],
    )

    record = await one_pass(database, fake)

    assert record.new_videos == 2
    first = await video(database, 2)
    assert first is not None and first.discovered_via is YtDiscoveryPath.FIRST
    assert await video(database, 10) is not None
    assert await anywhere(database, video_id(40)) == []
    # The forty-day one was never even asked about.
    assert video_id(40) not in fake.calls("videos")[0]["id"]


async def test_a_video_older_than_the_horizon_is_not_taken_for_new(
    database: Database,
) -> None:
    """Its data expired; a listing shows it again; it stays forgotten."""
    await seeds(database, 1, listed_at=NOW - timedelta(days=2))
    old = NOW - timedelta(days=60)
    fake = api(
        1,
        uploads={1: [upload_item(5, published=old)]},
        resources=[video_resource(5, published=old)],
    )

    record = await one_pass(database, fake)

    assert record.new_videos == 0
    assert await video(database, 5) is None


async def test_a_channel_that_cannot_be_listed_does_not_stop_the_pass(
    database: Database,
) -> None:
    await seeds(database, 1, 2)
    published = NOW - timedelta(hours=1)
    # Channel 1's playlist is not there; channel 2's is.
    fake = api(
        1,
        2,
        uploads={2: [upload_item(9, published=published)]},
        resources=[video_resource(9, published=published, channel=2)],
    )

    record = await one_pass(database, fake)

    assert record.list_failures == 1
    assert await video(database, 9) is not None
    failing = await poll(database, 1)
    assert failing is not None and failing.consecutive_failures == 1
    assert failing.listed_at is None


async def test_a_pass_dying_between_listing_and_reading_still_owes(
    database: Database,
) -> None:
    await seeds(database, 1, videos=42, listed_at=NOW - timedelta(hours=1))
    published = NOW - timedelta(minutes=10)
    fake = FakeYouTube(
        channel_resource(1, videos=43),
        uploads={uploads_id(1): [upload_item(3, published=published)]},
    )
    # Detection and listing succeed; every attempt at the videos fails.
    fake.queued = [
        httpx.Response(200, json={"items": [channel_resource(1, videos=43)]}),
        httpx.Response(
            200, json={"items": [upload_item(3, published=published)]}
        ),
        *(
            httpx.Response(500, json=error_body(500, "backendError"))
            for _ in range(3)
        ),
    ]

    record = await one_pass(database, fake)

    assert record.outcome is YtPassOutcome.FAILED
    owed = await poll(database, 1)
    assert owed is not None and owed.list_owed

    # The next pass lists the channel again although its count is "seen".
    retry = api(
        1,
        videos=43,
        uploads={1: [upload_item(3, published=published)]},
        resources=[video_resource(3, published=published)],
    )
    await one_pass(database, retry, at=NOW + timedelta(minutes=15))
    assert len(retry.calls("playlistItems")) == 1
    assert await video(database, 3) is not None
    paid = await poll(database, 1)
    assert paid is not None and not paid.list_owed


# --- reading ---------------------------------------------------------------


async def test_discovery_takes_the_first_reading(database: Database) -> None:
    await seeds(database, 1)
    published = NOW - timedelta(minutes=7)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=published)]},
        resources=[video_resource(1, published=published, views=55)],
    )

    record = await one_pass(database, fake)

    [reading] = await readings(database, 1)
    assert (reading.observed_at, reading.views) == (NOW, 55)
    assert len(fake.calls("videos")) == 1
    assert record.readings == 1
    found = await video(database, 1)
    assert found is not None and found.due_at == published + timedelta(hours=1)


async def test_a_reading_refreshes_the_stored_video(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(hours=3)
    await tracked(
        database, 1, published=published, due=NOW - timedelta(minutes=1)
    )
    fake = api(
        1, resources=[video_resource(1, published=published, views=900)]
    )

    await one_pass(database, fake)

    found = await video(database, 1)
    assert found is not None
    assert found.fetched_at == NOW
    assert found.payload["statistics"]["viewCount"] == "900"
    assert found.due_at == published + timedelta(hours=4)
    assert [r.views for r in await readings(database, 1)] == [1, 900]


async def test_videos_of_different_channels_share_requests(
    database: Database,
) -> None:
    await seeds(database, 1, 2, 3, listed_at=NOW)
    published = NOW - timedelta(hours=3)
    for n in range(60):
        await tracked(
            database, n, published=published, due=NOW, channel=1 + n % 3
        )
    fake = api(
        1,
        2,
        3,
        resources=[
            video_resource(n, published=published, channel=1 + n % 3)
            for n in range(60)
        ],
    )

    record = await one_pass(database, fake)

    assert len(fake.calls("videos")) == 2
    assert record.readings == 60


async def test_a_long_gap_produces_no_backlog(database: Database) -> None:
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(hours=10)
    # Due at 2 h; the passes that should have read it at 2, 4 and 8 h
    # never ran.
    await tracked(
        database, 1, published=published, due=published + timedelta(hours=2)
    )
    fake = api(1, resources=[video_resource(1, published=published)])

    await one_pass(database, fake)

    assert len(await readings(database, 1)) == 2
    found = await video(database, 1)
    assert found is not None and found.due_at == published + timedelta(
        hours=12
    )


async def test_reading_ends_at_the_horizon(database: Database) -> None:
    """Its last reading was missed; it is closed, not read late."""
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(days=29, hours=6)
    await tracked(
        database, 1, published=published, due=published + timedelta(days=28)
    )
    fake = api(1, resources=[video_resource(1, published=published)])

    await one_pass(database, fake)

    assert fake.calls("videos") == []
    found = await video(database, 1)
    assert found is not None and found.due_at is None


async def test_a_finished_stream_is_aged_from_its_start(
    database: Database,
) -> None:
    await seeds(database, 1)
    published = NOW - timedelta(hours=5)
    went_live = NOW - timedelta(minutes=30)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=published)]},
        resources=[
            video_resource(1, published=published, live_started=went_live)
        ],
    )

    await one_pass(database, fake)

    found = await video(database, 1)
    assert found is not None and found.due_at == went_live + timedelta(hours=1)


async def test_a_scheduled_stream_is_tracked_but_not_read(
    database: Database,
) -> None:
    await seeds(database, 1)
    tomorrow = NOW + timedelta(days=1)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=None)]},
        resources=[
            video_resource(
                1, published=NOW, broadcast="upcoming", scheduled=tomorrow
            )
        ],
    )

    record = await one_pass(database, fake)

    assert record.new_videos == 1
    assert await readings(database, 1) == []
    found = await video(database, 1)
    assert found is not None and found.due_at == tomorrow


async def test_the_stream_starts(database: Database) -> None:
    await seeds(database, 1, listed_at=NOW)
    scheduled = NOW - timedelta(minutes=20)
    async with database.session() as session:
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(1),
                    payload=video_resource(
                        1,
                        published=NOW - timedelta(days=1),
                        broadcast="upcoming",
                        scheduled=scheduled,
                    ),
                    due_at=scheduled,
                    reading=None,
                    discovered_via=YtDiscoveryPath.COUNT,
                )
            ],
            observed_at=NOW - timedelta(days=1),
        )
    went_live = NOW - timedelta(minutes=10)
    fake = api(
        1,
        resources=[
            video_resource(
                1,
                published=NOW - timedelta(days=1),
                broadcast="live",
                scheduled=scheduled,
                live_started=went_live,
            )
        ],
    )

    await one_pass(database, fake)

    assert len(await readings(database, 1)) == 1
    found = await video(database, 1)
    assert found is not None and found.due_at == went_live + timedelta(hours=1)
    assert found.discovered_via is YtDiscoveryPath.COUNT


async def test_a_stream_that_never_starts_is_given_up(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    scheduled = NOW - timedelta(days=7)
    upcoming = video_resource(
        1,
        published=NOW - timedelta(days=8),
        broadcast="upcoming",
        scheduled=scheduled,
    )
    async with database.session() as session:
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(1),
                    payload=upcoming,
                    due_at=NOW - timedelta(minutes=5),
                    reading=None,
                    discovered_via=YtDiscoveryPath.CHECK,
                )
            ],
            observed_at=NOW - timedelta(hours=1),
        )

    await one_pass(database, api(1, resources=[upcoming]))

    found = await video(database, 1)
    assert found is not None and found.due_at is None
    assert await readings(database, 1) == []


async def test_a_short_video_is_tracked_like_any_other(
    database: Database,
) -> None:
    await seeds(database, 1)
    published = NOW - timedelta(minutes=3)
    short = video_resource(1, published=published)
    short["contentDetails"]["duration"] = "PT40S"
    fake = api(
        1,
        uploads={1: [upload_item(1, published=published)]},
        resources=[short],
    )

    await one_pass(database, fake)

    found = await video(database, 1)
    assert found is not None and found.due_at == published + timedelta(hours=1)
    assert len(await readings(database, 1)) == 1
    columns = set(YtRawVideo.__table__.columns.keys())
    assert columns == {
        "video_id",
        "channel_id",
        "payload",
        "fetched_at",
        "discovered_at",
        "discovered_via",
        "due_at",
    }


async def test_a_video_moved_to_an_unwatched_channel(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(hours=3)
    await tracked(database, 1, published=published, due=NOW)
    fake = api(
        1, resources=[video_resource(1, published=published, channel=8)]
    )

    await one_pass(database, fake)

    assert len(await readings(database, 1)) == 2
    found = await video(database, 1)
    assert found is not None
    assert found.channel_id == channel_id(8)
    assert found.due_at is None


# --- pace ------------------------------------------------------------------


async def test_ahead_of_pace_the_oldest_work_waits(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch_module, "pace_allows", lambda *args: False)
    await seeds(database, 1, videos=42, listed_at=NOW - timedelta(days=2))
    await seeds(database, 2, videos=42, listed_at=NOW - timedelta(days=2))
    young = NOW - timedelta(hours=3)
    old = NOW - timedelta(days=8)
    await tracked(database, 1, published=young, due=NOW)
    await tracked(database, 2, published=old, due=NOW)
    fresh = NOW - timedelta(minutes=5)
    fake = FakeYouTube(
        channel_resource(1, videos=43),
        channel_resource(2, videos=42),
        uploads={
            uploads_id(1): [upload_item(3, published=fresh)],
            uploads_id(2): [],
        },
        videos=[
            video_resource(1, published=young),
            video_resource(2, published=old),
            video_resource(3, published=fresh),
        ],
    )

    record = await one_pass(database, fake)

    # Channel 1's count moved and is listed; channel 2's daily check waits.
    assert [c["playlistId"] for c in fake.calls("playlistItems")] == [
        uploads_id(1)
    ]
    asked = fake.calls("videos")[0]["id"].split(",")
    assert video_id(3) in asked and video_id(1) in asked
    assert video_id(2) not in asked
    assert (record.deferred_checks, record.deferred_readings) == (1, 1)


# --- disappearing videos ---------------------------------------------------


async def test_a_deleted_or_private_video_is_removed_with_its_readings(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(hours=3)
    await tracked(
        database, 1, published=published, due=NOW, title="Gone Title"
    )

    record = await one_pass(database, api(1))

    assert record.removed == 1
    assert await anywhere(database, video_id(1)) == []
    assert await anywhere(database, "Gone Title") == []


async def test_one_missing_video_does_not_cost_the_others(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(hours=3)
    for n in range(50):
        await tracked(database, n, published=published, due=NOW)
    fake = api(
        1,
        resources=[
            video_resource(n, published=published) for n in range(1, 50)
        ],
    )

    record = await one_pass(database, fake)

    assert record.readings == 49
    assert record.removed == 1
    assert await video(database, 0) is None


async def test_a_batch_answered_not_found_is_asked_id_by_id(
    database: Database,
) -> None:
    await seeds(database, 1, listed_at=NOW)
    published = NOW - timedelta(hours=3)
    for n in (1, 2, 3):
        await tracked(database, n, published=published, due=NOW)
    fake = api(
        1,
        resources=[video_resource(n, published=published) for n in (1, 3)],
        batch_404=True,
    )

    record = await one_pass(database, fake)

    assert len(fake.calls("videos")) == 4
    assert record.removed == 1
    assert await video(database, 2) is None
    assert await video(database, 1) is not None


# --- what a pass writes ----------------------------------------------------


async def test_a_pass_logs_ids_and_nothing_youtube_said(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await seeds(database, 1)
    published = NOW - timedelta(minutes=5)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=published)]},
        resources=[
            video_resource(1, published=published, title="Secret Log Title")
        ],
    )

    await one_pass(database, fake)

    assert video_id(1) in caplog.text
    assert "Secret Log Title" not in caplog.text
    assert "An invented video" not in caplog.text
    assert "Fake Channel 1" not in caplog.text


async def test_the_loop_derives_nothing(database: Database) -> None:
    await seeds(database, 1)
    published = NOW - timedelta(minutes=5)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=published)]},
        resources=[video_resource(1, published=published)],
    )

    await one_pass(database, fake)

    written = set()
    async with database.session() as session:
        for table in Base.metadata.sorted_tables:
            count = await session.scalar(
                select(func.count()).select_from(table)
            )
            if count:
                written.add(table.name)
    assert written == {
        "yt_channels",
        "yt_raw_channels",
        "yt_channel_polls",
        "yt_raw_videos",
        "yt_raw_video_stats",
        "yt_quota_days",
        "yt_requests",
    }
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
    assert record is not None and record.status is ChannelStatus.SEED


async def test_a_listed_video_gone_by_the_time_it_is_asked_for(
    database: Database,
) -> None:
    """Listed, then private before the read: nothing is stored, the debt
    is paid all the same."""
    await seeds(database, 1)
    published = NOW - timedelta(minutes=5)
    fake = api(1, uploads={1: [upload_item(1, published=published)]})

    record = await one_pass(database, fake)

    assert record.new_videos == 0
    assert await anywhere(database, video_id(1)) == []
    paid = await poll(database, 1)
    assert paid is not None and paid.listed_at == NOW


async def test_a_channel_whose_data_names_no_uploads_playlist(
    database: Database,
) -> None:
    await seeds(database, 1)
    resource = channel_resource(1)
    del resource["contentDetails"]
    fake = FakeYouTube(resource)

    record = await one_pass(database, fake)

    assert record.list_failures == 1
    assert fake.calls("playlistItems") == []
    failing = await poll(database, 1)
    assert failing is not None
    assert failing.last_error == "no uploads playlist in its stored data"


async def test_a_broadcast_the_listing_dates_before_the_horizon(
    database: Database,
) -> None:
    """The accepted risk, pinned: the listing's date decides, unasked."""
    await seeds(database, 1)
    old = NOW - timedelta(days=40)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=old)]},
        resources=[
            video_resource(
                1,
                published=old,
                broadcast="upcoming",
                scheduled=NOW + timedelta(days=1),
            )
        ],
    )

    record = await one_pass(database, fake)

    assert record.new_videos == 0
    assert fake.calls("videos") == []
    assert await anywhere(database, video_id(1)) == []


async def test_a_stream_scheduled_far_ahead_keeps_its_data_current(
    database: Database,
) -> None:
    """Due again at the refresh age, not at a start 40 days away — or its
    data would expire before the stream began."""
    await seeds(database, 1)
    fake = api(
        1,
        uploads={1: [upload_item(1, published=None)]},
        resources=[
            video_resource(
                1,
                published=NOW,
                broadcast="upcoming",
                scheduled=NOW + timedelta(days=40),
            )
        ],
    )

    await one_pass(database, fake)

    found = await video(database, 1)
    assert found is not None
    assert found.due_at == NOW + timedelta(days=25)
    assert await readings(database, 1) == []
