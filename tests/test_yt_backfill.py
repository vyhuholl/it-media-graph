"""The history walk, against a fake API and the test database.

Every scenario of the walk's spec a run can show: how deep it reaches,
where it stops, what it counts, what it stores and leaves to the poll
loop, what it reads, and how it stops and resumes. The clock is fixed, so a scenario states a moment.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    stamp,
    upload_item,
    uploads_id,
    use_key,
    video_id,
    video_resource,
)

from itgraph.config import settings
from itgraph.db.models import (
    Base,
    ChannelStatus,
    YtBackfillWalk,
    YtChannel,
    YtChannelKind,
    YtCommand,
    YtDiscoveryPath,
    YtLang,
    YtRawVideo,
    YtRawVideoStat,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_quota import (
    FloorReachedError,
    PoolSpentError,
    QuotaExhaustedError,
    book,
    record_exhaustion,
)
from itgraph.db.yt_videos import VideoWrite, store_videos
from itgraph.derive.yt_videos import Counters
from itgraph.yt import client as yt_client
from itgraph.yt.backfill import WalkReport, run_walk, walk_channels
from itgraph.yt.client import youtube
from itgraph.yt.errors import ApiError, RequestLimitReached
from itgraph.yt.watch import watch_pass

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


# --- arranging -------------------------------------------------------------


async def seeds(database: Database, *numbers: int) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in numbers])
        await store_payloads(
            session,
            {channel_id(n): channel_resource(n) for n in numbers},
            fetched_at=NOW - DAY,
        )
        for n in numbers:
            record = await session.get(YtChannel, channel_id(n))
            assert record is not None
            record.status = ChannelStatus.SEED
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN


def listing(*ages: float | None, start: int = 0) -> list[dict[str, Any]]:
    """Upload items numbered from ``start``, dated that many days ago."""
    return [
        upload_item(
            start + offset,
            published=None if age is None else NOW - timedelta(days=age),
        )
        for offset, age in enumerate(ages)
    ]


def resources(
    *ages: float, start: int = 0, **extra: Any
) -> list[dict[str, Any]]:
    """Video resources matching ``listing``'s numbers and dates."""
    return [
        video_resource(
            start + offset, published=NOW - timedelta(days=age), **extra
        )
        for offset, age in enumerate(ages)
    ]


def api(
    uploads: dict[int, list[dict[str, Any]]],
    videos: list[dict[str, Any]],
) -> FakeYouTube:
    return FakeYouTube(
        uploads={uploads_id(n): items for n, items in uploads.items()},
        videos=videos,
    )


@asynccontextmanager
async def connected(
    database: Database, fake: FakeYouTube, *, limit: int | None = None
) -> AsyncIterator[Any]:
    async with youtube(
        database,
        YtCommand.BACKFILL,
        transport=fake.transport(),
        sleep=no_sleep,
        limit=limit,
    ) as client:
        yield client


async def walk(
    database: Database,
    fake: FakeYouTube,
    *,
    named: list[str] | None = None,
    whole: bool = False,
    limit: int | None = None,
    at: datetime = NOW,
) -> WalkReport:
    clock: Callable[[], datetime] = lambda: at
    async with connected(database, fake, limit=limit) as client:
        return await walk_channels(
            client, database, named=named, whole=whole, clock=clock
        )


# --- reading the outcome ---------------------------------------------------


async def stored(database: Database) -> list[str]:
    async with database.session() as session:
        rows = await session.scalars(
            select(YtRawVideo.video_id).order_by(YtRawVideo.video_id)
        )
        return list(rows)


async def video(database: Database, number: int) -> YtRawVideo | None:
    async with database.session() as session:
        return await session.get(YtRawVideo, video_id(number))


async def record(database: Database, number: int = 1) -> YtBackfillWalk:
    async with database.session() as session:
        found = await session.get(YtBackfillWalk, channel_id(number))
    assert found is not None
    return found


async def readings(database: Database) -> list[YtRawVideoStat]:
    async with database.session() as session:
        return list(await session.scalars(select(YtRawVideoStat)))


def asked(fake: FakeYouTube) -> list[str]:
    """Every video id asked for, in order."""
    return [
        id_ for call in fake.calls("videos") for id_ in call["id"].split(",")
    ]


# --- Depth -----------------------------------------------------------------


async def test_a_video_inside_the_depth_is_stored(database: Database) -> None:
    await seeds(database, 1)
    fake = api({1: listing(40, 200, 400)}, resources(40, 200, 400))

    report = await walk(database, fake)

    assert await stored(database) == [video_id(0), video_id(1)]
    assert asked(fake) == [video_id(0), video_id(1)]
    assert (report.finished, report.videos) == (1, 2)


# --- What The Poll Loop Owns Is Left To It ---------------------------------


async def test_a_recent_video_is_left_to_the_poll_loop(
    database: Database,
) -> None:
    await seeds(database, 1)
    fake = api({1: listing(10, 40, 400)}, resources(10, 40, 400))

    await walk(database, fake)

    assert video_id(0) not in asked(fake)
    assert await stored(database) == [video_id(1)]


async def test_a_stored_video_is_untouched(database: Database) -> None:
    await seeds(database, 1)
    old = NOW - timedelta(days=35)
    async with database.session() as session:
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(0),
                    payload=video_resource(0, published=old, title="Kept"),
                    due_at=None,
                    reading=Counters(views=5),
                    discovered_via=YtDiscoveryPath.CHECK,
                )
            ],
            observed_at=NOW - timedelta(days=7),
        )
    fake = api({1: listing(35, 400)}, resources(35, 400))

    await walk(database, fake)

    assert asked(fake) == []
    found = await video(database, 0)
    assert found is not None
    assert found.payload["snippet"]["title"] == "Kept"
    assert found.fetched_at == NOW - timedelta(days=7)
    assert found.discovered_via is YtDiscoveryPath.CHECK


# --- Where A Walk Stops ----------------------------------------------------


def _years_of(count: int) -> list[float]:
    """``count`` uploads three days apart, starting a month back."""
    return [30 + 3 * n for n in range(count)]


async def test_the_walk_stops_on_the_page_that_crosses_the_depth(
    database: Database,
) -> None:
    """Upload 112 is the first past 365 days; it is on the third page."""
    await seeds(database, 1)
    ages = _years_of(160)
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake)

    assert len(fake.calls("playlistItems")) == 3
    assert report.videos == 112
    assert (await record(database)).finished_at == NOW


async def test_a_short_playlist_ends_the_walk(database: Database) -> None:
    await seeds(database, 1)
    ages = _years_of(30)
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake)

    assert len(fake.calls("playlistItems")) == 1
    assert (report.finished, report.videos) == (1, 30)


async def test_a_whole_walk_goes_to_the_end(database: Database) -> None:
    await seeds(database, 1)
    ages = [30 + 6 * n for n in range(160)]  # past the depth from no. 56
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake, named=[channel_id(1)], whole=True)

    assert len(fake.calls("playlistItems")) == 4
    assert report.videos == 56
    assert (await record(database)).whole


async def test_no_page_token_is_needed_for_the_first_page(
    database: Database,
) -> None:
    await seeds(database, 1)
    ages = _years_of(160)
    fake = api({1: listing(*ages)}, resources(*ages))

    await walk(database, fake)

    tokens = [call.get("pageToken") for call in fake.calls("playlistItems")]
    assert tokens[0] is None and None not in tokens[1:]


# --- Found By The Walk Is Recorded -----------------------------------------


async def test_a_video_found_by_a_walk(database: Database) -> None:
    await seeds(database, 1)
    fake = api({1: listing(40)}, resources(40))

    await walk(database, fake)

    found = await video(database, 0)
    assert found is not None
    assert found.discovered_via is YtDiscoveryPath.BACKFILL
    assert (found.discovered_at, found.fetched_at) == (NOW, NOW)
    assert found.due_at is None


# --- The Listing's Order Is Checked, Not Trusted ---------------------------


async def test_an_item_out_of_order_is_counted(database: Database) -> None:
    """The second page holds an item dated later than one on the first."""
    await seeds(database, 1)
    ages = _years_of(60)
    ages[55] = ages[10]  # later than items 11–49 on the first page
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake)

    assert report.out_of_order == 1
    assert (await record(database)).out_of_order == 1


async def test_an_undated_item_is_asked_about(database: Database) -> None:
    await seeds(database, 1)
    fake = api({1: listing(None, None, 400)}, resources(40, 380))

    report = await walk(database, fake)

    assert asked(fake) == [video_id(0), video_id(1)]
    # Dated inside the depth by YouTube: stored. Past it: not.
    assert await stored(database) == [video_id(0)]
    assert report.undated == 2
    assert (await record(database)).undated == 2


async def test_a_listed_video_youtube_does_not_return_is_counted(
    database: Database,
) -> None:
    await seeds(database, 1)
    fake = api({1: listing(40, 50, 400)}, resources(40))

    report = await walk(database, fake)

    assert await stored(database) == [video_id(0)]
    assert report.unreturned == 1
    assert (await record(database)).unreturned == 1


async def test_the_record_holds_no_data_youtube_returned(
    database: Database,
) -> None:
    await seeds(database, 1)
    fake = api({1: listing(40, None, 400)}, resources(40, 41, title="Odd"))

    await walk(database, fake)

    walked = await record(database)
    assert walked.cutoff == NOW - timedelta(days=365)
    async with database.session() as session:
        row = await session.scalar(
            text("SELECT row_to_json(w)::text FROM yt_backfill_walks w")
        )
    assert row is not None
    for moment in (40, 41, 400):
        assert stamp(NOW - timedelta(days=moment))[:10] not in row
    assert "Odd" not in row


# --- A Ceiling Per Channel -------------------------------------------------


async def test_the_ceiling_ends_the_walk(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_backfill_max_videos", 70)
    await seeds(database, 1)
    ages = _years_of(100)
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake)

    assert len(await stored(database)) == 70
    assert len(fake.calls("playlistItems")) == 2
    walked = await record(database)
    assert walked.capped and walked.finished_at == NOW
    assert report.capped == 1


async def test_zero_lifts_the_ceiling(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_backfill_max_videos", 0)
    await seeds(database, 1)
    ages = _years_of(100)
    fake = api({1: listing(*ages)}, resources(*ages))

    await walk(database, fake)

    assert len(await stored(database)) == 100
    assert not (await record(database)).capped


# --- Broadcasts Yet To Start Are Handed To The Poll Loop -------------------


def _scheduled_stream(scheduled: datetime) -> dict[str, Any]:
    return video_resource(
        0,
        published=NOW - timedelta(days=40),
        broadcast="upcoming",
        scheduled=scheduled,
    )


async def test_a_scheduled_stream_is_handed_to_the_poll_loop(
    database: Database,
) -> None:
    await seeds(database, 1)
    scheduled = NOW + DAY
    fake = api({1: listing(40, 400)}, [_scheduled_stream(scheduled)])

    report = await walk(database, fake)

    found = await video(database, 0)
    assert found is not None and found.due_at == scheduled
    assert found.discovered_via is YtDiscoveryPath.BACKFILL
    assert await readings(database) == []
    assert report.handed_over == 1


async def test_the_poll_loop_checks_reads_and_gives_up_what_it_was_handed(
    database: Database,
) -> None:
    await seeds(database, 1)
    soon = NOW + timedelta(hours=2)
    never = NOW + timedelta(hours=3)
    upcoming = [
        _scheduled_stream(soon),
        video_resource(
            1,
            published=NOW - timedelta(days=40),
            broadcast="upcoming",
            scheduled=never,
        ),
    ]
    await walk(database, api({1: listing(40, 40, 400)}, upcoming))

    # At its start the first is live and read; the second still waits.
    live = video_resource(
        0,
        published=NOW - timedelta(days=40),
        broadcast="live",
        scheduled=soon,
        live_started=soon,
    )
    loop = FakeYouTube(
        channel_resource(1),
        uploads={uploads_id(1): []},
        videos=[live, upcoming[1]],
    )
    async with youtube(
        database, YtCommand.WATCH, transport=loop.transport(), sleep=no_sleep
    ) as client:
        await watch_pass(client, database, clock=lambda: never)
        a_week_on = never + timedelta(days=7, minutes=1)
        await watch_pass(client, database, clock=lambda: a_week_on)

    read = await video(database, 0)
    given_up = await video(database, 1)
    assert read is not None and given_up is not None
    taken = {(r.video_id, r.observed_at) for r in await readings(database)}
    # Read once live, and on from there by the loop's own schedule.
    assert (video_id(0), never) in taken
    assert not any(id_ == video_id(1) for id_, _ in taken)
    assert read.discovered_via is YtDiscoveryPath.BACKFILL
    assert given_up.due_at is None


# --- Every Stored Video Is A Reading ---------------------------------------


async def test_each_stored_video_is_a_reading(database: Database) -> None:
    await seeds(database, 1)
    fake = api({1: listing(40, 400)}, resources(40))

    await walk(database, fake)

    [call] = fake.calls("videos")
    assert "statistics" in call["part"]
    [reading] = await readings(database)
    assert (reading.video_id, reading.observed_at, reading.views) == (
        video_id(0),
        NOW,
        100,
    )


# --- runs: failures, limits, the floor, resuming ---------------------------


async def test_a_channel_that_cannot_be_listed_does_not_stop_the_run(
    database: Database,
) -> None:
    await seeds(database, 1, 2)
    fake = api({2: listing(40, 400)}, resources(40))

    report = await walk(database, fake)

    assert report.failures == 1 and report.finished == 1
    failed = await record(database, 1)
    assert failed.finished_at is None
    assert failed.consecutive_failures == 1
    assert failed.last_error is not None and "404" in failed.last_error
    assert await stored(database) == [video_id(0)]


async def test_a_run_is_bounded_by_requests(database: Database) -> None:
    await seeds(database, 1)
    ages = _years_of(160)
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake, limit=3)

    assert len(fake.requests) == 3
    assert isinstance(report.stopped, RequestLimitReached)
    assert report.failed is None


async def test_the_floor_stops_a_walk_and_keeps_its_work(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six for the general pool, five of them left alone: one request."""
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 4)
    monkeypatch.setattr(settings, "youtube_backfill_general_floor", 4)
    await seeds(database, 1)
    ages = _years_of(160)
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake)

    assert isinstance(report.stopped, FloorReachedError)
    assert len(fake.requests) == 2
    assert len(await stored(database)) == 50
    assert (await record(database)).finished_at is None


class RefusingTokens(FakeYouTube):
    """YouTube that answers every listing past the first page with a 400:
    a page token that did not live, which stops the run."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.method(request) == "playlistItems" and (
            "pageToken" in request.url.params
        ):
            self.requests.append(request)
            return httpx.Response(
                400, json=error_body(400, "invalidPageToken")
            )
        return super().handler(request)


async def test_an_interrupted_walk_resumes_from_the_newest_page(
    database: Database,
) -> None:
    await seeds(database, 1)
    ages = _years_of(160)
    fake = RefusingTokens(
        uploads={uploads_id(1): listing(*ages)}, videos=resources(*ages)
    )

    report = await walk(database, fake)

    assert isinstance(report.failed, ApiError)
    assert len(await stored(database)) == 50
    assert (await record(database)).finished_at is None

    again = api({1: listing(*ages)}, resources(*ages))
    resumed = await walk(database, again)

    assert again.calls("playlistItems")[0].get("pageToken") is None
    # The fifty stored before the stop are not asked for again.
    assert not set(asked(again)) & {video_id(n) for n in range(50)}
    assert resumed.finished == 1
    assert len(await stored(database)) == 112


async def test_a_spent_pool_ends_a_walk_with_no_floor(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the floor at zero, what stops the walk is the pool's own cap."""
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 4)
    monkeypatch.setattr(settings, "youtube_backfill_general_floor", 0)
    await seeds(database, 1)
    # Five of the general pool's six spent by another command.
    await book(
        database, command=YtCommand.ADD, method="channels.list", units=5
    )
    ages = _years_of(160)
    fake = api({1: listing(*ages)}, resources(*ages))

    report = await walk(database, fake)

    assert isinstance(report.stopped, PoolSpentError)
    assert report.failed is None
    assert len(fake.requests) == 1
    assert (await record(database)).finished_at is None


async def test_without_records_or_videos_every_seed_is_walked_anew(
    database: Database,
) -> None:
    """With both empty — a database restored from before any walk — the
    next walk finds the seed due and walks it from the newest page."""
    await seeds(database, 1)
    ages = _years_of(30)
    await walk(database, api({1: listing(*ages)}, resources(*ages)))
    assert (await record(database)).finished_at == NOW

    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_backfill_walks"))
        await session.execute(text("DELETE FROM yt_raw_videos"))

    again = api({1: listing(*ages)}, resources(*ages))
    report = await walk(database, again)

    assert again.calls("playlistItems")[0].get("pageToken") is None
    assert (report.finished, report.videos) == (1, 30)
    assert len(await stored(database)) == 30


async def test_a_walk_on_an_exhausted_day_makes_no_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seeds(database, 1)
    await record_exhaustion(database, command=YtCommand.WATCH)
    built: list[str] = []
    real = yt_client.youtube

    def counting(*args: Any, **kwargs: Any) -> Any:
        built.append("client")
        return real(*args, **kwargs)

    monkeypatch.setattr(yt_client, "youtube", counting)

    report = await run_walk(database)

    assert isinstance(report.stopped, QuotaExhaustedError)
    assert built == []


# --- Logs Name Ids Only, The Walk Derives Nothing --------------------------


async def test_a_walk_logs_ids_and_nothing_youtube_said(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    await seeds(database, 1)
    title = "A Fake Title Nobody Logs"
    videos = resources(40, title=title) + [_scheduled_stream(NOW + DAY)]
    videos[1]["id"] = video_id(1)
    fake = api({1: listing(40, 41, 400)}, videos)

    with caplog.at_level(logging.DEBUG):
        await walk(database, fake)

    logged = "\n".join(entry.getMessage() for entry in caplog.records)
    assert video_id(1) in logged
    assert title not in logged
    assert "invented video" not in logged
    assert stamp(NOW - timedelta(days=40)) not in logged


async def test_the_walk_derives_nothing(database: Database) -> None:
    await seeds(database, 1)
    fake = api({1: listing(40, 400)}, resources(40))
    async with database.session() as session:
        before = {
            table.name: await session.scalar(
                text(f'SELECT count(*) FROM "{table.name}"')
            )
            for table in Base.metadata.sorted_tables
        }

    await walk(database, fake)

    async with database.session() as session:
        after = {
            table.name: await session.scalar(
                text(f'SELECT count(*) FROM "{table.name}"')
            )
            for table in Base.metadata.sorted_tables
        }
    changed = {name for name in after if after[name] != before[name]}
    assert changed == {
        "yt_raw_videos",
        "yt_raw_video_stats",
        "yt_backfill_walks",
        "yt_requests",
        "yt_quota_days",
    }
