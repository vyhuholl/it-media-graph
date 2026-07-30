"""Channels named by hand, resolved into inventory rows.

The third way into the inventory, beside the operator's own
subscriptions and discovery by reference: a list of usernames someone
decided on. Every name costs one ``contacts.resolveUsername`` — the same
rationed method ``resolve`` spends, with no batch form and no substitute
— and nothing here joins, subscribes to or leaves anything.

The join is the part worth being explicit about. Subscribing from a
client and re-importing the dialog list would reach the same rows, and
would spend a ``channels.joinChannel`` per channel on top of a lookup
that gets spent either way. The join buys nothing and is the strongest
ban signal available, so this pass does not have one to forget.

A name the inventory already holds costs no request at all, which is
what makes a run resumable: re-run the same list and the work continues
where it stopped rather than paying for it twice.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import RPCError

from itgraph.config import settings
from itgraph.db.channels import (
    DiscoveredChannel,
    create_resolved_channel,
    existing_usernames,
)
from itgraph.db.edges import delete_pending_mention
from itgraph.db.floods import latest_flood_for_method
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
)
from itgraph.db.session import Database
from itgraph.tg.backfill import FloodWaitTooLong, waiting_out_floods
from itgraph.tg.client import persist_peers
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.pacing import pace
from itgraph.tg.resolve import channel_identity

__all__ = ["AddSummary", "Review", "add_channels"]

logger = logging.getLogger(__name__)

# The same failures resolution treats as a lookup that did not work. A
# bare `ValueError` is included because Telethon raises one for a
# username nobody occupies.
_LOOKUP_ERRORS = (RPCError, OSError, ValueError, TypeError)

# The method this pass spends, named as the flood record spells it.
RESOLVE_METHOD = "ResolveUsernameRequest"

# How far back a rate limit on that method is still worth mentioning.
# The shape of a per-day quota; see `_warn_about_recent_floods` for why
# this reports rather than decides.
_FLOOD_LOOKBACK = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Review:
    """A decision to record on the channels a run creates.

    Only ever applied to a row this run inserted. An existing record's
    review is the operator's and no import path may overwrite it.
    """

    status: ChannelStatus
    kind: ChannelKind | None = None


@dataclass(slots=True)
class AddSummary:
    """What one run did, and why it stopped.

    ``failures`` carries the usernames that did not become channels, with
    the reason each did not, so the command can report them and write
    them back out as the next run's input.
    """

    added: int = 0
    known: int = 0
    not_channels: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    halt: FloodWaitTooLong | None = None

    def line(self) -> str:
        return (
            f"added {self.added}, already known {self.known}, "
            f"not channels {self.not_channels}, failed {len(self.failures)}"
        )


async def _warn_about_recent_floods(session: AsyncSession) -> None:
    """Say whether this method was recently limited. Never refuse over it.

    ``flood_events`` records which run was limited, not which account was
    behind it — and this command is expected to run from a different
    account than the collection passes, which is the whole reason it
    exists. A guard that refused would be deciding on the one fact the
    table does not hold, and would be wrong in exactly the case it was
    written for. So: the event, and the operator's judgement.
    """
    event = await latest_flood_for_method(
        session,
        method=RESOLVE_METHOD,
        since=datetime.now(UTC) - _FLOOD_LOOKBACK,
    )
    if event is None:
        return
    logger.warning(
        "%s was rate-limited for %ds at %s, recorded by `itgraph %s`. "
        "This table records the run, not the account behind it — if that "
        "was a different account, it may not apply here.",
        event.method,
        event.seconds,
        event.occurred_at.strftime("%Y-%m-%d %H:%M UTC"),
        event.command.value,
    )


async def add_channels(
    client: TelegramClient,
    database: Database,
    *,
    usernames: list[str],
    review: Review | None = None,
    delay: float | None = None,
    limit: int | None = None,
) -> AddSummary:
    """Add each username to the inventory, paced and one request at a time.

    ``limit`` bounds the number of *lookups*, not the number of names:
    a username already held costs nothing and must not consume the
    budget, or the flag would mean something different on every run.

    A rate limit too long to sit through stops the run. What was added
    before it is committed and reported.
    """
    pause = delay if delay is not None else settings.backfill_request_delay
    summary = AddSummary()
    remaining = limit
    # No channel attributed: the id being asked about is not one the
    # inventory necessarily holds yet.
    recorder = FloodRecorder(database, CollectionCommand.ADD)

    async with database.session() as session:
        await _warn_about_recent_floods(session)

        # Once for the whole list. Newly created channels join the set in
        # memory, so the run stays consistent with itself without asking
        # again.
        known = await existing_usernames(session, usernames)

        try:
            for username in usernames:
                if username in known:
                    logger.info("@%s is already in the inventory", username)
                    summary.known += 1
                    continue
                if remaining is not None and remaining <= 0:
                    break

                await pace(pause)
                await _add_one(
                    client, session, username, summary, recorder, review
                )
                known.add(username)
                remaining = remaining - 1 if remaining is not None else None
        except FloodWaitTooLong as exc:
            logger.warning("%s", exc)
            await session.rollback()
            summary.halt = exc

    logger.info("add done: %s", summary.line())
    return summary


async def _add_one(
    client: TelegramClient,
    session: AsyncSession,
    username: str,
    summary: AddSummary,
    recorder: FloodRecorder,
    review: Review | None,
) -> None:
    """Resolve one username and record what it turned out to be.

    The lookup sits inside ``waiting_out_floods`` and must stay there.
    ``FloodWaitError`` is an ``RPCError``, so a lookup left outside would
    be caught by the failure handler below, counted as a name that did
    not work, and followed immediately by a request for the next one —
    asking again at the moment Telegram said to stop.
    """
    try:
        entity = await waiting_out_floods(
            lambda: client.get_entity(username), recorder
        )
    except _LOOKUP_ERRORS as exc:
        logger.warning("@%s did not resolve: %s", username, exc)
        summary.failures.append((username, str(exc)))
        return

    identity = channel_identity(entity)
    if identity is None:
        # A person or a bot. This graph holds channels only.
        logger.info("@%s is not a channel", username)
        summary.not_channels += 1
        summary.failures.append(
            (username, "resolved to a user or bot, not a channel")
        )
        return

    inserted = await create_resolved_channel(
        session,
        # The lookup's own spelling wins; the entry is a fallback for the
        # rare channel that answers to a lookup and reports no username.
        channel=DiscoveredChannel(
            tg_id=identity.tg_id,
            username=identity.username or username,
            title=identity.title,
            is_chat=identity.is_chat,
        ),
        discovered_via=DiscoverySource.MANUAL,
    )
    # The queue stores usernames normalised; a channel found both ways
    # would otherwise leave behind a request that can only return a
    # channel the inventory now has.
    await delete_pending_mention(session, username.lower())

    if inserted:
        if review is not None:
            await _apply_review(session, identity.tg_id, review)
        summary.added += 1
    else:
        # Identity refreshed, judgement untouched: the row was already
        # here, and its review — including no review at all — is the
        # operator's to change with `mark`.
        logger.info(
            "@%s was already in the inventory under another handle", username
        )
        summary.known += 1

    # Before the database commit, never after: see `persist_peers`. This
    # is what makes an added channel walkable by a later `backfill`
    # rather than merely present in the inventory.
    await persist_peers(client)
    await session.commit()


async def _apply_review(
    session: AsyncSession, tg_id: int, review: Review
) -> None:
    """Record the review on a channel this run created.

    Not ``mark_channel``: that writes unconditionally, which is right for
    a command whose purpose is recording a decision and wrong here, where
    the caller has just been told whether the row is new.
    """
    channel = await session.get(Channel, tg_id)
    if channel is None:  # pragma: no cover - just inserted in this session
        return
    channel.status = review.status
    if review.kind is not None:
        channel.kind = review.kind
    channel.reviewed_at = datetime.now(UTC)
