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
    count_pending_mention_sources,
    delete_pending_mention,
    pending_mentions_to_resolve,
    record_pending_failure,
)
from itgraph.db.models import CollectionCommand, DiscoverySource
from itgraph.db.session import Database
from itgraph.tg.backfill import FloodWaitTooLong, waiting_out_floods
from itgraph.tg.client import persist_peers
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.pacing import pace

__all__ = ["ResolveSummary", "channel_identity", "resolve_inventory"]

logger = logging.getLogger(__name__)

# The same failures the collector treats as retry-worthy or not. A lookup
# raising one of these means the entity is gone or was never reachable;
# anything else, including a bare `ValueError` from an id the session
# cannot place, is caught the same way and recorded as an attempt.
_LOOKUP_ERRORS = (RPCError, OSError, ValueError, TypeError)


@dataclass(slots=True)
class ResolveSummary:
    """What a resolution run did, across both queues.

    ``halt`` is set when a rate limit stopped the run short. Everything
    resolved before that point is committed and counted here.
    """

    resolved: int = 0
    discovered: int = 0
    not_channels: int = 0
    failed: int = 0
    halt: FloodWaitTooLong | None = None

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
    min_sources: int | None = None,
) -> ResolveSummary:
    """Work both resolution queues, paced and one request at a time.

    Channels-by-id first, then pending usernames. ``limit`` bounds the
    whole run — total requests across both queues — so a cautious operator
    can spend a fixed number of requests and stop. Every request is paced
    the same way the collector's are, and passes through the collector's
    FloodWait handling.

    The username queue is worked most-mentioned first: it is the only part
    of this project that spends ``contacts.resolveUsername``, which is
    rationed by the day and cannot be batched, and the majority of a real
    queue is mentioned by one channel apiece. ``min_sources`` bounds it by
    evidence rather than by count — the head of the queue without having
    to know how long the head is.

    A rate limit too long to sit through stops the whole run, both queues
    with it. What was resolved first is committed and reported.
    """
    pause = delay if delay is not None else settings.backfill_request_delay
    summary = ResolveSummary()
    remaining = limit
    # No channel is attributed: resolution walks references, not
    # channels, and the id it is asking about is not one the inventory
    # necessarily holds yet.
    recorder = FloodRecorder(database, CollectionCommand.RESOLVE)

    async with database.session() as session:
        channels = await channels_awaiting_resolution(
            session, retry_failed=retry_failed, limit=remaining
        )
        try:
            for channel in channels:
                if remaining is not None and remaining <= 0:
                    break
                await pace(pause)
                await _resolve_channel(
                    client, session, channel.tg_id, summary, recorder
                )
                if remaining is not None:
                    remaining -= 1

            # Queried only now: `remaining` has to reflect what the first
            # queue already spent, or `--limit` bounds each queue instead
            # of the run.
            pending = await pending_mentions_to_resolve(
                session,
                retry_failed=retry_failed,
                limit=remaining,
                min_sources=min_sources,
            )
            if pending and not await count_pending_mention_sources(session):
                # No sources at all against a non-empty queue means
                # derivation has not run since the table appeared — not
                # that nothing mentions these. The two are identical in
                # the ordering and opposite in meaning, so say which.
                logger.warning(
                    "no mention sources recorded: run `itgraph derive` to "
                    "fill them, or this queue stays in arrival order"
                )
            for mention in pending:
                if remaining is not None and remaining <= 0:
                    break
                # The count is what decided the order, so a reader of the
                # log can see the priority rather than infer it.
                logger.info(
                    "resolving @%s (mentioned by %d channel%s)",
                    mention.username,
                    mention.sources,
                    "" if mention.sources == 1 else "s",
                )
                await pace(pause)
                await _resolve_pending(
                    client, session, mention.username, summary, recorder
                )
                if remaining is not None:
                    remaining -= 1
        except FloodWaitTooLong as exc:
            logger.warning("%s", exc)
            await session.rollback()
            summary.halt = exc

    logger.info("resolution done: %s", summary.line())
    return summary


async def _resolve_channel(
    client: TelegramClient,
    session: AsyncSession,
    tg_id: int,
    summary: ResolveSummary,
    recorder: FloodRecorder,
) -> None:
    """Resolve one channel by id, through the session's cached hash.

    Clears any pending mention this resolution has just answered. A
    channel found both ways — forwarded from in one message, named in
    another — used to resolve by this cheap path and leave the expensive
    path's row behind for good, because only ``_resolve_pending`` deleted
    from that queue. Those rows survive as requests that can only return a
    channel the inventory already has, and each one costs a
    ``contacts.resolveUsername``.
    """
    try:
        entity = await waiting_out_floods(
            lambda: client.get_entity(PeerChannel(tg_id)), recorder
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
    if identity.username:
        # Normalised the way the queue stores it, or the delete silently
        # matches nothing for any channel Telegram spells with capitals.
        await delete_pending_mention(session, identity.username.lower())
    # Before the database commit, never after: see `persist_peers`.
    await persist_peers(client)
    await session.commit()
    summary.resolved += 1


async def _resolve_pending(
    client: TelegramClient,
    session: AsyncSession,
    username: str,
    summary: ResolveSummary,
    recorder: FloodRecorder,
) -> None:
    """Resolve one pending username by public lookup."""
    try:
        entity = await waiting_out_floods(
            lambda: client.get_entity(username), recorder
        )
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
    # Before the database commit, never after: see `persist_peers`.
    await persist_peers(client)
    await session.commit()
    summary.discovered += 1
