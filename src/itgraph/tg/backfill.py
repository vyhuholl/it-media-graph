"""The history walk: many requests, none of them scarce.

Three rules shape everything here. A run must survive being interrupted,
so the cursor advances in the same transaction as the batch it describes
and never ahead of it. A run must never look like a bot in a hurry, so
channels are walked strictly one at a time, with a delay between
requests, and every rate limit is answered by waiting. And a walk spends
no request that carries a daily quota: it asks for history and nothing
else.

That third rule is the newest and the least obvious, because the two
quota-bearing requests it removed did not look like requests at all —
they looked like getting hold of the channel. Resolving a username and
fetching extended information were both preconditions of walking, and
both were rationed per day, so a first pass over two hundred channels
ran out of budget before it collected anything. Now the peer comes from
the session file, extended information is a separate command, and what a
walk spends is `messages.getHistory` and time.

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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.utils import maybe_async

from itgraph.config import settings
from itgraph.db.backfill import (
    channels_in_scope,
    count_deferred_chats,
    count_stale_metadata,
    load_state,
    record_complete,
    record_failure,
    record_progress,
    record_skip,
    start_channel,
    was_completed_to,
)
from itgraph.db.models import Channel, CollectionCommand, FailureKind
from itgraph.db.raw import count_messages, store_messages
from itgraph.db.session import Database
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.pacing import pace, pause_between_channels
from itgraph.tg.payload import encode_payload

__all__ = [
    "ChannelRun",
    "FloodWaitTooLong",
    "PeerNotCached",
    "RunSummary",
    "backfill_channel",
    "backfill_channels",
    "cached_peer",
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


class FloodWaitTooLong(RuntimeError):
    """Telegram asked for a wait longer than this run is willing to hold.

    Deliberately **not** an ``RPCError``. ``FloodWaitError`` is one, and
    the per-channel handler in ``backfill_channels`` catches ``RPCError``
    — so a rate limit re-raised in its own class would be recorded as a
    transient channel failure and the run would move straight on to the
    next channel, issuing a fresh request at the exact moment Telegram
    asked for silence. That is the behaviour ``waiting_out_floods``
    exists to prevent, and the one that turns a rate limit into a ban.
    Living outside the ``RPCError`` hierarchy is what keeps this
    unreachable by that handler.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.resume_after = datetime.now(UTC) + timedelta(seconds=seconds)
        super().__init__(
            f"FloodWait of {seconds:.0f}s exceeds the "
            f"{settings.flood_abort_threshold:.0f}s halt threshold; "
            f"stopping. Work may resume after "
            f"{self.resume_after:%Y-%m-%d %H:%M} UTC."
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
    """What a run did, in the terms the operator asked for it.

    ``halt`` is set when a rate limit stopped the run short. The counts
    around it are still true — they describe committed work — so a halted
    run reports rather than vanishes.

    ``deferred`` counts what the run never considered rather than what it
    did: accepted chats with no parent channel, which nothing walks yet.
    It is reported in its own clause because those are not channels the
    run passed over — they are work that is waiting.

    ``stale_metadata`` is the same kind of number for a different queue:
    in-scope channels whose description and linked chat are absent or
    out of date. A walk no longer fetches any of that, so without this
    the only sign that ``itgraph metadata`` was never run would be
    silence. Counting costs a query and no request.
    """

    completed: int = 0
    capped: int = 0
    skipped: int = 0
    failed: int = 0
    stored: int = 0
    deferred: int = 0
    stale_metadata: int = 0
    halt: FloodWaitTooLong | None = None

    def line(self) -> str:
        line = (
            f"completed {self.completed}, capped {self.capped}, "
            f"skipped {self.skipped}, failed {self.failed}; "
            f"{self.stored} new messages"
        )
        if self.deferred:
            line += (
                f"; {self.deferred} standalone chat"
                f"{'' if self.deferred == 1 else 's'} deferred"
            )
        if self.stale_metadata:
            line += (
                f"; {self.stale_metadata} channel"
                f"{'' if self.stale_metadata == 1 else 's'} "
                "awaiting `itgraph metadata`"
            )
        return line


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


async def waiting_out_floods[T](
    operation: Callable[[], Awaitable[T]], recorder: FloodRecorder
) -> T:
    """Run a request, sleeping off a short rate limit and asking again.

    Every request this module makes goes through here, which matters more
    than it looks: ``FloodWaitError`` is itself an ``RPCError``, so a
    request left outside would be caught by the failure handler and
    recorded as transient. The channel would be abandoned and the next
    one asked for immediately — hammering the API precisely when it has
    said to stop, which is the behaviour that escalates a limit into a
    ban.

    Retrying discards nothing: the cursor has not moved, and re-storing a
    message it already has is a no-op.

    A wait past ``flood_abort_threshold`` is not slept off. Holding a
    connection open for hours to resume work that has no deadline buys
    nothing, and a wait that long is the shape of a per-method daily
    quota — which waiting does not answer, because it counts calls rather
    than measuring their rate. The run stops instead, and the operator
    re-runs after the reported time; the walk resumes from its cursor
    like any other interruption.

    Every wait is recorded before it is acted on, so the method that was
    limited survives the run that hit it. ``recorder`` never raises.
    """
    while True:
        try:
            return await operation()
        except FloodWaitError as exc:
            seconds = getattr(exc, "seconds", 0)
            halting = seconds > settings.flood_abort_threshold
            await recorder.record(
                request=getattr(exc, "request", None),
                seconds=seconds,
                halted=halting,
            )
            if halting:
                raise FloodWaitTooLong(seconds) from exc
            logger.warning("FloodWait for %ds — sleeping it off", seconds)
            await asyncio.sleep(seconds)


async def _fetch_window(
    client: TelegramClient,
    entity: Any,
    *,
    offset_id: int,
    size: int,
    recorder: FloodRecorder,
) -> list[Any]:
    """One window of history, oldest-ward from ``offset_id``."""

    async def fetch() -> list[Any]:
        return [
            message
            async for message in client.iter_messages(
                entity, limit=size, offset_id=offset_id
            )
        ]

    return await waiting_out_floods(fetch, recorder)


class PeerNotCached(RuntimeError):
    """The session file holds no peer for this channel.

    Deliberately **not** a ``ValueError``, which is what the session
    raises on a miss. ``classify`` files a bare ``ValueError`` as
    ``PERMANENT``, and ``channels_in_scope`` excludes anything
    permanently failed — so a miss allowed to reach the per-channel
    failure handler would retire the channel from collection for good,
    silently, over a session file that can be rebuilt in one command.
    Its own class is what keeps that unreachable.
    """

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(f"@{username} is not in the session's entity cache")


async def cached_peer(client: TelegramClient, username: str) -> Any:
    """The peer for a username, out of the session file and nowhere else.

    Asks the *session* rather than the client, and the distinction is the
    whole point. ``client.get_input_entity`` consults the same cache, but
    on a miss it keeps going: ``telethon/client/users.py`` falls through
    to ``_get_entity_from_string`` and out to
    ``contacts.resolveUsername`` — the tightest daily quota in the
    project, and exactly the request a history walk must never make. By
    the time that call raised, the quota would already be spent, so
    catching its error would be catching the wrong thing at the wrong
    time. ``session.get_input_entity`` stops at the cache and raises.

    Two hundred channels on a rebuilt session is what this prevents: a
    day's resolution budget gone before a single message arrived.

    ``maybe_async`` because a session may implement the lookup either
    way; the bundled SQLite one is synchronous, and Telethon wraps it the
    same way at its own call site.
    """
    try:
        return await maybe_async(client.session.get_input_entity(username))
    except (ValueError, TypeError) as exc:
        raise PeerNotCached(username) from exc


async def backfill_channel(
    client: TelegramClient,
    session: AsyncSession,
    *,
    channel_id: int,
    username: str | None,
    cutoff: datetime,
    batch_size: int | None = None,
    request_delay: float | None = None,
    max_messages: int | None = None,
    recorder: FloodRecorder,
) -> ChannelRun:
    """Walk one channel back to ``cutoff``, or until it holds its share.

    Takes an id and a username rather than a mapped ``Channel`` on
    purpose. This function commits, and its caller rolls back when a
    channel fails; both expire mapped instances, and reading an attribute
    off one afterwards is a lazy load that an async session cannot
    perform. Plain values do not go stale, so the walk cannot be made to
    depend on where the transaction happens to be.

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

    if not username:
        raise ValueError(
            f"{channel_id} has no username; there is nothing to resolve"
        )

    # Counted from the rows, so the ceiling holds across runs: what was
    # collected last week still counts against this channel's share.
    held = 0 if ceiling is None else await count_messages(session, channel_id)
    remaining = None if ceiling is None else ceiling - held
    if remaining is not None and remaining <= 0:
        # Already at its ceiling: not a request's worth of anything.
        return ChannelRun(capped=True)

    state = await load_state(session, channel_id)
    await start_channel(session, channel_id)
    await session.commit()

    # No pacing before this one: it reads the session file, not the
    # network, so there is nothing to be polite to.
    entity = await cached_peer(client, username)

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
        await pace(delay)
        window = await _fetch_window(
            client,
            entity,
            offset_id=offset_id or 0,
            size=want,
            recorder=recorder,
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
                session, channel_id=channel_id, payloads=payloads
            )
            newest = keep[0] if first_contact else None
            await record_progress(
                session,
                channel_id,
                oldest_fetched_id=keep[-1].id,
                newest_fetched_id=newest.id if newest else None,
            )
            if newest is not None:
                await session.execute(
                    update(Channel)
                    .where(Channel.tg_id == channel_id)
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
        await record_complete(session, channel_id, oldest_date or cutoff)
        await session.commit()
        return ChannelRun(stored=stored, capped=True)

    await record_complete(session, channel_id, cutoff)
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
    database: Database,
) -> RunSummary:
    """Walk every in-scope channel, one at a time, and survive each one.

    Concurrency would buy nothing here: Telegram's limits are per
    account, so parallel workers reach the same ceiling faster and look
    worse doing it.

    A rate limit too long to sit through stops the run rather than any
    one channel, and what was committed up to that point is still
    reported.
    """
    summary = RunSummary()
    ceiling = ceiling_for(max_messages)
    worked = False
    # Rebound per channel below, so an event names the channel that was
    # being walked when the limit arrived.
    recorder = FloodRecorder(database, CollectionCommand.BACKFILL)

    # Read off the mapped instances once, here. A rollback in the failure
    # handler below expires every loaded object — not just the channel
    # that failed — and reading an attribute off an expired instance on a
    # later iteration is a lazy load that an async session cannot perform.
    # Plain tuples survive whatever the transaction does.
    targets = [
        (channel.tg_id, channel.username)
        for channel in await channels_in_scope(session)
    ]

    # Both counted before the walk, so a run stopped by a rate limit
    # still reports them: neither depends on how far the run got.
    summary.deferred = await count_deferred_chats(session)
    if summary.deferred:
        logger.info(
            "%d accepted standalone chat(s) deferred: reading community "
            "chats is not implemented yet",
            summary.deferred,
        )

    summary.stale_metadata = await count_stale_metadata(
        session,
        max_age=timedelta(days=settings.channel_metadata_max_age_days),
    )
    if summary.stale_metadata:
        logger.info(
            "%d channel(s) have no current description or linked chat; "
            "run `itgraph metadata`",
            summary.stale_metadata,
        )

    for tg_id, username in targets:
        touched = summary.completed + summary.capped + summary.failed
        if limit is not None and touched >= limit:
            logger.info("stopping at --limit %d", limit)
            break

        state = await load_state(session, tg_id)
        if was_completed_to(state, cutoff):
            logger.debug("@%s already complete to %s", username, cutoff)
            continue

        if (
            ceiling is not None
            and await count_messages(session, tg_id) >= ceiling
        ):
            # Done for good, whatever cutoff this run was given: a deeper
            # `--since` must not reopen a channel that already has its
            # share of the corpus.
            logger.debug("@%s is at its ceiling of %d", username, ceiling)
            continue

        if not username:
            # Without a username there is nothing to resolve: this
            # account was never let in, and asking would be pointless.
            logger.info("skipping %d: no username", tg_id)
            await record_skip(session, tg_id, "no username")
            await session.commit()
            summary.skipped += 1
            continue

        # Past every guard, so this channel is going to spend requests.
        # The pause separates channels, which means there is nothing to
        # separate the first one from, and a channel skipped above costs
        # nothing because it never got here.
        if worked:
            await pause_between_channels()
        worked = True

        try:
            result = await backfill_channel(
                client,
                session,
                channel_id=tg_id,
                username=username,
                cutoff=cutoff,
                batch_size=batch_size,
                request_delay=request_delay,
                max_messages=max_messages,
                recorder=recorder.for_channel(tg_id),
            )
        except PeerNotCached as exc:
            # Not a failure: the channel is fine, this session simply has
            # never seen it. Recording it as failed would be worse than
            # useless — `classify` would call the underlying `ValueError`
            # permanent and `channels_in_scope` would drop the channel for
            # good. Caught before the handler below, which is what keeps
            # that from happening; see `PeerNotCached`.
            logger.info("skipping @%s: %s", username, exc)
            await session.rollback()
            await record_skip(session, tg_id, "no cached peer")
            await session.commit()
            summary.skipped += 1
            continue
        except FloodWaitTooLong as exc:
            # Not this channel's fault and not a channel failure: nothing
            # is recorded against it, and its committed progress stands.
            # Caught before the handler below can see it — see
            # `FloodWaitTooLong` for what happens if it ever is not.
            logger.warning("%s", exc)
            await session.rollback()
            summary.halt = exc
            break
        except (RPCError, OSError, ValueError, TypeError) as exc:
            kind = classify(exc)
            logger.warning("@%s failed (%s): %s", username, kind.value, exc)
            await session.rollback()
            await record_failure(session, tg_id, kind, str(exc))
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
            username,
            result.stored,
            " (ceiling reached)" if result.capped else "",
        )

    return summary
