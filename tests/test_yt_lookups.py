"""Asking YouTube whose a linked name is, and keeping the answer.

The requests name the narrowest fields there are; a missing video and a
handle nobody holds are answers too; the answers live in one table and
are read while current. Every id and handle is invented; nothing touches
a socket.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    no_sleep,
    use_key,
    video_id,
    video_resource,
)

from itgraph.db.models import YtCommand, YtLookupKind, YtRawLookup
from itgraph.db.session import Database
from itgraph.db.yt_lookups import (
    lookups_in_date,
    store_lookups,
)
from itgraph.yt import channels, videos
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.lookups import (
    UnacceptableChannelIdError,
    channel_of_handle,
    channel_of_legacy_name,
    channels_of_videos,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[YouTube]:
    async with youtube(
        database,
        YtCommand.TG_LOOKUP,
        transport=fake.transport(),
        sleep=no_sleep,
    ) as connected:
        yield connected


def _videos(*numbers: int, channel: int = 7) -> list[dict[str, object]]:
    return [
        video_resource(number, published=NOW, channel=channel)
        for number in numbers
    ]


# --- the requests name the narrowest fields --------------------------


async def test_a_linked_video_is_asked_for_its_channel_only(
    database: Database,
) -> None:
    fake = FakeYouTube(videos=_videos(1))
    async with client(database, fake) as yt:
        await channels_of_videos(yt, [video_id(1)])

    (call,) = fake.calls("videos")
    assert call["part"] == "snippet"
    assert call["fields"] == "items(id,snippet/channelId)"


async def test_a_linked_handle_or_name_is_asked_for_the_id_only(
    database: Database,
) -> None:
    fake = FakeYouTube(
        channel_resource(7, handle="fake-unknown"),
        legacy={"fakelegacy": channel_id(7)},
    )
    async with client(database, fake) as yt:
        await channel_of_handle(yt, "fake-unknown")
        await channel_of_legacy_name(yt, "fakelegacy")

    by_handle, by_name = fake.calls("channels")
    assert by_handle["forHandle"] == "@fake-unknown"
    assert by_name["forUsername"] == "fakelegacy"
    for call in (by_handle, by_name):
        assert call["part"] == "id"
        assert call["fields"] == "items(id)"


async def test_the_other_requests_are_unchanged(database: Database) -> None:
    """The import's handle lookup and the loop's videos keep their shape."""
    fake = FakeYouTube(
        channel_resource(7, handle="fake-seed"), videos=_videos(1)
    )
    async with client(database, fake) as yt:
        await channels.by_handle(yt, "fake-seed")
        await videos.by_ids(yt, [video_id(1)])

    (channel_call,) = fake.calls("channels")
    (video_call,) = fake.calls("videos")
    assert (channel_call["part"], channel_call["fields"]) == (
        channels.PARTS,
        channels.FIELDS,
    )
    assert (video_call["part"], video_call["fields"]) == (
        videos.PARTS,
        videos.FIELDS,
    )


# --- video lookups ---------------------------------------------------


async def test_a_missing_video_is_not_found(database: Database) -> None:
    fake = FakeYouTube(videos=_videos(*range(1, 50)))
    async with client(database, fake) as yt:
        answers = await channels_of_videos(
            yt, [video_id(n) for n in range(1, 51)]
        )

    assert len(fake.calls("videos")) == 1
    assert sum(1 for owner in answers.values() if owner == channel_id(7)) == 49
    assert answers[video_id(50)] is None


async def test_a_batch_answered_not_found_is_asked_id_by_id(
    database: Database,
) -> None:
    fake = FakeYouTube(videos=_videos(1, 2), batch_404=True)
    async with client(database, fake) as yt:
        answers = await channels_of_videos(
            yt, [video_id(1), video_id(2), video_id(3)]
        )

    assert len(fake.calls("videos")) == 4
    assert answers == {
        video_id(1): channel_id(7),
        video_id(2): channel_id(7),
        video_id(3): None,
    }


async def test_more_than_fifty_videos_is_a_caller_s_mistake(
    database: Database,
) -> None:
    async with client(database, FakeYouTube()) as yt:
        with pytest.raises(ValueError, match="at most 50"):
            await channels_of_videos(yt, [video_id(n) for n in range(51)])


# --- handle and legacy name lookups ----------------------------------


async def test_a_handle_names_a_channel(database: Database) -> None:
    fake = FakeYouTube(channel_resource(7, handle="fake-unknown"))
    async with client(database, fake) as yt:
        assert await channel_of_handle(yt, "fake-unknown") == channel_id(7)


async def test_a_handle_names_nobody(database: Database) -> None:
    async with client(database, FakeYouTube()) as yt:
        assert await channel_of_handle(yt, "fake-nobody") is None


async def test_a_legacy_name(database: Database) -> None:
    fake = FakeYouTube(
        channel_resource(7), legacy={"FakeLegacy": channel_id(7)}
    )
    async with client(database, fake) as yt:
        assert await channel_of_legacy_name(yt, "fakelegacy") == channel_id(7)


async def test_an_id_of_an_unexpected_shape_fails_loudly(
    database: Database,
) -> None:
    fake = FakeYouTube()
    fake.queued.append(
        httpx.Response(200, json={"items": [{"id": "HCnot-a-channel"}]})
    )
    async with client(database, fake) as yt:
        with pytest.raises(UnacceptableChannelIdError):
            await channel_of_handle(yt, "fake-odd")


# --- answers are kept, not refreshed ---------------------------------


async def test_answers_are_kept_found_or_not(database: Database) -> None:
    async with database.session() as session:
        await store_lookups(
            session,
            {
                (YtLookupKind.VIDEO, video_id(1)): channel_id(7),
                (YtLookupKind.HANDLE, "fake-nobody"): None,
            },
            fetched_at=NOW - timedelta(days=5),
        )
        kept = await lookups_in_date(session, now=NOW)

    assert kept == {
        (YtLookupKind.VIDEO, video_id(1)): channel_id(7),
        (YtLookupKind.HANDLE, "fake-nobody"): None,
    }


async def test_a_newer_answer_replaces_the_old_one(database: Database) -> None:
    key = (YtLookupKind.HANDLE, "fake-moved")
    async with database.session() as session:
        await store_lookups(
            session, {key: channel_id(7)}, fetched_at=NOW - timedelta(days=20)
        )
        await store_lookups(session, {key: channel_id(8)}, fetched_at=NOW)

    async with database.session() as session:
        rows = (await session.scalars(select(YtRawLookup))).all()
    assert [(row.channel_id, row.fetched_at) for row in rows] == [
        (channel_id(8), NOW)
    ]


async def test_a_lookup_holds_a_channel_id_only(database: Database) -> None:
    """The name, the answer, the moment — nothing else read from the API."""
    assert set(YtRawLookup.__table__.columns.keys()) == {
        "kind",
        "value",
        "channel_id",
        "fetched_at",
    }


async def test_an_old_lookup_is_kept_and_not_read(
    database: Database,
) -> None:
    async with database.session() as session:
        await store_lookups(
            session,
            {(YtLookupKind.VIDEO, video_id(1)): channel_id(7)},
            fetched_at=NOW - timedelta(days=31),
        )
        current = await lookups_in_date(session, now=NOW)
        then = await lookups_in_date(session, now=NOW - timedelta(days=20))

    assert current == {}
    assert then == {(YtLookupKind.VIDEO, video_id(1)): channel_id(7)}
