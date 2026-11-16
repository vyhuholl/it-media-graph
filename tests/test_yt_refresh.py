"""The refresh pass: everything due, fifty a call, nothing lost on a stop."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from typer.testing import CliRunner
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    sections_response,
    use_key,
    video_id,
    video_resource,
)

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtCommand,
    YtLang,
    YtRawChannel,
    YtRawChannelSections,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import (
    create_channels,
    due_for_refresh,
    store_payloads,
)
from itgraph.db.yt_quota import QuotaExhaustedError, record_exhaustion
from itgraph.db.yt_sections import store_sections
from itgraph.derive.yt_featured import featured_ids
from itgraph.yt import client as yt_client
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.refresh import refresh_channels

STALE = datetime.now(UTC) - timedelta(days=26)
FRESH = datetime.now(UTC) - timedelta(days=1)

runner = CliRunner()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[YouTube]:
    async with youtube(
        database,
        YtCommand.REFRESH,
        transport=fake.transport(),
        sleep=no_sleep,
    ) as connected:
        yield connected


async def _channels(
    database: Database, numbers: range | list[int], fetched_at: datetime | None
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in numbers])
        if fetched_at is not None:
            await store_payloads(
                session,
                {channel_id(n): channel_resource(n) for n in numbers},
                fetched_at=fetched_at,
            )


async def _fetched_at(database: Database) -> dict[str, datetime]:
    async with database.session() as session:
        rows = await session.execute(
            select(YtRawChannel.channel_id, YtRawChannel.fetched_at)
        )
        return {channel: when for channel, when in rows.tuples()}


async def test_a_stale_payload_is_refreshed_and_a_fresh_one_is_not(
    database: Database,
) -> None:
    await _channels(database, [1], STALE)
    await _channels(database, [2], FRESH)
    fake = FakeYouTube(channel_resource(1), channel_resource(2))

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert (summary.due, summary.refreshed) == (1, 1)
    assert fake.params()[0]["id"] == channel_id(1)
    fetched = await _fetched_at(database)
    assert fetched[channel_id(1)] > STALE
    assert fetched[channel_id(2)] == FRESH


async def test_rejected_channels_are_refreshed_too(
    database: Database,
) -> None:
    await _channels(database, [1], STALE)
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.REJECTED
        record.reject_reason = YtRejectReason.ADJACENT

    async with client(database, FakeYouTube(channel_resource(1))) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.refreshed == 1


async def test_a_hundred_due_cost_two_requests(database: Database) -> None:
    await _channels(database, range(100), STALE)
    fake = FakeYouTube(*(channel_resource(n) for n in range(100)))

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.refreshed == 100
    assert len(fake.requests) == 2


async def test_gone_and_back(database: Database) -> None:
    await _channels(database, [1, 2], STALE)

    async with client(database, FakeYouTube(channel_resource(1))) as yt:
        first = await refresh_channels(yt, database)

    assert first.gone == [channel_id(2)]
    assert channel_id(2) not in await _fetched_at(database)

    fake = FakeYouTube(channel_resource(1), channel_resource(2))
    async with client(database, fake) as yt:
        second = await refresh_channels(yt, database)

    # Only the gone one was due: it has no payload to be fresh.
    assert second.due == 1
    assert second.returned == [channel_id(2)]
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(2))
    assert record is not None and record.gone_at is None


async def test_one_dead_channel_does_not_cost_the_others(
    database: Database,
) -> None:
    await _channels(database, [1, 2, 3], STALE)
    fake = FakeYouTube(
        channel_resource(1), channel_resource(3), batch_404=True
    )

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.refreshed == 2
    assert summary.gone == [channel_id(2)]


async def test_exhaustion_part_way_keeps_what_was_done(
    database: Database,
) -> None:
    await _channels(database, range(60), STALE)
    fake = FakeYouTube(*(channel_resource(n) for n in range(60)))
    fake.queued = [
        httpx.Response(
            200, json={"items": [channel_resource(n) for n in range(50)]}
        ),
        httpx.Response(403, json=error_body(403, "quotaExceeded")),
    ]

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert isinstance(summary.stopped, QuotaExhaustedError)
    assert summary.refreshed == 50
    assert summary.gone == []

    # The next quota day: only the ten not reached are still due.
    async with database.session() as session:
        due = await due_for_refresh(
            session, older_than=datetime.now(UTC) - timedelta(days=25)
        )
    assert len(due) == 10


async def test_the_log_carries_ids_and_nothing_youtube_said(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await _channels(database, [1, 2], STALE)
    fake = FakeYouTube(channel_resource(1, title="A Title For The Log Test"))

    async with client(database, fake) as yt:
        await refresh_channels(yt, database)

    assert channel_id(2) in caplog.text
    assert "A Title For The Log Test" not in caplog.text
    assert "fakehandle1" not in caplog.text


def _use(monkeypatch: pytest.MonkeyPatch, url: str, fake: FakeYouTube) -> None:
    original_database = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original_database(url))
    original_youtube = yt_client.youtube

    def faked(database: Database, command: YtCommand) -> object:
        return original_youtube(
            database, command, transport=fake.transport(), sleep=no_sleep
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


async def _setup_stale(url: str) -> None:
    database = Database(url)
    try:
        await _channels(database, [1, 2], STALE)
    finally:
        await database.dispose()


def test_the_command_reports_and_lists_the_gone(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    asyncio.run(_setup_stale(database_url))
    _use(monkeypatch, database_url, FakeYouTube(channel_resource(1)))

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 0, result.output
    assert "due 2, refreshed 1, gone 1, back 0" in result.output
    assert f"gone: {channel_id(2)}" in result.output


def test_the_command_fails_when_stopped(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    asyncio.run(_setup_stale(database_url))
    fake = FakeYouTube()
    fake.queued = [httpx.Response(403, json=error_body(403, "quotaExceeded"))]
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 1
    assert "quotaExceeded" in result.output


def test_the_command_without_a_key_names_the_setting(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    asyncio.run(_setup_stale(database_url))
    fake = FakeYouTube()
    _use(monkeypatch, database_url, fake)
    monkeypatch.setattr(settings, "youtube_api_key", None)

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 1
    assert "YOUTUBE_API_KEY" in result.output
    assert fake.requests == []


def test_the_command_refuses_to_start_on_an_exhausted_day(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:

    async def exhaust() -> None:
        database = Database(database_url)
        try:
            await _channels(database, [1], STALE)
            await record_exhaustion(database, command=YtCommand.ADD)
        finally:
            await database.dispose()

    asyncio.run(exhaust())
    fake = FakeYouTube(channel_resource(1))
    _use(monkeypatch, database_url, fake)

    result = runner.invoke(app, ["yt", "refresh"])

    assert result.exit_code == 1
    assert "quotaExceeded" in result.stderr
    assert fake.requests == []


# --- past uploads, kept fresh while the history walk is enabled ------------


@pytest.fixture
def walk_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "youtube_backfill_enabled", True)


async def _seed(database: Database, number: int = 1) -> None:
    from itgraph.db.models import YtChannelKind, YtLang

    await _channels(database, [number], FRESH)
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(number))
        assert record is not None
        record.status = ChannelStatus.SEED
        record.kind = YtChannelKind.PERSONAL
        record.lang = YtLang.EN


async def _past_upload(
    database: Database,
    number: int,
    *,
    age: int,
    channel: int = 1,
    walked: bool = True,
    **resource: Any,
) -> None:
    """A past upload last fetched 26 days ago: by a walk, or by the loop."""
    from itgraph.db.models import YtDiscoveryPath
    from itgraph.db.yt_videos import (
        FoundVideo,
        VideoWrite,
        store_found_videos,
        store_videos,
    )
    from itgraph.derive.yt_videos import Counters

    payload = video_resource(
        number,
        published=datetime.now(UTC) - timedelta(days=age),
        channel=channel,
        **resource,
    )
    async with database.session() as session:
        if walked:
            await store_found_videos(
                session,
                [
                    FoundVideo(
                        video_id=video_id(number),
                        payload=payload,
                        due_at=None,
                        reading=None,
                    )
                ],
                fetched_at=STALE,
            )
        else:
            await store_videos(
                session,
                [
                    VideoWrite(
                        video_id=video_id(number),
                        payload=payload,
                        due_at=None,
                        reading=Counters(views=10),
                        discovered_via=YtDiscoveryPath.CHECK,
                    )
                ],
                observed_at=STALE,
            )


def _answer(number: int, *, age: int) -> dict[str, Any]:
    return video_resource(
        number, published=datetime.now(UTC) - timedelta(days=age), views=555
    )


async def _video_row(database: Database, number: int) -> Any:
    from itgraph.db.models import YtRawVideo

    async with database.session() as session:
        return await session.get(YtRawVideo, video_id(number))


async def _reading_ids(database: Database) -> list[str]:
    from itgraph.db.models import YtRawVideoStat

    async with database.session() as session:
        return list(await session.scalars(select(YtRawVideoStat.video_id)))


async def test_a_past_upload_is_refreshed(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _past_upload(database, 1, age=100)
    fake = FakeYouTube(videos=[_answer(1, age=100)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert (summary.past_due, summary.past_refreshed) == (1, 1)
    found = await _video_row(database, 1)
    assert found is not None and found.fetched_at > STALE
    assert "past uploads due 1, refreshed 1, removed 0" in summary.line()


async def test_a_video_the_poll_loop_finished_joins_the_year(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _past_upload(database, 1, age=35, walked=False)
    fake = FakeYouTube(videos=[_answer(1, age=35)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_refreshed == 1


async def test_a_video_past_the_depth_is_not_read_again(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _past_upload(database, 1, age=370)
    fake = FakeYouTube(videos=[_answer(1, age=370)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_due == 0
    assert fake.calls("videos") == []


async def test_a_channel_out_of_scope_is_not_kept(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _channels(database, [2], FRESH)
    await _past_upload(database, 1, age=100, channel=2)
    fake = FakeYouTube(videos=[_answer(1, age=100)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_due == 0


async def test_a_broadcast_yet_to_start_is_the_loop_s_to_check(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _past_upload(
        database,
        1,
        age=100,
        broadcast="upcoming",
        scheduled=datetime.now(UTC) - timedelta(days=90),
    )

    async with client(database, FakeYouTube()) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_due == 0


async def test_nothing_is_kept_while_the_walk_is_disabled(
    database: Database,
) -> None:
    await _seed(database)
    await _past_upload(database, 1, age=100)
    fake = FakeYouTube(videos=[_answer(1, age=100)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_due == 0
    assert fake.calls("videos") == []
    assert "past uploads" not in summary.line()


async def test_every_refresh_of_a_past_upload_is_a_reading(
    database: Database, walk_enabled: None
) -> None:
    from itgraph.db.models import YtRawVideoStat

    await _seed(database)
    await _past_upload(database, 1, age=100)
    fake = FakeYouTube(videos=[_answer(1, age=100)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_readings == 1
    async with database.session() as session:
        [reading] = list(await session.scalars(select(YtRawVideoStat)))
    assert (reading.video_id, reading.views) == (video_id(1), 555)
    assert len(fake.calls("videos")) == 1


async def test_a_past_upload_youtube_no_longer_returns_is_removed(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _past_upload(database, 1, age=100)

    async with client(database, FakeYouTube()) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_removed == 1
    assert await _video_row(database, 1) is None


# --- featured lists ------------------------------------------------------


async def _seeds(
    database: Database,
    numbers: list[int],
    *,
    status: ChannelStatus = ChannelStatus.SEED,
) -> None:
    """Channels with fresh payloads, so only their sections can be due."""
    await _channels(database, numbers, FRESH)
    async with database.session() as session:
        for number in numbers:
            record = await session.get(YtChannel, channel_id(number))
            assert record is not None
            record.status = status
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN
            if status is ChannelStatus.REJECTED:
                record.reject_reason = YtRejectReason.NOT_IT


async def _lists(database: Database) -> dict[str, list[str]]:
    async with database.session() as session:
        rows = await session.scalars(select(YtRawChannelSections))
        return {row.channel_id: featured_ids(row.payload) for row in rows}


async def test_a_seed_without_a_list_has_its_sections_read(
    database: Database,
) -> None:
    await _seeds(database, [1])
    fake = FakeYouTube(
        channel_resource(1),
        sections={channel_id(1): sections_response([], [channel_id(5)])},
    )

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert (summary.sections_due, summary.sections_read) == (1, 1)
    assert len(fake.calls("channelSections")) == 1
    assert await _lists(database) == {channel_id(1): [channel_id(5)]}
    assert "featured lists due 1, read 1" in summary.line()


async def test_reading_sections_logs_ids_only(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """Featured Logs Name Ids Only: the seed's id, never a title or handle."""
    caplog.set_level(logging.DEBUG)
    await _seeds(database, [1])
    fake = FakeYouTube(
        channel_resource(1, title="Fake Channel Title"),
        sections={
            channel_id(1): {
                "items": [
                    {
                        "snippet": {"title": "Fake Shelf Title"},
                        "contentDetails": {"channels": [channel_id(5)]},
                    }
                ]
            }
        },
    )

    async with client(database, fake) as yt:
        await refresh_channels(yt, database)

    assert f"read the featured list of {channel_id(1)}" in caplog.text
    for said in ("Fake Shelf Title", "Fake Channel Title", "fakehandle"):
        assert said not in caplog.text


async def test_a_fresh_list_costs_nothing(database: Database) -> None:
    await _seeds(database, [1])
    async with database.session() as session:
        await store_sections(
            session, channel_id(1), sections_response([]), fetched_at=FRESH
        )
    fake = FakeYouTube(channel_resource(1))

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.sections_due == 0
    assert fake.calls("channelSections") == []


async def test_a_stale_list_is_read_again(database: Database) -> None:
    await _seeds(database, [1])
    async with database.session() as session:
        await store_sections(
            session,
            channel_id(1),
            sections_response([channel_id(5)]),
            fetched_at=STALE,
        )
    fake = FakeYouTube(
        channel_resource(1),
        sections={channel_id(1): sections_response([channel_id(6)])},
    )

    async with client(database, fake) as yt:
        await refresh_channels(yt, database)

    assert await _lists(database) == {channel_id(1): [channel_id(6)]}


@pytest.mark.parametrize(
    "status",
    [ChannelStatus.CANDIDATE, ChannelStatus.MAYBE, ChannelStatus.REJECTED],
)
async def test_other_statuses_are_not_asked_about(
    database: Database, status: ChannelStatus
) -> None:
    await _seeds(database, [1], status=status)
    fake = FakeYouTube(channel_resource(1))

    async with client(database, fake) as yt:
        await refresh_channels(yt, database)

    assert fake.calls("channelSections") == []


async def test_a_seed_found_gone_is_not_asked_about(
    database: Database,
) -> None:
    """The channels come first, so a seed just marked gone is skipped."""
    await _seeds(database, [1])
    async with database.session() as session:
        payload = await session.get(YtRawChannel, channel_id(1))
        assert payload is not None
        payload.fetched_at = STALE
    fake = FakeYouTube()  # YouTube no longer returns the channel

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.gone == [channel_id(1)]
    assert fake.calls("channelSections") == []


async def test_exhaustion_stops_the_lists_part_way(database: Database) -> None:
    await _seeds(database, [1, 2, 3])
    fake = FakeYouTube(
        channel_resource(1),
        channel_resource(2),
        channel_resource(3),
        sections={
            channel_id(n): sections_response([channel_id(9)])
            for n in (1, 2, 3)
        },
    )
    real = fake.handler

    def second_call_spent(request: httpx.Request) -> httpx.Response:
        if (
            fake.method(request) == "channelSections"
            and len(fake.calls("channelSections")) == 1
        ):
            fake.requests.append(request)
            return httpx.Response(403, json=error_body(403, "quotaExceeded"))
        return real(request)

    async with youtube(
        database,
        YtCommand.REFRESH,
        transport=httpx.MockTransport(second_call_spent),
        sleep=no_sleep,
    ) as yt:
        summary = await refresh_channels(yt, database)

    assert isinstance(summary.stopped, QuotaExhaustedError)
    assert summary.sections_read == 1
    assert set(await _lists(database)) == {channel_id(1)}

    # The next quota day continues with those still due.
    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_quota_exhausted"))
    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)
    assert summary.sections_due == 2
    assert set(await _lists(database)) == {channel_id(n) for n in (1, 2, 3)}


async def test_a_restore_is_completed_by_a_refresh(database: Database) -> None:
    """A dump carries no lists, so after a restore every seed is due."""
    await _seeds(database, [1, 2])
    fake = FakeYouTube(
        channel_resource(1),
        channel_resource(2),
        sections={channel_id(1): sections_response([channel_id(9)])},
    )

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.sections_read == 2
    assert await _lists(database) == {
        channel_id(1): [channel_id(9)],
        channel_id(2): [],
    }


# --- late readings: the counts of past uploads, daily then weekly ----------


async def _kept(
    database: Database,
    number: int,
    *,
    age: float,
    read_ago: timedelta | None,
    channel: int = 1,
    due_at: datetime | None = None,
    duration: str | None = None,
    **resource: Any,
) -> None:
    """A past upload with fresh data, last read ``read_ago`` (or never)."""
    from itgraph.db.models import YtDiscoveryPath
    from itgraph.db.yt_videos import (
        FoundVideo,
        VideoWrite,
        store_found_videos,
        store_videos,
    )
    from itgraph.derive.yt_videos import Counters

    payload = video_resource(
        number,
        published=datetime.now(UTC) - timedelta(days=age),
        channel=channel,
        **resource,
    )
    if duration is not None:
        payload["contentDetails"]["duration"] = duration
    async with database.session() as session:
        if read_ago is None:
            await store_found_videos(
                session,
                [
                    FoundVideo(
                        video_id=video_id(number),
                        payload=payload,
                        due_at=due_at,
                        reading=None,
                    )
                ],
                fetched_at=FRESH,
            )
        else:
            await store_videos(
                session,
                [
                    VideoWrite(
                        video_id=video_id(number),
                        payload=payload,
                        due_at=due_at,
                        reading=Counters(views=10, likes=1, comments=0),
                        discovered_via=YtDiscoveryPath.CHECK,
                    )
                ],
                observed_at=datetime.now(UTC) - read_ago,
            )


async def _listed(database: Database, number: int = 1) -> None:
    """A seed whose featured list is fresh, so the refresh asks nothing
    of it and the first request a test queues for is a video request."""
    async with database.session() as session:
        await store_sections(
            session,
            channel_id(number),
            sections_response([]),
            fetched_at=FRESH,
        )


def _counts(number: int, *, age: float = 60, **counters: Any) -> Any:
    return video_resource(
        number,
        published=datetime.now(UTC) - timedelta(days=age),
        **{"views": 5000, **counters},
    )


async def _readings_of(database: Database, number: int) -> list[Any]:
    from itgraph.db.models import YtRawVideoStat

    async with database.session() as session:
        return list(
            await session.scalars(
                select(YtRawVideoStat)
                .where(YtRawVideoStat.video_id == video_id(number))
                .order_by(YtRawVideoStat.observed_at)
            )
        )


async def test_a_young_past_upload_is_read_daily(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(hours=24))
    fake = FakeYouTube(videos=[_counts(1)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert (summary.late_due, summary.late_read) == (1, 1)
    assert "late readings due 1, read 1" in summary.line()
    [_, late] = await _readings_of(database, 1)
    assert late.views == 5000
    [call] = fake.calls("videos")
    assert call["part"] == "statistics"


async def test_an_older_past_upload_is_read_weekly(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _kept(database, 1, age=200, read_ago=timedelta(days=3))
    await _kept(database, 2, age=200, read_ago=timedelta(days=7))
    fake = FakeYouTube(videos=[_counts(1, age=200), _counts(2, age=200)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.late_due == 1
    assert len(await _readings_of(database, 1)) == 1
    assert len(await _readings_of(database, 2)) == 2


async def test_a_daily_run_does_not_slip_a_day(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(hours=23))

    async with client(database, FakeYouTube(videos=[_counts(1)])) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.late_read == 1


async def test_a_video_read_once_in_a_run_is_not_read_twice(
    database: Database, walk_enabled: None
) -> None:
    """Its data was stale, so the step before fetched it with a reading."""
    await _seed(database)
    await _past_upload(database, 1, age=100)
    fake = FakeYouTube(videos=[_answer(1, age=100)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert (summary.past_readings, summary.late_due) == (1, 0)
    assert len(await _readings_of(database, 1)) == 1
    assert len(fake.calls("videos")) == 1


async def test_what_the_refresh_does_not_keep_is_not_read(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _channels(database, [2], FRESH)
    month = timedelta(days=30)
    # Past the depth; still the loop's; a broadcast yet to start; a
    # channel that is no longer a seed.
    await _kept(database, 1, age=370, read_ago=month)
    await _kept(
        database,
        2,
        age=10,
        read_ago=month,
        due_at=datetime.now(UTC) + timedelta(days=1),
    )
    await _kept(
        database,
        3,
        age=60,
        read_ago=None,
        broadcast="upcoming",
        scheduled=datetime.now(UTC) + timedelta(days=1),
    )
    await _kept(database, 4, age=60, read_ago=month, channel=2)
    fake = FakeYouTube(videos=[_counts(n) for n in range(1, 5)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.late_due == 0
    assert fake.calls("videos") == []


async def test_nothing_is_read_late_while_the_walk_is_disabled(
    database: Database,
) -> None:
    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    fake = FakeYouTube(videos=[_counts(1)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.late_due == 0
    assert fake.calls("videos") == []
    assert "late readings" not in summary.line()


async def test_every_format_is_read_alike(
    database: Database, walk_enabled: None
) -> None:
    """A 40-second video and a finished stream, each read like any other."""
    await _seed(database)
    two_days = timedelta(days=2)
    await _kept(database, 1, age=60, read_ago=two_days, duration="PT40S")
    await _kept(
        database,
        2,
        age=60,
        read_ago=two_days,
        live_started=datetime.now(UTC) - timedelta(days=60),
    )
    fake = FakeYouTube(videos=[_counts(1), _counts(2)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.late_read == 2


async def test_videos_share_requests(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    for number in range(120):
        await _kept(database, number, age=60, read_ago=timedelta(days=2))
    fake = FakeYouTube(videos=[_counts(n) for n in range(120)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.late_read == 120
    calls = fake.calls("videos")
    assert [len(call["id"].split(",")) for call in calls] == [50, 50, 20]


async def test_the_stored_data_is_not_replaced(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    before = await _video_row(database, 1)
    fake = FakeYouTube(videos=[_counts(1, likes=None, title="Renamed")])

    async with client(database, fake) as yt:
        await refresh_channels(yt, database)

    after = await _video_row(database, 1)
    assert (after.payload, after.fetched_at) == (
        before.payload,
        before.fetched_at,
    )
    late = (await _readings_of(database, 1))[-1]
    assert (late.views, late.likes, late.comments) == (5000, None, 3)


async def test_a_past_upload_youtube_no_longer_returns_is_removed_late(
    database: Database, walk_enabled: None
) -> None:
    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    await _kept(database, 2, age=60, read_ago=timedelta(days=2))
    fake = FakeYouTube(videos=[_counts(2)])

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.past_removed == 1
    assert await _video_row(database, 1) is None
    assert await _readings_of(database, 1) == []
    assert len(await _readings_of(database, 2)) == 2


async def test_exhaustion_stops_the_readings_part_way(
    database: Database, walk_enabled: None
) -> None:
    from itgraph.db.yt_videos import past_uploads_for_late_readings
    from itgraph.yt_schedule import late_reading_due

    await _seed(database)
    await _listed(database)
    for number in range(60):
        await _kept(database, number, age=60, read_ago=timedelta(days=2))
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": video_id(n), "statistics": {"viewCount": "7"}}
                    for n in range(50)
                ]
            },
        ),
        httpx.Response(403, json=error_body(403, "quotaExceeded")),
    ]

    async with client(database, fake) as yt:
        summary = await refresh_channels(yt, database)

    assert isinstance(summary.stopped, QuotaExhaustedError)
    assert (summary.late_due, summary.late_read) == (60, 50)
    now = datetime.now(UTC)
    async with database.session() as session:
        kept = await past_uploads_for_late_readings(
            session, since=now - timedelta(days=365)
        )
    still = [
        upload
        for upload in kept
        if late_reading_due(upload.published_at, upload.newest_reading_at, now)
    ]
    assert len(still) == 10
    # The least recently read come first, so the next run starts there.
    assert {upload.video_id for upload in kept[:10]} == {
        upload.video_id for upload in still
    }


async def test_late_readings_are_booked_to_the_general_pool(
    database: Database, walk_enabled: None
) -> None:
    from itgraph.db.models import YtQuotaPool, YtRequest

    await _seed(database)
    await _listed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))

    async with client(database, FakeYouTube(videos=[_counts(1)])) as yt:
        await refresh_channels(yt, database)

    async with database.session() as session:
        booked = list(
            await session.execute(
                select(YtRequest.pool, YtRequest.command, YtRequest.method)
            )
        )
    assert booked == [(YtQuotaPool.GENERAL, YtCommand.REFRESH, "videos.list")]


# --- each run is recorded ------------------------------------------------


async def _runs(database: Database) -> list[Any]:
    from itgraph.db.models import YtRefreshRun

    async with database.session() as session:
        return list(
            await session.scalars(
                select(YtRefreshRun).order_by(YtRefreshRun.started_at)
            )
        )


def _fake_api(monkeypatch: pytest.MonkeyPatch, fake: FakeYouTube) -> None:
    original = yt_client.youtube

    def faked(database: Database, command: YtCommand) -> object:
        return original(
            database, command, transport=fake.transport(), sleep=no_sleep
        )

    monkeypatch.setattr(yt_client, "youtube", faked)


async def test_a_complete_run_is_recorded(
    database: Database, walk_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.db.models import YtPassOutcome
    from itgraph.yt.refresh import run_refresh

    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    _fake_api(monkeypatch, FakeYouTube(videos=[_counts(1)]))

    summary = await run_refresh(database)

    assert summary.stopped is None
    [run] = await _runs(database)
    assert run.outcome == YtPassOutcome.COMPLETE
    assert (run.late_due, run.late_read, run.error) == (1, 1, None)
    assert run.finished_at >= run.started_at


async def test_a_run_stopped_for_quota_is_recorded_as_such(
    database: Database, walk_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.db.models import YtPassOutcome
    from itgraph.yt.refresh import run_refresh

    await _seed(database)
    await _listed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    fake = FakeYouTube()
    fake.queued = [httpx.Response(403, json=error_body(403, "quotaExceeded"))]
    _fake_api(monkeypatch, fake)

    await run_refresh(database)

    [run] = await _runs(database)
    assert run.outcome == YtPassOutcome.EXHAUSTED
    assert (run.late_due, run.late_read) == (1, 0)
    assert "quotaExceeded" in (run.error or "")


async def test_a_run_refused_on_an_exhausted_day_is_recorded(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.db.models import YtPassOutcome
    from itgraph.yt.refresh import run_refresh

    await record_exhaustion(database, command=YtCommand.ADD)
    fake = FakeYouTube()
    _fake_api(monkeypatch, fake)

    summary = await run_refresh(database)

    assert not summary.attempted
    assert fake.requests == []
    [run] = await _runs(database)
    assert run.outcome == YtPassOutcome.EXHAUSTED


async def test_a_run_that_fails_is_recorded_as_failed(
    database: Database, walk_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.db.models import YtPassOutcome
    from itgraph.yt.refresh import run_refresh

    await _seed(database)
    await _listed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    fake = FakeYouTube()
    fake.queued = [httpx.Response(400, json=error_body(400, "badRequest"))]
    _fake_api(monkeypatch, fake)

    await run_refresh(database)

    [run] = await _runs(database)
    assert run.outcome == YtPassOutcome.FAILED
    assert run.error


async def test_what_a_message_shows_is_the_reading_not_the_payload(
    database: Database,
) -> None:
    """After a late reading the payload's own counts lag; what an alert
    and a weekly entry show (`video_evidence`, which `entry_evidence`
    wraps) is the newest reading."""
    from itgraph.db.yt_alerts import video_evidence
    from itgraph.db.yt_videos import store_readings
    from itgraph.derive.yt_videos import Counters

    await _seed(database)
    await _kept(database, 1, age=60, read_ago=timedelta(days=2))
    [scored] = await _readings_of(database, 1)
    async with database.session() as session:
        await store_readings(
            session,
            {video_id(1): Counters(views=5000, likes=40, comments=7)},
            observed_at=datetime.now(UTC),
        )
    row = await _video_row(database, 1)
    assert row.payload["statistics"]["viewCount"] == "100"

    async with database.session() as session:
        for short_max_seconds in (None, 180):
            evidence = await video_evidence(
                session,
                video_id=video_id(1),
                scored_at=scored.observed_at,
                short_max_seconds=short_max_seconds,
            )
            assert evidence is not None
            assert evidence.counters == Counters(
                views=5000, likes=40, comments=7
            )
            assert evidence.scored.views == 10


async def test_a_run_without_a_key_is_recorded_as_failed(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that could not start is a run too, and did not complete."""
    from itgraph.db.models import YtPassOutcome
    from itgraph.yt.refresh import run_refresh

    fake = FakeYouTube()
    _fake_api(monkeypatch, fake)
    monkeypatch.setattr(settings, "youtube_api_key", None)

    summary = await run_refresh(database)

    assert not summary.attempted
    assert fake.requests == []
    [run] = await _runs(database)
    assert run.outcome == YtPassOutcome.FAILED
    assert "YOUTUBE_API_KEY" in (run.error or "")
