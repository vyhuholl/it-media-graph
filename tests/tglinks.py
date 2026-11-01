"""Telegram seeds and their posts, for the Telegram-links tests.

Every channel, post, id and handle is invented: Telegram ids are small
numbers no channel has, usernames read `fake_tg_…`, and a post's one
link sits behind its first word, so no offset needs computing.
"""

from typing import Any

from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    RejectReason,
)
from itgraph.db.raw import store_messages
from itgraph.db.session import Database


def post(
    msg_id: int,
    url: str | None = None,
    *,
    date: str = "2026-10-01T09:00:00+00:00",
    forwarded: bool = False,
) -> dict[str, Any]:
    """A post in the stored shape, its one link behind its first word."""
    entities = (
        [{"_": "MessageEntityTextUrl", "offset": 0, "length": 4, "url": url}]
        if url
        else []
    )
    return {
        "_": "Message",
        "id": msg_id,
        "date": date,
        "message": "look at this",
        "entities": entities,
        "media": None,
        "fwd_from": (
            {
                "_": "MessageFwdHeader",
                "from_id": {"_": "PeerChannel", "channel_id": 9001},
            }
            if forwarded
            else None
        ),
    }


async def tg_channel(
    database: Database,
    tg_id: int,
    *,
    status: ChannelStatus = ChannelStatus.SEED,
    is_chat: bool = False,
) -> None:
    """A Telegram channel in the inventory, reviewed to ``status``."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(tg_id, f"fake_tg_{tg_id}", "Fake", is_chat)],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await mark_channel(
            session,
            tg_id,
            status=status,
            kind=ChannelKind.PERSONAL
            if status is ChannelStatus.SEED
            else None,
            reject_reason=(
                RejectReason.NOT_IT
                if status is ChannelStatus.REJECTED
                else None
            ),
        )


async def tg_posts(
    database: Database, tg_id: int, *posts: dict[str, Any]
) -> None:
    """Store posts under a Telegram channel."""
    async with database.session() as session:
        await store_messages(
            session,
            channel_id=tg_id,
            payloads={payload["id"]: payload for payload in posts},
        )


async def seed_linking(database: Database, tg_id: int, *urls: str) -> None:
    """A seed whose own posts link each URL, one post a link."""
    await tg_channel(database, tg_id)
    await tg_posts(
        database,
        tg_id,
        *(post(number, url) for number, url in enumerate(urls, start=1)),
    )
