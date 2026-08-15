"""The poll loop: many small requests, forever, none of them scarce.

The first process in this project meant to stay up, and nearly everything
here follows from that one difference rather than from the measurement
itself. A backfill spends a budget and stops; this spends a little and
does not, so the numbers are chosen for a sustained rate below the walk's
and the failure handling is chosen for a process that must not exit.

Five rules shape it.

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

**Nothing here may wait forever, and a loop that waits forever stops.**
The one failure this loop has actually had was neither an error nor a
rate limit: Telegram closed the connection, Telethon accepted the next
request while reconnecting, the reconnect failed for good, and the
request was left in a queue that nothing drains. The `await` never
returned, and for 67 hours the process was a healthy-looking PID
collecting nothing. So every request carries a deadline; a deadline
passed means the connection is discarded rather than reused; the loop
confirms it is connected before each poll and reconnects when it is not;
and if it concludes no poll at all while channels are due, it raises
`WatchStalled` and exits for a supervisor to restart. The last of those
is the only one that would have caught a bug nobody had thought of,
which is why it is deliberately incurious about the cause.
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
    RequestTimedOut,
    cached_peer,
    classify,
    waiting_out_floods,
)
from itgraph.tg.errors import WatchStalled
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.pacing import pace
from itgraph.tg.payload import encode_payload

__all__ = [
    "PollAttempt",
    "PollOutcome",
    "WatchStats",
    "poll_channel",
    "watch",
]

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
class PollAttempt:
    """What one channel's turn in the batch amounted to.

    ``concluded`` is the loop's own health, not the channel's: it is
    false only when the request passed its deadline, which is the one
    outcome that taught the loop nothing at all. An error from Telegram
    concluded — the channel answered, and what it said was no.

    ``halt`` carries a rate limit too long to sleep off, which belongs to
    the schedule rather than to this channel.
    """

    halt: FloodWaitTooLong | None = None
    concluded: bool = True


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
    timed_out: int = 0
    reconnects: int = 0
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
        # Counted apart from `failed` for the same reason `skipped` is:
        # a request that never answered is a fact about the connection,
        # and a loop reconnecting all day looks identical to a healthy
        # one in every other number here.
        if self.timed_out:
            line += f"; {self.timed_out} timed out"
        if self.reconnects:
            line += f"; {self.reconnects} reconnect(s)"
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

    Raises ``WatchStalled`` when it has work due and concludes no poll
    for ``watch_stall_minutes`` — the one condition under which this
    returns by failing rather than by being asked to stop.

    The stall check runs as a task beside the cycles rather than inside
    them, and that is the entire reason it is worth having. A check at
    the top of each cycle can only fire if the loop is still reaching
    the top of its cycles — which is precisely what the failure it was
    written for did not do. It sat in one `await` for 67 hours. A guard
    that shares its victim's liveness is not a guard.

    Two tasks and ``asyncio.wait`` rather than a ``TaskGroup``, for one
    reason: a group re-raises *everything* as an ``ExceptionGroup``,
    including an exception from the body it is hosting. ``LeaseLostError``
    would then reach ``cli.py`` wrapped, miss the tuple it catches, and
    print a traceback in place of the sentence written for it — a
    regression in the one path that already worked. Whichever task
    finishes first is awaited here, so every failure arrives as itself.
    """
    stats = WatchStats()
    progress = _Progress()
    cycles = asyncio.create_task(
        _cycles(
            client,
            database,
            stats=stats,
            progress=progress,
            lease=lease,
            stop=stop or asyncio.Event(),
            max_cycles=max_cycles,
        )
    )
    watchdog = asyncio.create_task(_refuse_to_stall(progress))

    try:
        done, _ = await asyncio.wait(
            {cycles, watchdog}, return_when=asyncio.FIRST_COMPLETED
        )
        # The watchdog only ever finishes by raising, so if it is in
        # `done` the loop is the one being given up on.
        first, second = (
            (watchdog, cycles) if watchdog in done else (cycles, watchdog)
        )
        await _abandon(second)
        await first
    finally:
        # Reached when this coroutine is itself cancelled. Neither task
        # outlives the call that made them.
        await _abandon(cycles)
        await _abandon(watchdog)
    return stats


async def _abandon(task: asyncio.Task[None]) -> None:
    """Cancel a task and wait for it to notice. Never raises its failure.

    A task that has already finished is only marked as read, so that a
    failure asyncio would otherwise log as "never retrieved" stays quiet
    — and, more importantly, so that cleanup in a ``finally`` cannot
    replace the exception already on its way out with the same one, or
    with the other task's.
    """
    if task.done():
        with suppress(asyncio.CancelledError):
            task.exception()
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _cycles(
    client: TelegramClient,
    database: Database,
    *,
    stats: WatchStats,
    progress: _Progress,
    lease: SessionLease | None,
    stop: asyncio.Event,
    max_cycles: int | None,
) -> None:
    """The loop itself. See ``watch``, which owns the stall check."""
    recorder = FloodRecorder(database, CollectionCommand.WATCH)
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
            progress.made()
            await _sleep(stop, settings.watch_tick_seconds)
            continue

        # Before the queue is read, so an outage costs no database work
        # and — more to the point — cannot reach a channel. A poll issued
        # over a connection the client has given up on fails instantly,
        # and 25 of them would push a whole batch out by the failure
        # backoff for something that was never the channels' fault.
        if not await _ensure_connected(client, stop, stats=stats):
            continue

        async with database.session() as session:
            batch = await due_channels(session, now=now, limit=BATCH)
            lag = await queue_lag(session, now=now)

        if not batch:
            progress.made()
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
            # The connection is re-checked per channel, not per batch. A
            # batch is 25 channels and several minutes; a connection lost
            # partway through it would otherwise cost every remaining
            # channel a recorded failure and a backoff. It is a flag
            # read, not a probe, so asking costs nothing.
            if stop.is_set() or not client.is_connected():
                break
            attempt = await _poll_one(
                client, database, channel, stats=stats, recorder=recorder
            )
            if attempt.concluded:
                progress.made()
            if attempt.halt is not None:
                # Not this channel's fault and not a channel failure:
                # the whole schedule slides past the wait and the loop
                # carries on. A batch job would exit here; a loop that
                # exited would stop being the product.
                async with database.session() as session:
                    await postpone_all(
                        session, until=attempt.halt.resume_after
                    )
                stats.postponed += 1
                progress.made()
                logger.warning("%s — postponing the schedule", attempt.halt)
                break


class _Progress:
    """When the loop last got somewhere, on a monotonic clock.

    Monotonic because the question is how long *the loop* has been
    silent; a wall clock stepped by ntp or by a resume from suspend
    answers a different one, and answers it wrongly in both directions.
    """

    def __init__(self) -> None:
        self.at = asyncio.get_running_loop().time()

    def made(self) -> None:
        self.at = asyncio.get_running_loop().time()

    def silent_for(self) -> float:
        return asyncio.get_running_loop().time() - self.at


async def _refuse_to_stall(progress: _Progress) -> None:
    """Stop the loop if it has had work to do and done none of it.

    The catch-all, and the only guard here that does not know what it is
    guarding against. Everything else in this module answers a failure
    somebody anticipated; this one answers the next one — which is why
    it runs as its own task and asks a clock rather than asking the loop.

    Progress is a poll that *concluded* — stored, skipped, or failed
    with an answer from Telegram. A request that passed its deadline is
    deliberately not progress: it taught the loop nothing, and a loop
    that times out on every request forever is exactly what a restart is
    for. Nor is a successful reconnect, for the same reason —
    reconnecting and then learning nothing is not working, however busy
    it looks in the log.

    States where there is legitimately nothing to conclude — quiet
    hours, an empty queue, a postponed schedule — count as progress at
    their own call sites. Without that this would fire on a loop that is
    perfectly healthy and merely idle.

    Sleeps exactly as long as it would take to stall, then asks again.
    No polling interval to choose, and a loop that made progress in the
    meantime simply resets what the next sleep is.
    """
    while True:
        remaining = settings.watch_stall_minutes * 60 - progress.silent_for()
        if remaining > 0:
            await asyncio.sleep(remaining)
            continue
        raise WatchStalled(
            f"no poll has concluded in "
            f"{progress.silent_for() / 60:.0f} minutes: the loop is not "
            "working, and it cannot say why. Stopping so it can be "
            "restarted — under systemd that happens by itself."
        )


async def _ensure_connected(
    client: TelegramClient, stop: asyncio.Event, *, stats: WatchStats
) -> bool:
    """Confirm the client is connected, reconnecting once if it is not.

    ``is_connected`` is a flag rather than a probe: Telethon holds it
    true for the whole of its own reconnection and clears it only once
    the sender has given up entirely. That makes it exactly the question
    worth asking here — not "is the socket up right now", which a poll
    would discover anyway, but "has the client stopped trying", which it
    would not.

    One attempt per cycle, with a delay after a failure and no
    escalating backoff: `_refuse_to_stall` is what ends an outage that
    does not end on its own, and two mechanisms for giving up would
    eventually disagree about which one applies.

    Reconnecting is safe with the lease held. The lease is on the
    session *file*, and a disconnect does not release it, touch the auth
    key, or empty the entity cache: what comes back is the same client.
    """
    if client.is_connected():
        return True

    logger.warning("not connected to Telegram — reconnecting")
    try:
        async with asyncio.timeout(settings.request_timeout_seconds):
            await client.connect()
    except (OSError, RPCError) as exc:
        # `ConnectionError` is an `OSError`, and so is the `TimeoutError`
        # the deadline raises; neither needs an arm of its own.
        logger.warning(
            "reconnect failed (%s: %s) — retrying in %.0fs",
            type(exc).__name__,
            exc,
            settings.watch_reconnect_delay_seconds,
        )
        await _sleep(stop, settings.watch_reconnect_delay_seconds)
        return False

    stats.reconnects += 1
    logger.info("reconnected to Telegram")
    return True


async def _drop_connection(client: TelegramClient) -> None:
    """Discard a connection that stopped answering.

    Cleanup, so it swallows its own failures and takes a deadline of its
    own: a socket wedged badly enough to strand a request is exactly the
    one that could stall the teardown too, and a cleanup path that hangs
    would reproduce the bug it is here to end.
    """
    try:
        async with asyncio.timeout(settings.request_timeout_seconds):
            await client.disconnect()
    except Exception:
        logger.warning(
            "could not close the connection cleanly; continuing anyway",
            exc_info=True,
        )


async def _poll_one(
    client: TelegramClient,
    database: Database,
    channel: DueChannel,
    *,
    stats: WatchStats,
    recorder: FloodRecorder,
) -> PollAttempt:
    """One channel, with every failure contained. Says what it concluded."""
    now = datetime.now(UTC)
    measure_rate = _rate_is_stale(channel, now)
    error: str | None = None
    found_nothing = True
    concluded = True

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
        return PollAttempt(halt=exc)
    except RequestTimedOut as exc:
        # Before the `OSError` arm, which it would otherwise land in as
        # an ordinary transient failure — and then the *next* channel
        # would be asked over the same connection, which has just proved
        # it does not answer. Discarding it makes the connection check at
        # the top of the batch loop false, and one reconnect fixes what
        # would otherwise be a failure per channel.
        logger.warning("@%s: %s", channel.username, exc)
        error = f"{type(exc).__name__}: {exc}"[:500]
        stats.timed_out += 1
        concluded = False
        await _drop_connection(client)
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

    # A timed-out poll is rescheduled with its error recorded, like any
    # other failed one, and that is a deliberate inaccuracy: the channel
    # did nothing wrong. Leaving it unrecorded would leave it the oldest
    # overdue channel and therefore first in the next batch — so a
    # channel that timed out reliably would sit at the head of the queue
    # forever and nothing behind it would ever be polled. One failure
    # costs it one backoff step. In an outage at most one channel pays
    # it, because the connection check stops the batch before the second.
    await _reschedule(
        database,
        channel,
        now=now,
        found_nothing=found_nothing,
        error=error,
        measure_rate=measure_rate,
    )
    return PollAttempt(concluded=concluded)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    """Wait, but wake immediately if asked to stop.

    A plain sleep would make shutdown take as long as a tick, which is
    the difference between a daemon that stops and one that has to be
    killed.
    """
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
