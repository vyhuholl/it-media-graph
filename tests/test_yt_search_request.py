"""One `search.list` page: what it asks, and the one field it reads."""

from datetime import UTC, datetime

import httpx
import pytest
from ytfakes import FakeYouTube, channel_id, no_sleep, use_key

from itgraph.db.models import YtCommand, YtSearchType
from itgraph.db.session import Database
from itgraph.yt import search
from itgraph.yt.client import youtube

AFTER = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


async def _page(
    database: Database,
    fake: FakeYouTube,
    kind: YtSearchType,
    *,
    published_after: datetime | None = None,
    token: str | None = None,
) -> search.Page:
    async with youtube(
        database, YtCommand.SEARCH, transport=fake.transport(), sleep=no_sleep
    ) as client:
        return await search.page(
            client,
            "fake query",
            kind,
            published_after=published_after,
            token=token,
        )


async def test_searching_videos(database: Database) -> None:
    fake = FakeYouTube(
        searches={("fake query", "video"): [channel_id(1), channel_id(2)]}
    )

    found = await _page(
        database, fake, YtSearchType.VIDEO, published_after=AFTER
    )

    assert found.channels == [channel_id(1), channel_id(2)]
    [asked] = fake.calls("search")
    assert asked == {
        "part": "snippet",
        "q": "fake query",
        "type": "video",
        "maxResults": "50",
        "relevanceLanguage": "en",
        "fields": "nextPageToken,items(snippet/channelId)",
        "publishedAfter": "2026-10-02T12:00:00Z",
    }


async def test_searching_channels(database: Database) -> None:
    fake = FakeYouTube(searches={("fake query", "channel"): [channel_id(3)]})

    found = await _page(database, fake, YtSearchType.CHANNEL)

    assert found.channels == [channel_id(3)]
    [asked] = fake.calls("search")
    assert asked["type"] == "channel"
    assert asked["fields"] == "nextPageToken,items(id/channelId)"
    assert "publishedAfter" not in asked


async def test_the_next_page_is_followed_by_its_token(
    database: Database,
) -> None:
    fake = FakeYouTube(
        searches={("fake query", "video"): [channel_id(n) for n in range(60)]}
    )

    first = await _page(database, fake, YtSearchType.VIDEO)
    second = await _page(
        database, fake, YtSearchType.VIDEO, token=first.next_token
    )

    assert len(first.channels) == 50 and first.next_token is not None
    assert len(second.channels) == 10 and second.next_token is None


async def test_nothing_but_the_channel_id_is_read(database: Database) -> None:
    """Should YouTube send the whole snippet, only the channel id is kept."""
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": {
                            "kind": "youtube#video",
                            "videoId": "fakevid0001",
                        },
                        "snippet": {
                            "channelId": channel_id(1),
                            "title": "Fake Title",
                            "channelTitle": "Fake Channel",
                        },
                    },
                    {"snippet": {"channelId": "not-a-channel"}},
                ]
            },
        )
    ]

    found = await _page(database, fake, YtSearchType.VIDEO)

    # The unreadable one keeps its place, so positions stay YouTube's.
    assert found.channels == [channel_id(1), None]
