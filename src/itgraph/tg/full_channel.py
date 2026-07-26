"""The per-channel metadata pass: one request, when it is due.

``GetFullChannelRequest`` answers two questions at once — what a
channel's description says, and which discussion chat belongs to it.

It is not cheap in the way that matters. It is never cached, so it is a
network call every time, and it is one of the methods that carries a
per-day quota. Over two hundred channels that is two hundred quota-bearing
requests per run, spent to re-read a description and a linked chat that
change on the order of months. So the caller runs this only when what it
holds is absent or older than ``channel_metadata_max_age_days``, and
takes the peer from the session's entity cache the rest of the time. See
``backfill._resolve_peer``.

That skip gives something up. This pass ran before any history partly as
a reachability probe: an inaccessible channel cost one request here
rather than failing part-way through a long walk. When it is skipped the
first history request finds out instead — one request either way, and
``backfill.classify`` reaches the same verdict about whether to try again.
The probe was cheap insurance, not a guarantee, and it was not worth two
hundred requests a run to keep.

The payload is stored as it arrives. The external links in a channel's
description are extracted later, from that payload, by code that has to
stay re-runnable — not here.
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest

from itgraph.db.channels import DiscoveredChannel, link_discussion_chat
from itgraph.db.raw import store_channel_payload
from itgraph.tg.payload import encode_payload

__all__ = ["ChannelMetadata", "fetch_full_channel"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelMetadata:
    """What one metadata pass established, and the entity it resolved.

    The entity is handed back so the history walk that follows does not
    resolve the same username a second time. Telethon caches resolutions
    in its session file, so the second call would be cheap — but it would
    still be a request on a path where every request is counted.
    """

    tg_id: int
    entity: Any
    linked_chat_id: int | None


def _discussion_chat(
    result: Any, linked_chat_id: int
) -> DiscoveredChannel | None:
    """The linked chat, as the same response already described it.

    The response carries the chat alongside the channel, so there is no
    second request to make here. A chat that is somehow absent is worth a
    warning and nothing more: the link is a convenience, and refusing to
    store the payload over it would lose far more than it saved.
    """
    for chat in getattr(result, "chats", []):
        if chat.id != linked_chat_id:
            continue
        return DiscoveredChannel(
            tg_id=chat.id,
            username=getattr(chat, "username", None),
            title=getattr(chat, "title", None),
            # It is a discussion group; that is the whole point of it.
            is_chat=True,
        )
    return None


async def fetch_full_channel(
    client: TelegramClient, session: AsyncSession, *, username: str
) -> ChannelMetadata:
    """Fetch, store and interpret nothing but the linked chat.

    Resolution is by username: ``access_hash`` is issued per account, and
    the account that built the inventory is not the one collecting, so
    the inventory holds no hash to reuse. Telethon caches the resolution
    in its session file, which is why that file is worth keeping.

    The channel must already be in ``channels``; ``raw_channels`` has a
    foreign key onto it. That is not a burden in practice — the metadata
    pass runs only over reviewed, in-scope channels — and it is what
    stops a payload arriving for a channel nothing ever decided about.
    """
    entity = await client.get_entity(username)
    result = await client(GetFullChannelRequest(entity))

    await store_channel_payload(
        session, channel_id=entity.id, payload=encode_payload(result)
    )

    linked_chat_id: int | None = getattr(
        result.full_chat, "linked_chat_id", None
    )
    if linked_chat_id is not None:
        chat = _discussion_chat(result, linked_chat_id)
        if chat is None:
            logger.warning(
                "@%s names linked chat %d, absent from the response",
                username,
                linked_chat_id,
            )
        else:
            await link_discussion_chat(
                session, parent_tg_id=entity.id, chat=chat
            )

    return ChannelMetadata(
        tg_id=entity.id, entity=entity, linked_chat_id=linked_chat_id
    )
