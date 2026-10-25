"""Listing a channel's uploads: one page, ids and dates, the channel's own failures."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from ytfakes import (
    FakeYouTube,
    error_body,
    no_sleep,
    upload_item,
    uploads_id,
    use_key,
    video_id,
)

from itgraph.db.models import YtCommand
from itgraph.db.session import Database
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.errors import ApiError, ListingFailed
from itgraph.yt.uploads import FIELDS, PART, newest_uploads

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


async def test_the_newest_page_and_nothing_else(database: Database) -> None:
    items = [
        upload_item(n, published=NOW - timedelta(hours=n)) for n in range(60)
    ]
    fake = FakeYouTube(uploads={uploads_id(1): items})

    async with client(database, fake) as yt:
        found = await newest_uploads(yt, uploads_id(1))

    assert len(found) == 50
    assert found[0].video_id == video_id(0)
    assert found[0].published_at == NOW
    [params] = fake.calls("playlistItems")
    assert params == {
        "part": PART,
        "fields": FIELDS,
        "playlistId": uploads_id(1),
        "maxResults": "50",
    }
    assert FIELDS == "items/contentDetails(videoId,videoPublishedAt)"


async def test_an_item_without_a_date_is_kept_without_one(
    database: Database,
) -> None:
    fake = FakeYouTube(
        uploads={uploads_id(1): [upload_item(1, published=None)]}
    )

    async with client(database, fake) as yt:
        [found] = await newest_uploads(yt, uploads_id(1))

    assert (found.video_id, found.published_at) == (video_id(1), None)


async def test_a_playlist_not_found_is_the_channels_failure(
    database: Database,
) -> None:
    fake = FakeYouTube()

    async with client(database, fake) as yt:
        with pytest.raises(ListingFailed, match="404"):
            await newest_uploads(yt, uploads_id(1))


async def test_a_playlist_not_accessible_is_the_channels_failure(
    database: Database,
) -> None:
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(403, json=error_body(403, "playlistItemsNotAccessible"))
    ]

    async with client(database, fake) as yt:
        with pytest.raises(ListingFailed, match="playlistItemsNotAccessible"):
            await newest_uploads(yt, uploads_id(1))

    assert len(fake.requests) == 1


async def test_a_bad_request_stops_the_pass(database: Database) -> None:
    """A 400 is our mistake — a `fields` string YouTube refused — and
    says nothing about the channel."""
    fake = FakeYouTube()
    fake.queued = [httpx.Response(400, json=error_body(400, "invalidFields"))]

    async with client(database, fake) as yt:
        with pytest.raises(ApiError) as caught:
            await newest_uploads(yt, uploads_id(1))

    assert not isinstance(caught.value, ListingFailed)
