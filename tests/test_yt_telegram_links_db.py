"""Loading the Telegram seeds' YouTube links out of the database.

Whose posts are read, how families are taken from the confirmed pairs,
what is named against which stored answer — and that reading writes
nothing. Every channel, post, id and handle is invented.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from tglinks import post, tg_channel, tg_posts
from ytfakes import channel_id, channel_resource, video_id

from itgraph.db.channels import confirm_affiliation
from itgraph.db.models import (
    ChannelStatus,
    RawMessage,
    YtChannel,
    YtLookupKind,
    YtRawLookup,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_telegram_links import TelegramLinks, read_telegram_links
from itgraph.derive.tg_youtube import Name, NameKind

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
TARGET = channel_id(50)


async def _read(database: Database) -> TelegramLinks:
    async with database.session() as session:
        return await read_telegram_links(session, now=NOW)


LINK = f"https://www.youtube.com/channel/{TARGET}"

POSTS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "tg_youtube_posts.json").read_text(
        encoding="utf-8"
    )
)


# --- sources ---------------------------------------------------------


async def test_channels_out_of_scope_are_not_sources(
    database: Database,
) -> None:
    for tg_id, status in [
        (2001, ChannelStatus.CANDIDATE),
        (2002, ChannelStatus.MAYBE),
        (2003, ChannelStatus.REJECTED),
    ]:
        await tg_channel(database, tg_id, status=status)
        await tg_posts(database, tg_id, post(1, LINK))
    await tg_channel(database, 2004, is_chat=True)
    await tg_posts(database, 2004, post(1, LINK))

    links = await _read(database)

    assert links.reading.channels == {}
    assert links.reading.scanned.posts == 0


async def test_a_forwarded_post_is_not_read(database: Database) -> None:
    await tg_channel(database, 2001)
    await tg_posts(database, 2001, post(1, LINK, forwarded=True))

    links = await _read(database)

    assert links.reading.channels == {}
    assert links.reading.scanned.posts == 0


async def test_old_posts_count(database: Database) -> None:
    await tg_channel(database, 2001)
    await tg_posts(
        database, 2001, post(1, LINK, date="2017-03-01T09:00:00+00:00")
    )

    links = await _read(database)

    assert links.reading.channels[TARGET].families == {2001}


async def test_every_own_post_is_counted_whether_it_links_or_not(
    database: Database,
) -> None:
    await tg_channel(database, 2001)
    await tg_channel(database, 2002)
    await tg_posts(database, 2001, post(1, LINK), post(2), post(3))
    await tg_posts(database, 2002, post(1))

    scanned = (await _read(database)).reading.scanned

    assert (scanned.posts, scanned.seeds, scanned.families) == (4, 2, 2)


async def test_every_place_a_link_is_read_survives_the_query(
    database: Database,
) -> None:
    """The SQL selects and filters on the text, entities and preview URL.

    A link marked in the text after an emoji, a link only in the preview,
    a percent-encoded handle behind text, and the counted forms — each
    has to come through the filter and the selected fields, not only
    through the reader.
    """
    await tg_channel(database, 2001)
    await tg_posts(
        database,
        2001,
        POSTS["emoji_url"],
        POSTS["preview_only"],
        POSTS["cyrillic_handle"],
        POSTS["counted_forms"],
    )

    reading = (await _read(database)).reading

    assert set(reading.awaiting) == {
        Name(NameKind.HANDLE, "fake-seed-one"),
        Name(NameKind.VIDEO, "fakevid0002"),
        Name(NameKind.HANDLE, "фейк-канал"),
    }
    assert set(reading.custom_urls) == {"fakecustom", "fakevanity"}
    assert reading.counted["playlist pages"] == 1
    assert reading.counted["shortened links"] == 1
    assert reading.scanned.posts == 4


# --- families --------------------------------------------------------


async def test_two_affiliated_seeds_are_one_family(database: Database) -> None:
    for tg_id in (2001, 2002, 2003):
        await tg_channel(database, tg_id)
        await tg_posts(database, tg_id, post(1, LINK))
    async with database.session() as session:
        await confirm_affiliation(session, [2001, 2002])

    links = await _read(database)

    assert len(links.reading.channels[TARGET].families) == 2


# --- what names a channel --------------------------------------------


async def test_a_stored_payload_s_handle_and_the_inventory(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        await store_payloads(
            session,
            {channel_id(1): channel_resource(1, handle="fake-seed-one")},
            fetched_at=NOW,
        )
    await tg_channel(database, 2001)
    await tg_posts(database, 2001, post(1, "youtube.com/@Fake-Seed-One"))

    links = await _read(database)

    assert channel_id(1) in links.known.inventory
    assert links.reading.channels[channel_id(1)].families == {2001}
    assert links.reading.awaiting == {}


async def test_a_lookup_in_date_names_and_an_expired_one_does_not(
    database: Database,
) -> None:
    async with database.session() as session:
        await store_lookups(
            session,
            {(YtLookupKind.VIDEO, video_id(1)): TARGET},
            fetched_at=NOW - timedelta(days=10),
        )
        await store_lookups(
            session,
            {(YtLookupKind.VIDEO, video_id(2)): TARGET},
            fetched_at=NOW - timedelta(days=31),
        )
    await tg_channel(database, 2001)
    await tg_channel(database, 2002)
    await tg_posts(database, 2001, post(1, f"youtu.be/{video_id(1)}"))
    await tg_posts(database, 2002, post(1, f"youtu.be/{video_id(2)}"))

    links = await _read(database)

    assert links.reading.channels[TARGET].families == {2001}
    assert Name(NameKind.VIDEO, video_id(2)) in links.reading.awaiting


# --- nothing joined is stored ----------------------------------------


async def test_reading_writes_nothing(database: Database) -> None:
    await tg_channel(database, 2001)
    await tg_posts(
        database, 2001, post(1, LINK), post(2, "youtube.com/@fake-x")
    )

    async def counts() -> tuple[int, ...]:
        async with database.session() as session:
            return tuple(
                [
                    await session.scalar(
                        select(func.count()).select_from(model)
                    )
                    or 0
                    for model in (RawMessage, YtChannel, YtRawLookup)
                ]
            )

    before = await counts()
    await _read(database)
    assert await counts() == before
