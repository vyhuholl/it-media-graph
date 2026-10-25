"""Asking for videos by id: fifty to a request, declared fields, dead ids absent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from ytfakes import FakeYouTube, no_sleep, use_key, video_id, video_resource

from itgraph.db.models import YtCommand
from itgraph.db.session import Database
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.videos import FIELDS, PARTS, by_ids

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[YouTube]:
    async with youtube(
        database, YtCommand.WATCH, transport=fake.transport(), sleep=no_sleep
    ) as connected:
        yield connected


def _fake(*numbers: int, batch_404: bool = False) -> FakeYouTube:
    return FakeYouTube(
        videos=[video_resource(n, published=NOW) for n in numbers],
        batch_404=batch_404,
    )


async def test_sixty_ids_make_two_requests(database: Database) -> None:
    fake = _fake(*range(60))

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(n) for n in range(60)])

    assert len(found) == 60
    calls = fake.calls("videos")
    assert [len(call["id"].split(",")) for call in calls] == [50, 10]
    for call in calls:
        assert call["part"] == PARTS
        assert call["fields"] == FIELDS


async def test_the_declared_fields_and_no_others() -> None:
    """What the privacy policy lists, and nothing it does not."""
    for wanted in (
        "publishedAt",
        "channelId",
        "title",
        "description",
        "liveBroadcastContent",
        "duration",
        "actualStartTime",
        "scheduledStartTime",
        "viewCount",
        "likeCount",
        "commentCount",
    ):
        assert wanted in FIELDS
    for unwanted in (
        "tags",
        "thumbnails",
        "categoryId",
        "localized",
        "concurrentViewers",
    ):
        assert unwanted not in FIELDS


async def test_a_missing_video_is_absent(database: Database) -> None:
    fake = _fake(1)

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(1), video_id(2)])

    assert list(found) == [video_id(1)]
    assert len(fake.requests) == 1


async def test_a_404_batch_still_yields_every_live_video(
    database: Database,
) -> None:
    fake = _fake(1, 3, batch_404=True)

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(1), video_id(2), video_id(3)])

    assert sorted(found) == [video_id(1), video_id(3)]
    # The batch, then one request per id.
    assert len(fake.requests) == 4
