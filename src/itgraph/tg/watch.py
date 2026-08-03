"""The poll loop: many small requests, forever, none of them scarce.

The first process in this project meant to stay up, and nearly everything
here follows from that one difference rather than from the measurement
itself. A backfill spends a budget and stops; this spends a little and
does not, so the numbers are chosen for a sustained rate below the walk's
and the failure handling is chosen for a process that must not exit.

Four rules shape it.

**One request does both jobs.** ``messages.getHistory`` returns each
message with its counters as of the response, so one window of a
channel's recent history answers "is there anything new" and "what do the
live posts look like now" together. The cost of watching a channel is
therefore per poll, not per live post: a channel with four posts still
inside the horizon refreshes all four for one request.

**Nothing is derived.** Messages are stored as payloads, counters as
snapshots, and neither is interpreted. Edges are still the derivation
pass's job, run over these rows afterwards exactly as over any others.

**No request carries a daily quota.** The peer comes from the session
file's entity cache through ``cached_peer``, never from ``get_entity``.
In a one-off walk that mistake costs a run; in a loop it would spend the
tightest quota in the project every day until somebody noticed.

**A rate limit does not stop it.** ``waiting_out_floods`` is reused
unchanged, halt and all — and the halt is caught here and converted into
a postponement of the whole schedule. One policy, two callers, rather
than a second flood handler drifting away from the first.
"""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import RPCError

from itgraph.config import settings
from itgraph.db.backfill import advance_newest
from itgraph.db.metrics import store_metrics
from itgraph.db.models import Channel, CollectionCommand
from itgraph.db.poll import (
    DueChannel,
    due_channels,
    live_post_dates,
    measure_posts_per_day,
    postpone_all,
    queue_lag,
    record_poll,
)
from itgraph.db.raw import store_messages
from itgraph.db.session import Database
from itgraph.db.session_lease import SessionLease
from itgraph.derive.metrics import counters_of
from itgraph.schedule import (
    horizon,
    in_quiet_hours,
    next_channel_poll,
    window_size,
)
from itgraph.tg.backfill import (
    FloodWaitTooLong,
    PeerNotCached,
    cached_peer,
    classify,
    waiting_out_floods,
)
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.pacing import pace
from itgraph.tg.payload import encode_payload

__all__ = ["PollOutcome", "WatchStats", "poll_channel", "watch"]

logger = logging.getLogger(__name__)

# How many due channels one tick claims at a time. Not a concurrency
# limit — there is no concurrency — but a bound on how stale the batch is
# allowed to get: the schedule moves while the loop works through it, and
# a batch of the whole inventory would be answering a question asked
# hours ago by the time it reached the end.
BATCH = 25


@dataclass(slots=True)
class PollOutcome:
    """What one channel's poll did."""

    stored: int = 0
    snapshots: int = 0
    newest_id: int | None = None


@dataclass(slots=True)
class WatchStats:
    """What the loop has done since it started.

    ``skipped`` is counted apart from ``failed`` because the two are
    different facts and only one of them is a problem with the channel. A
    channel the session file cannot supply a peer for is fine; this
    account has simply never seen it, and the fix is a session rebuild
    rather than anything about the channel. In a loop that distinction
    matters more than in a walk: the skip repeats forever, silently,
    unless something counts it.
    """

    polled: int = 0
    stored: int = 0
    snapshots: int = 0
    skipped: int = 0
    failed: int = 0
    postponed: int = 0
    cycles: int = 0
    skipped_channels: set[int] = field(default_factory=set)

    def line(self) -> str:
        line = (
            f"polled {self.polled}, {self.stored} new messages, "
            f"{self.snapshots} snapshots"
        )
        if self.skipped:
            line += f"; {len(self.skipped_channels)} channel(s) have no cached peer"
        if self.failed:
            line += f"; {self.failed} failed"
        if self.postponed:
            line += f"; {self.postponed} rate-limit postponement(s)"
        return line


async def _window(
    client: TelegramClient,
    entity: Any,
    *,
    size: int,
    recorder: FloodRecorder,
) -> list[Any]:
    """The newest ``size`` messages of a channel, as one request."""

    async def fetch() -> list[Any]:
        return [
            message
            async for message in client.iter_messages(entity, limit=size)
        ]

    return await waiting_out_floods(fetch, recorder)


async def poll_channel(
    client: TelegramClient,
    session: AsyncSession,
    *,
    channel: DueChannel,
    now: datetime,
    recorder: FloodRecorder,
) -> PollOutcome:
    """Read one channel once: new posts and fresh counters, one request.

    Payloads are written before snapshots and in the same transaction.
    The foreign key from ``message_metrics`` onto ``raw_messages``
    enforces that, so getting it wrong is a database error rather than a
    silent inconsistency — but the ordering is deliberate here and not an
    accident of how the code happens to read.

    Does not commit. The caller owns the transaction boundary, because it
    is the caller that has to roll back a failed channel without losing
    the ones before it.
    """
    entity = await cached_peer(
        client, tg_id=channel.tg_id, username=channel.username
    )

    size = window_size(channel.posts_per_day)
    await pace(settings.watch_request_delay)
    window = await _window(client, entity, size=size, recorder=recorder)
    if not window:
        return PollOutcome()

    cutoff = now - horizon()
    payloads: dict[int, dict[str, Any]] = {}
    readings = {}
    newest_id: int | None = None
    newest_date: datetime | None = None

    for message in window:
        payload = encode_payload(message)
        payloads[message.id] = payload
        if newest_id is None or message.id > newest_id:
            newest_id, newest_date = message.id, message.date
        # Only posts still inside the horizon are read. An older one is
        # in the window because the window is sized in messages rather
        # than in time, and re-reading it would cost a row to learn a
        # number that has stopped moving.
        if message.date >= cutoff:
            counters = counters_of(payload)
            if counters is not None:
                readings[message.id] = counters

    stored = await store_messages(
        session, channel_id=channel.tg_id, payloads=payloads
    )
    snapshots = await store_metrics(
        session,
        channel_id=channel.tg_id,
        observed_at=now,
        counters=readings,
    )

    if newest_id is not None:
        await advance_newest(session, channel.tg_id, newest_id)
    if stored and newest_date is not None:
        await session.execute(
            update(Channel)
            .where(Channel.tg_id == channel.tg_id)
            .values(last_post_at=newest_date)
        )

    return PollOutcome(stored=stored, snapshots=snapshots, newest_id=newest_id)


def _rate_is_stale(channel: DueChannel, now: datetime) -> bool:
    """Whether this channel's cached posting rate should be re-measured."""
    if channel.posts_per_day is None or channel.posts_per_day_at is None:
        return True
    age = now - channel.posts_per_day_at
    return age.total_seconds() >= settings.watch_rate_max_age_hours * 3600


async def _reschedule(
    database: Database,
    channel: DueChannel,
    *,
    now: datetime,
    found_nothing: bool,
    error: str | None,
    measure_rate: bool,
) -> None:
    """Work out when this channel is next due, and write it down.

    In a session of its own so that it survives the rollback of a failed
    poll. A channel whose poll raised must still be given a later due
    moment — otherwise the loop selects it again on the next tick and
    keeps doing so, at full rate, forever.
    """
    async with database.session() as session:
        rate = (
            await measure_posts_per_day(
                session, channel_id=channel.tg_id, now=now
            )
            if measure_rate
            else None
        )
        live = await live_post_dates(
            session, channel_id=channel.tg_id, now=now
        )
        due_at = next_channel_poll(
            now,
            live_posts=live,
            posts_per_day=(
                rate if rate is not None else channel.posts_per_day
            ),
            last_polled_at=now,
            consecutive_empty=channel.consecutive_empty,
            consecutive_failures=channel.consecutive_failures,
        )
        await record_poll(
            session,
            channel.tg_id,
            due_at=due_at,
            polled_at=now,
            posts_per_day=rate,
            found_nothing=found_nothing,
            error=error,
        )


async def watch(
    client: TelegramClient,
    database: Database,
    *,
    lease: SessionLease | None = None,
    stop: asyncio.Event | None = None,
    max_cycles: int | None = None,
) -> WatchStats:
    """Poll due channels, one at a time, until asked to stop.

    Concurrency is structurally absent and that is the point: Telegram's
    limits are per account, so parallel workers reach the same ceiling
    faster and look worse doing it. One worker behind ``pace`` also means
    the loop cannot burst however overdue its queue gets — the only way
    it could is by trying to catch up, which the schedule refuses to do.

    ``max_cycles`` exists for the tests and for an operator who wants one
    pass; ``stop`` is what a signal handler sets.
    """
    stats = WatchStats()
    recorder = FloodRecorder(database, CollectionCommand.WATCH)
    stop = stop or asyncio.Event()
    last_lease_check = 0.0

    while not stop.is_set():
        if max_cycles is not None and stats.cycles >= max_cycles:
            break
        stats.cycles += 1
        now = datetime.now(UTC)

        # Losing the lease is fatal, deliberately: something else may
        # already be using the session file, and reconnecting on the
        # assumption it survived is the one path to two writers on one
        # session.
        if lease is not None:
            elapsed = asyncio.get_running_loop().time() - last_lease_check
            if elapsed >= settings.watch_lease_check_seconds:
                await lease.verify()
                last_lease_check = asyncio.get_running_loop().time()

        if in_quiet_hours(now):
            logger.debug("quiet hours — not polling")
            await _sleep(stop, settings.watch_tick_seconds)
            continue

        async with database.session() as session:
            batch = await due_channels(session, now=now, limit=BATCH)
            lag = await queue_lag(session, now=now)

        if not batch:
            await _sleep(stop, settings.watch_tick_seconds)
            continue

        # One line per batch, carrying what the loop has done and how far
        # behind it is. Reported rather than left to be inferred: this
        # process runs unattended, so the state nobody was watching is the
        # only state anyone ever asks about.
        logger.info(
            "%s; %d overdue%s",
            stats.line(),
            lag.overdue,
            (
                f", oldest by {now - lag.oldest_due_at}"
                if lag.oldest_due_at is not None
                else ""
            ),
        )

        for channel in batch:
            if stop.is_set():
                break
            halted = await _poll_one(
                client, database, channel, stats=stats, recorder=recorder
            )
            if halted is not None:
                # Not this channel's fault and not a channel failure:
                # the whole schedule slides past the wait and the loop
                # carries on. A batch job would exit here; a loop that
                # exited would stop being the product.
                async with database.session() as session:
                    await postpone_all(session, until=halted.resume_after)
                stats.postponed += 1
                logger.warning("%s — postponing the schedule", halted)
                break

    return stats


async def _poll_one(
    client: TelegramClient,
    database: Database,
    channel: DueChannel,
    *,
    stats: WatchStats,
    recorder: FloodRecorder,
) -> FloodWaitTooLong | None:
    """One channel, with every failure contained. Returns a halt, if any."""
    now = datetime.now(UTC)
    measure_rate = _rate_is_stale(channel, now)
    error: str | None = None
    found_nothing = True

    try:
        async with database.session() as session:
            outcome = await poll_channel(
                client,
                session,
                channel=channel,
                now=now,
                recorder=recorder.for_channel(channel.tg_id),
            )
    except PeerNotCached as exc:
        # Not a failure. The channel is fine; this session has never seen
        # it. Recorded as an error it would be classified permanent and
        # dropped from scope for good — and in a loop that verdict would
        # be reached silently and never revisited.
        if channel.tg_id not in stats.skipped_channels:
            logger.info("skipping @%s: %s", channel.username, exc)
        stats.skipped_channels.add(channel.tg_id)
        stats.skipped += 1
    except FloodWaitTooLong as exc:
        # Caught before the handler below can see it — it is deliberately
        # not an `RPCError` so that the per-channel handler cannot treat
        # a rate limit as this channel's fault.
        return exc
    except (RPCError, OSError, ValueError, TypeError) as exc:
        kind = classify(exc)
        logger.warning(
            "@%s failed (%s): %s", channel.username, kind.value, exc
        )
        error = f"{type(exc).__name__}: {exc}"[:500]
        stats.failed += 1
    else:
        stats.polled += 1
        stats.stored += outcome.stored
        stats.snapshots += outcome.snapshots
        found_nothing = outcome.stored == 0
        if outcome.stored:
            logger.info(
                "@%s: %d new, %d snapshots",
                channel.username,
                outcome.stored,
                outcome.snapshots,
            )

    await _reschedule(
        database,
        channel,
        now=now,
        found_nothing=found_nothing,
        error=error,
        measure_rate=measure_rate,
    )
    return None


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    """Wait, but wake immediately if asked to stop.

    A plain sleep would make shutdown take as long as a tick, which is
    the difference between a daemon that stops and one that has to be
    killed.
    """
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
