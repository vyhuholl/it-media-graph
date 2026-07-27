"""The metadata pass: descriptions and linked chats, on their own budget.

A command rather than a step of the history walk, and the split is the
point. ``channels.getFullChannel`` carries a per-day quota; over two
hundred channels that is two hundred rationed requests spent to re-read a
description and a linked discussion chat that change on the order of
months. While it opened every walk, a run that exhausted the quota
collected no history either — the expensive pass took the cheap one down
with it. Separated, a metadata run can hit its limit and stop, and the
next backfill still walks every channel it was going to.

Nothing here resolves a username. The peer comes from the session's
entity cache, the same place the history walk gets its own, and a channel
the session cannot supply is skipped rather than paid for. That is what
keeps ``contacts.resolveUsername`` — the tightest daily quota in the
project — confined to the one command whose job it is.

The payload is stored as it arrives. What a description *says* is parsed
later, from the raw layer, by code that has to stay re-runnable.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from telethon import TelegramClient
from telethon.errors import RPCError

from itgraph.config import settings
from itgraph.db.backfill import channels_needing_metadata
from itgraph.db.models import CollectionCommand
from itgraph.db.session import Database
from itgraph.tg.backfill import (
    FloodWaitTooLong,
    PeerNotCached,
    cached_peer,
    waiting_out_floods,
)
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.full_channel import fetch_full_channel
from itgraph.tg.pacing import pace

__all__ = ["MetadataSummary", "refresh_metadata"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MetadataSummary:
    """What a metadata run did.

    ``halt`` is set when a rate limit stopped the run short. The counts
    around it describe committed work, so a halted run reports rather
    than vanishes — and what it reports is how much of the queue is
    still waiting.
    """

    fetched: int = 0
    linked: int = 0
    skipped: int = 0
    failed: int = 0
    halt: FloodWaitTooLong | None = None

    def line(self) -> str:
        return (
            f"fetched {self.fetched}, linked chats {self.linked}, "
            f"skipped {self.skipped}, failed {self.failed}"
        )


async def refresh_metadata(
    client: TelegramClient,
    database: Database,
    *,
    limit: int | None = None,
    delay: float | None = None,
    refresh: bool = False,
) -> MetadataSummary:
    """Fetch extended information for the channels that are due it.

    One channel at a time, paced, through the collector's FloodWait
    handling — the same discipline the history walk keeps, for the same
    reason. ``limit`` bounds the run, which is how a quota measured in
    hundreds per day gets spread over as many sittings as it needs.

    A rate limit too long to sit through stops the run. What was fetched
    before that point is committed and counted; the rest of the queue is
    still the queue, and the next run picks it up unchanged.
    """
    pause = delay if delay is not None else settings.backfill_request_delay
    max_age = timedelta(days=settings.channel_metadata_max_age_days)
    summary = MetadataSummary()
    recorder = FloodRecorder(database, CollectionCommand.METADATA)

    async with database.session() as session:
        due = await channels_needing_metadata(
            session, max_age=max_age, limit=limit, refresh=refresh
        )
        logger.info("%d channel(s) due a metadata fetch", len(due))

        # Read off the mapped instances before anything can expire them:
        # this loop rolls back on failure, and an attribute read off an
        # expired instance afterwards is a lazy load an async session
        # cannot perform.
        targets = [(channel.tg_id, channel.username) for channel in due]

        for tg_id, username in targets:
            if not username:
                logger.info("skipping %d: no username", tg_id)
                summary.skipped += 1
                continue

            try:
                peer = await cached_peer(client, username)
            except PeerNotCached as exc:
                # Resolving it here would spend the one budget this pass
                # is not allowed to touch. It is `resolve`'s to spend.
                logger.info("skipping @%s: %s", username, exc)
                summary.skipped += 1
                continue

            await pace(pause)
            try:
                metadata = await waiting_out_floods(
                    lambda: fetch_full_channel(client, session, peer=peer),
                    recorder.for_channel(tg_id),
                )
            except FloodWaitTooLong as exc:
                logger.warning("%s", exc)
                await session.rollback()
                summary.halt = exc
                break
            except (RPCError, OSError, ValueError, TypeError) as exc:
                # One channel's failure is not the run's. Nothing is
                # recorded against the channel: unlike a history walk
                # there is no cursor to protect, and the next run simply
                # finds it still due. A halt is a `FloodWaitTooLong`,
                # caught above and deliberately outside this tuple.
                logger.warning("@%s: metadata failed: %s", username, exc)
                await session.rollback()
                summary.failed += 1
                continue

            await session.commit()
            summary.fetched += 1
            if metadata.linked_chat_id is not None:
                summary.linked += 1

    logger.info("metadata done: %s", summary.line())
    return summary
