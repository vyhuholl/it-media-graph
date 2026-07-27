"""The per-channel metadata fetch: one request, when it is due.

``GetFullChannelRequest`` answers two questions at once — what a
channel's description says, and which discussion chat belongs to it.

It is not cheap in the way that matters. It is never cached, so it is a
network call every time, and it is one of the methods that carries a
per-day quota. Over two hundred channels that is two hundred
quota-bearing requests, spent to re-read a description and a linked chat
that change on the order of months. That is why this is no longer part of
walking a channel's history: it is its own pass, with its own budget, run
on its own cadence. See ``tg/metadata.py``.

Nothing here resolves a username, and that is the point rather than an
optimisation. ``GetFullChannelRequest.resolve`` calls
``client.get_input_entity`` on whatever it is handed; a username sends
that out to ``contacts.resolveUsername``, which carries the tightest
daily quota in the project, while an input peer short-circuits on the
first line and costs nothing. So this takes a peer, and the caller is the
one that decides where a peer comes from.

The channel's own identity is read back out of the response. Telegram
returns the ``Channel`` alongside the ``ChannelFull``, in the same
``chats`` list the linked chat arrives in, so asking who this channel is
was never worth a second request.

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
    """What one metadata fetch established.

    Identity travels with it because the response carried it anyway — the
    caller would otherwise have to ask who it just fetched, and the only
    way to ask is the request this whole change exists to stop making.
    """

    tg_id: int
    username: str | None
    title: str | None
    linked_chat_id: int | None


def _chat_from(result: Any, chat_id: int) -> Any | None:
    """The chat with this id, out of the ones the response carried.

    Telegram answers ``GetFullChannelRequest`` with the channel itself
    and its linked discussion chat in one ``chats`` list, so both the
    identity and the link are already in hand and neither costs a second
    request.
    """
    for chat in getattr(result, "chats", []):
        if chat.id == chat_id:
            return chat
    return None


def _discussion_chat(
    result: Any, linked_chat_id: int
) -> DiscoveredChannel | None:
    """The linked chat, as the same response already described it.

    A chat that is somehow absent is worth a warning and nothing more:
    the link is a convenience, and refusing to store the payload over it
    would lose far more than it saved.
    """
    chat = _chat_from(result, linked_chat_id)
    if chat is None:
        return None
    return DiscoveredChannel(
        tg_id=chat.id,
        username=getattr(chat, "username", None),
        title=getattr(chat, "title", None),
        # It is a discussion group; that is the whole point of it.
        is_chat=True,
    )


async def fetch_full_channel(
    client: TelegramClient, session: AsyncSession, *, peer: Any
) -> ChannelMetadata:
    """Fetch, store and interpret nothing but the linked chat.

    Takes a peer rather than a username, and the difference is the whole
    reason this function was touched. ``GetFullChannelRequest.resolve``
    calls ``client.get_input_entity`` on its argument: a string leaves
    the cache behind and reaches ``contacts.resolveUsername``, while an
    input peer is returned unchanged on the first line. One argument's
    type is the difference between one quota-bearing request and two.

    Which id this turns out to be is read from the response rather than
    assumed from the peer, because a peer is a pair of numbers and the
    response is Telegram's own answer to who that pair belongs to.

    The channel must already be in ``channels``; ``raw_channels`` has a
    foreign key onto it. That is not a burden in practice — the metadata
    pass runs only over reviewed, in-scope channels — and it is what
    stops a payload arriving for a channel nothing ever decided about.
    """
    result = await client(GetFullChannelRequest(peer))
    tg_id: int = result.full_chat.id

    await store_channel_payload(
        session, channel_id=tg_id, payload=encode_payload(result)
    )

    channel = _chat_from(result, tg_id)
    username: str | None = getattr(channel, "username", None)
    title: str | None = getattr(channel, "title", None)

    linked_chat_id: int | None = getattr(
        result.full_chat, "linked_chat_id", None
    )
    if linked_chat_id is not None:
        chat = _discussion_chat(result, linked_chat_id)
        if chat is None:
            logger.warning(
                "%s names linked chat %d, absent from the response",
                f"@{username}" if username else tg_id,
                linked_chat_id,
            )
        else:
            await link_discussion_chat(session, parent_tg_id=tg_id, chat=chat)

    return ChannelMetadata(
        tg_id=tg_id,
        username=username,
        title=title,
        linked_chat_id=linked_chat_id,
    )
