"""The resolution pass: the only part of the graph that talks to Telegram.

Derivation discovers channels by reference but cannot describe them — a
forward leaves a bare id, a mention a bare username. This turns those
handles into identities, and it is the one place in the change subject to
the pacing and FloodWait rules that govern every networked part of the
project: sequential requests, a delay between them, a rate limit waited
out rather than worked around.

Two queues, resolved the two ways their handles allow. A channel
discovered by forward is resolved by id, through the ``access_hash`` the
session cached when it first saw the entity — which is why a failure here
is provisional: a later backfill may teach the session a hash it lacked.
A pending mention is resolved by public username lookup, which needs no
hash. A pending username that turns out to be a channel becomes a
channel row, and the *next* derivation run writes the edge that was
waiting on it.

Whatever resolves to a user or a bot rather than a channel is recorded as
such and creates no channel row: this graph holds channels only.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.types import Channel as TLChannel
from telethon.tl.types import PeerChannel

from itgraph.config import settings
from itgraph.db.channels import (
    DiscoveredChannel,
    channels_awaiting_resolution,
    create_resolved_channel,
    record_channel_resolve_failure,
    record_channel_resolved,
)
from itgraph.db.edges import (
    delete_pending_mention,
    pending_mentions_to_resolve,
    record_pending_failure,
)
from itgraph.db.models import DiscoverySource
from itgraph.db.session import Database
from itgraph.tg.backfill import waiting_out_floods

__all__ = ["ResolveSummary", "channel_identity", "resolve_inventory"]

logger = logging.getLogger(__name__)

# The same failures the collector treats as retry-worthy or not. A lookup
# raising one of these means the entity is gone or was never reachable;
# anything else, including a bare `ValueError` from an id the session
# cannot place, is caught the same way and recorded as an attempt.
_LOOKUP_ERRORS = (RPCError, OSError, ValueError, TypeError)


@dataclass(slots=True)
class ResolveSummary:
    """What a resolution run did, across both queues."""

    resolved: int = 0
    discovered: int = 0
    not_channels: int = 0
    failed: int = 0

    def line(self) -> str:
        return (
            f"resolved {self.resolved}, discovered {self.discovered}, "
            f"not channels {self.not_channels}, failed {self.failed}"
        )


def channel_identity(entity: Any) -> DiscoveredChannel | None:
    """A resolved entity as an inventory identity, or ``None`` if not one.

    Only a ``Channel`` — a broadcast channel or a supergroup — belongs in
    this graph. A ``User`` (bot or person) and a legacy ``Chat`` resolve
    to ``None``, so the caller records the reference as not-a-channel and
    creates nothing. A supergroup is stored as a chat: that is the column
    the comments phase reads.
    """
    if not isinstance(entity, TLChannel):
        return None
    return DiscoveredChannel(
        tg_id=entity.id,
        username=getattr(entity, "username", None),
        title=getattr(entity, "title", None),
        is_chat=bool(getattr(entity, "megagroup", False)),
    )


async def resolve_inventory(
    client: TelegramClient,
    database: Database,
    *,
    retry_failed: bool = False,
    delay: float | None = None,
    limit: int | None = None,
) -> ResolveSummary:
    """Work both resolution queues, paced and one request at a time.

    Channels-by-id first, then pending usernames. ``limit`` bounds the
    whole run — total requests across both queues — so a cautious operator
    can spend a fixed number of requests and stop. Every request is paced
    by ``delay`` and passes through the collector's FloodWait handling.
    """
    pause = delay if delay is not None else settings.backfill_request_delay
    summary = ResolveSummary()
    remaining = limit

    async with database.session() as session:
        channels = await channels_awaiting_resolution(
            session, retry_failed=retry_failed, limit=remaining
        )
        for channel in channels:
            if remaining is not None and remaining <= 0:
                break
            await asyncio.sleep(pause)
            await _resolve_channel(client, session, channel.tg_id, summary)
            if remaining is not None:
                remaining -= 1

        pending = await pending_mentions_to_resolve(
            session, retry_failed=retry_failed, limit=remaining
        )
        for mention in pending:
            if remaining is not None and remaining <= 0:
                break
            await asyncio.sleep(pause)
            await _resolve_pending(client, session, mention.username, summary)
            if remaining is not None:
                remaining -= 1

    logger.info("resolution done: %s", summary.line())
    return summary


async def _resolve_channel(
    client: TelegramClient,
    session: AsyncSession,
    tg_id: int,
    summary: ResolveSummary,
) -> None:
    """Resolve one channel by id, through the session's cached hash."""
    try:
        entity = await waiting_out_floods(
            lambda: client.get_entity(PeerChannel(tg_id))
        )
    except _LOOKUP_ERRORS as exc:
        logger.warning("channel %d did not resolve: %s", tg_id, exc)
        await record_channel_resolve_failure(session, tg_id, str(exc))
        await session.commit()
        summary.failed += 1
        return

    identity = channel_identity(entity)
    if identity is None:
        await record_channel_resolve_failure(
            session, tg_id, "resolved to a user or bot, not a channel"
        )
        await session.commit()
        summary.not_channels += 1
        return

    await record_channel_resolved(
        session,
        tg_id,
        username=identity.username,
        title=identity.title,
        is_chat=identity.is_chat,
    )
    await session.commit()
    summary.resolved += 1


async def _resolve_pending(
    client: TelegramClient,
    session: AsyncSession,
    username: str,
    summary: ResolveSummary,
) -> None:
    """Resolve one pending username by public lookup."""
    try:
        entity = await waiting_out_floods(lambda: client.get_entity(username))
    except _LOOKUP_ERRORS as exc:
        logger.warning("@%s did not resolve: %s", username, exc)
        await record_pending_failure(session, username, str(exc))
        await session.commit()
        summary.failed += 1
        return

    identity = channel_identity(entity)
    if identity is None:
        # A person or a bot, not a channel: kept out of the inventory, and
        # marked so a routine re-run does not ask about it again.
        await record_pending_failure(
            session, username, "resolved to a user or bot, not a channel"
        )
        await session.commit()
        summary.not_channels += 1
        return

    await create_resolved_channel(
        session,
        # The lookup's own spelling of the username wins; the pending key
        # is only a fallback for the rare channel that answers to a
        # lookup but reports no username of its own.
        channel=DiscoveredChannel(
            tg_id=identity.tg_id,
            username=identity.username or username,
            title=identity.title,
            is_chat=identity.is_chat,
        ),
        discovered_via=DiscoverySource.MENTION,
    )
    await delete_pending_mention(session, username)
    await session.commit()
    summary.discovered += 1
