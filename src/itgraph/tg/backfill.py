"""The history walk: the one place in this project that spends requests.

Two rules shape everything here. A run must survive being interrupted,
so the cursor advances in the same transaction as the batch it describes
and never ahead of it. And a run must never look like a bot in a hurry,
so channels are walked strictly one at a time, with a delay between
requests, and every rate limit is answered by waiting.

Nothing is derived. Messages arrive, are re-encoded into something
``jsonb`` accepts, and are stored. Forward edges, mentions, links and
language are all computed later, from these rows.

What a channel is walked back to is the earlier of two bounds: the
cutoff the operator asked for, and the ceiling on how many messages one
channel may contribute at all. The second exists because a few
high-volume aggregators would otherwise be most of the corpus while
saying the least about who talks to whom.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from itgraph.config import settings
from itgraph.db.backfill import (
    channels_in_scope,
    load_state,
    record_complete,
    record_failure,
    record_progress,
    record_skip,
    start_channel,
    was_completed_to,
)
from itgraph.db.models import Channel, FailureKind
from itgraph.db.raw import count_messages, store_messages
from itgraph.tg.full_channel import fetch_full_channel
from itgraph.tg.payload import encode_payload

__all__ = [
    "ChannelRun",
    "RunSummary",
    "backfill_channel",
    "backfill_channels",
    "ceiling_for",
    "classify",
]

logger = logging.getLogger(__name__)

# A channel that is gone, private, or whose username belongs to someone
# else now. Retrying these every run spends requests to learn nothing.
PERMANENT_ERRORS = (
    "ChannelPrivateError",
    "ChannelInvalidError",
    "ChannelPublicGroupNaError",
    "UsernameInvalidError",
    "UsernameNotOccupiedError",
    "PeerIdInvalidError",
    "InviteHashExpiredError",
)


@dataclass(slots=True)
class ChannelRun:
    """What one channel's walk did, and why it stopped.

    ``capped`` means the ceiling ended it rather than the cutoff: this
    channel has contributed all it is ever going to, and later runs will
    not ask it for more.
    """

    stored: int = 0
    capped: bool = False


@dataclass(slots=True)
class RunSummary:
    """What a run did, in the terms the operator asked for it."""

    completed: int = 0
    capped: int = 0
    skipped: int = 0
    failed: int = 0
    stored: int = 0

    def line(self) -> str:
        return (
            f"completed {self.completed}, capped {self.capped}, "
            f"skipped {self.skipped}, failed {self.failed}; "
            f"{self.stored} new messages"
        )


def ceiling_for(max_messages: int | None) -> int | None:
    """How many messages one channel may hold in total, if bounded.

    ``None`` — from an explicit 0 or a configured 0 — means unbounded,
    which is the deliberate choice, not the default.
    """
    value = (
        max_messages
        if max_messages is not None
        else settings.backfill_max_messages
    )
    return value if value > 0 else None


def classify(error: BaseException) -> FailureKind:
    """Whether a later run should try this channel again.

    Matched by class name rather than by import: Telethon generates its
    error classes, and the set moves between versions. A name this does
    not recognise is treated as transient, which costs a retry — the
    opposite mistake costs a channel that is never looked at again.
    """
    if type(error).__name__ in PERMANENT_ERRORS:
        return FailureKind.PERMANENT
    # `get_entity` raises a plain ValueError for a username that no
    # longer resolves to anything.
    if isinstance(error, ValueError):
        return FailureKind.PERMANENT
    return FailureKind.TRANSIENT


async def waiting_out_floods[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Run a request, sleeping off any rate limit and asking again.

    Every request this module makes goes through here, which matters more
    than it looks: ``FloodWaitError`` is itself an ``RPCError``, so a
    request left outside would be caught by the failure handler and
    recorded as transient. The channel would be abandoned and the next
    one asked for immediately — hammering the API precisely when it has
    said to stop, which is the behaviour that escalates a limit into a
    ban.

    Retrying discards nothing: the cursor has not moved, and re-storing a
    message it already has is a no-op.
    """
    while True:
        try:
            return await operation()
        except FloodWaitError as exc:
            seconds = getattr(exc, "seconds", 0)
            logger.warning("FloodWait for %ds — sleeping it off", seconds)
            await asyncio.sleep(seconds)


async def _fetch_window(
    client: TelegramClient, entity: Any, *, offset_id: int, size: int
) -> list[Any]:
    """One window of history, oldest-ward from ``offset_id``."""

    async def fetch() -> list[Any]:
        return [
            message
            async for message in client.iter_messages(
                entity, limit=size, offset_id=offset_id
            )
        ]

    return await waiting_out_floods(fetch)


async def backfill_channel(
    client: TelegramClient,
    session: AsyncSession,
    channel: Channel,
    *,
    cutoff: datetime,
    batch_size: int | None = None,
    request_delay: float | None = None,
    max_messages: int | None = None,
) -> ChannelRun:
    """Walk one channel back to ``cutoff``, or until it holds its share.

    Commits per batch, so the caller's session is left between
    transactions rather than inside one. That is the point: an
    interrupted walk must leave the cursor consistent with the rows.

    ``max_messages`` bounds the channel, not the run. A channel that
    reaches it is finished for good: the walk stops there and later runs
    do not come back for the rest, however deep a cutoff they are given.
    An aggregator that posts fifty times a day would otherwise be most of
    the corpus, and it is the least informative node in the graph.
    """
    size = batch_size or settings.backfill_batch_size
    delay = (
        request_delay
        if request_delay is not None
        else settings.backfill_request_delay
    )
    ceiling = ceiling_for(max_messages)

    if not channel.username:
        raise ValueError(
            f"{channel.tg_id} has no username; there is nothing to resolve"
        )
    username = channel.username

    # Counted from the rows, so the ceiling holds across runs: what was
    # collected last week still counts against this channel's share.
    held = (
        0 if ceiling is None else await count_messages(session, channel.tg_id)
    )
    remaining = None if ceiling is None else ceiling - held
    if remaining is not None and remaining <= 0:
        # Already at its ceiling: not a request's worth of anything.
        return ChannelRun(capped=True)

    state = await load_state(session, channel.tg_id)
    await start_channel(session, channel.tg_id)
    await session.commit()

    # Before any history: one cheap request that says whether the channel
    # is reachable at all, so an inaccessible one costs a single request
    # rather than failing part-way through a long walk.
    metadata = await waiting_out_floods(
        lambda: fetch_full_channel(client, session, username=username)
    )
    await session.commit()

    # 0 means "from the newest message"; a stored cursor means "carry on
    # from where the last run stopped".
    offset_id = state.oldest_fetched_id if state else None
    first_contact = state is None or state.newest_fetched_id is None
    stored = 0
    capped = False
    oldest_date: datetime | None = None

    while True:
        if remaining is not None and remaining <= 0:
            logger.info(
                "@%s: at its %d-message ceiling — done with this channel",
                username,
                ceiling,
            )
            capped = True
            break

        # Never ask for more than the ceiling leaves: those messages are
        # not going to be stored, so the request buys nothing.
        want = size if remaining is None else min(size, remaining)
        await asyncio.sleep(delay)
        window = await _fetch_window(
            client, metadata.entity, offset_id=offset_id or 0, size=want
        )
        if not window:
            break

        # Media is never downloaded: what is stored is the metadata the
        # payload already carries, and nothing touches the filesystem.
        keep = []
        reached_cutoff = False
        for message in window:
            if message.date < cutoff:
                reached_cutoff = True
                break
            keep.append(message)

        if keep:
            payloads = {
                message.id: encode_payload(message) for message in keep
            }
            stored += await store_messages(
                session, channel_id=channel.tg_id, payloads=payloads
            )
            newest = keep[0] if first_contact else None
            await record_progress(
                session,
                channel.tg_id,
                oldest_fetched_id=keep[-1].id,
                newest_fetched_id=newest.id if newest else None,
            )
            if newest is not None:
                await session.execute(
                    update(Channel)
                    .where(Channel.tg_id == channel.tg_id)
                    .values(last_post_at=newest.date)
                )
                first_contact = False
            # Rows and cursor together, or a killed process loses a
            # window of history that no later run will ask for.
            await session.commit()
            offset_id = keep[-1].id
            oldest_date = keep[-1].date
            if remaining is not None:
                remaining -= len(keep)

        if reached_cutoff or len(window) < want:
            break

    if capped:
        # Complete in the sense that counts: nothing will ask this
        # channel for more history. The date recorded is how deep the
        # rows actually go — writing the requested cutoff instead would
        # have the listing claim a depth that was never collected.
        await record_complete(session, channel.tg_id, oldest_date or cutoff)
        await session.commit()
        return ChannelRun(stored=stored, capped=True)

    await record_complete(session, channel.tg_id, cutoff)
    await session.commit()
    return ChannelRun(stored=stored)


async def backfill_channels(
    client: TelegramClient,
    session: AsyncSession,
    *,
    cutoff: datetime,
    limit: int | None = None,
    batch_size: int | None = None,
    request_delay: float | None = None,
    max_messages: int | None = None,
) -> RunSummary:
    """Walk every in-scope channel, one at a time, and survive each one.

    Concurrency would buy nothing here: Telegram's limits are per
    account, so parallel workers reach the same ceiling faster and look
    worse doing it.
    """
    summary = RunSummary()
    ceiling = ceiling_for(max_messages)

    for channel in await channels_in_scope(session):
        touched = summary.completed + summary.capped + summary.failed
        if limit is not None and touched >= limit:
            logger.info("stopping at --limit %d", limit)
            break

        state = await load_state(session, channel.tg_id)
        if was_completed_to(state, cutoff):
            logger.debug(
                "@%s already complete to %s", channel.username, cutoff
            )
            continue

        if (
            ceiling is not None
            and await count_messages(session, channel.tg_id) >= ceiling
        ):
            # Done for good, whatever cutoff this run was given: a deeper
            # `--since` must not reopen a channel that already has its
            # share of the corpus.
            logger.debug(
                "@%s is at its ceiling of %d", channel.username, ceiling
            )
            continue

        if not channel.username:
            # Without a username there is nothing to resolve: this
            # account was never let in, and asking would be pointless.
            logger.info("skipping %d: no username", channel.tg_id)
            await record_skip(session, channel.tg_id, "no username")
            await session.commit()
            summary.skipped += 1
            continue

        try:
            result = await backfill_channel(
                client,
                session,
                channel,
                cutoff=cutoff,
                batch_size=batch_size,
                request_delay=request_delay,
                max_messages=max_messages,
            )
        except (RPCError, OSError, ValueError, TypeError) as exc:
            kind = classify(exc)
            logger.warning(
                "@%s failed (%s): %s", channel.username, kind.value, exc
            )
            await session.rollback()
            await record_failure(session, channel.tg_id, kind, str(exc))
            await session.commit()
            summary.failed += 1
            continue

        if result.capped:
            summary.capped += 1
        else:
            summary.completed += 1
        summary.stored += result.stored
        logger.info(
            "@%s: %d new messages%s",
            channel.username,
            result.stored,
            " (ceiling reached)" if result.capped else "",
        )

    return summary
