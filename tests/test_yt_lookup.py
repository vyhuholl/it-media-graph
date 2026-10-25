"""Asking YouTube for channels: fifty ids a call, one handle a call."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    no_sleep,
    use_key,
)

from itgraph.db.models import YtCommand
from itgraph.db.session import Database
from itgraph.yt.channels import FIELDS, PARTS, by_handle, by_ids, by_username
from itgraph.yt.client import YouTube, youtube


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[YouTube]:
    async with youtube(
        database, YtCommand.ADD, transport=fake.transport(), sleep=no_sleep
    ) as connected:
        yield connected


async def test_a_hundred_ids_make_two_requests(database: Database) -> None:
    fake = FakeYouTube(*(channel_resource(n) for n in range(100)))

    async with client(database, fake) as yt:
        found = await by_ids(yt, [channel_id(n) for n in range(100)])

    assert len(found) == 100
    assert len(fake.requests) == 2
    first = fake.params()[0]
    assert len(first["id"].split(",")) == 50
    # It defaults to five; whether it caps an id lookup is undocumented.
    assert first["maxResults"] == "50"
    assert first["part"] == PARTS
    assert first["fields"] == FIELDS


async def test_an_id_youtube_does_not_return_is_absent(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1))

    async with client(database, fake) as yt:
        found = await by_ids(yt, [channel_id(1), channel_id(2)])

    assert list(found) == [channel_id(1)]
    assert len(fake.requests) == 1


async def test_a_404_batch_still_yields_every_live_channel(
    database: Database,
) -> None:
    """If one dead id fails the whole call, each id is asked alone."""
    fake = FakeYouTube(
        channel_resource(1), channel_resource(3), batch_404=True
    )

    async with client(database, fake) as yt:
        found = await by_ids(yt, [channel_id(1), channel_id(2), channel_id(3)])

    assert sorted(found) == [channel_id(1), channel_id(3)]
    # The batch, then each of the three alone.
    assert len(fake.requests) == 4


async def test_a_single_dead_id_costs_one_request(database: Database) -> None:
    fake = FakeYouTube(batch_404=True)

    async with client(database, fake) as yt:
        assert await by_ids(yt, [channel_id(2)]) == {}

    assert len(fake.requests) == 1


async def test_repeated_ids_are_asked_for_once(database: Database) -> None:
    fake = FakeYouTube(channel_resource(1))

    async with client(database, fake) as yt:
        await by_ids(yt, [channel_id(1), channel_id(1)])

    assert fake.params()[0]["id"] == channel_id(1)


async def test_a_handle_is_asked_for_with_its_at(database: Database) -> None:
    fake = FakeYouTube(channel_resource(1, handle="FakeCaps"))

    async with client(database, fake) as yt:
        found = await by_handle(yt, "fakecaps")

    assert found is not None and found["id"] == channel_id(1)
    assert fake.params()[0]["forHandle"] == "@fakecaps"


async def test_a_handle_with_no_channel_is_none(database: Database) -> None:
    """No `items` at all: one of the two shapes "nothing" may take."""
    fake = FakeYouTube()

    async with client(database, fake) as yt:
        assert await by_handle(yt, "nobody") is None


async def test_a_legacy_name(database: Database) -> None:
    fake = FakeYouTube(channel_resource(1), legacy={"OldName": channel_id(1)})

    async with client(database, fake) as yt:
        found = await by_username(yt, "OldName")
        missing = await by_username(yt, "NoSuchName")

    assert found is not None and found["id"] == channel_id(1)
    assert missing is None
    assert fake.params()[0]["forUsername"] == "OldName"


async def test_every_lookup_asks_for_the_declared_fields_only(
    database: Database,
) -> None:
    """The privacy policy lists what is read; `fields` is what holds the
    request to that list inside the parts."""
    fake = FakeYouTube(
        channel_resource(1, handle="fakeone"),
        channel_resource(2),
        legacy={"OldName": channel_id(2)},
        batch_404=True,
    )

    async with client(database, fake) as yt:
        await by_ids(yt, [channel_id(1), channel_id(3)])
        await by_handle(yt, "fakeone")
        await by_username(yt, "OldName")

    # The 404 batch, its two retries, the handle and the legacy name.
    assert len(fake.requests) == 5
    for params in fake.params():
        assert params["part"] == "snippet,contentDetails,statistics"
        assert params["fields"] == FIELDS
    assert "brandingSettings" not in FIELDS
    assert "thumbnails" not in FIELDS
