## Context

See [proposal.md](proposal.md) — Why. What matters for the approach:

- **Every Telegram request in the project goes through `waiting_out_floods`** in [backfill.py](../../../src/itgraph/tg/backfill.py). That is already stated as an invariant there, and it is true: `watch`, `backfill`, `metadata`, `resolve` and `manual` all call it. It is therefore the one seam where a deadline reaches every request without being remembered at each call site.
- **`waiting_out_floods` also does the waiting that must not be bounded.** A `FloodWait` under `flood_abort_threshold` is slept off inside it, and that sleep can legitimately be minutes long. A deadline around the whole helper would cancel exactly the wait this project has decided to take.
- **Telethon sleeps short floods inside the request.** `flood_sleep_threshold` is 60s and `_call` retries up to `request_retries` times, so a single legitimate `iter_messages` can take over a minute without anything being wrong.
- **`client.is_connected()` is `_user_connected`** — a flag, not a probe. It is true throughout a reconnect and false only once the sender has given up. That makes it exactly the right question to ask before a poll: not "is the socket up" but "has the client stopped trying".
- The loop's `stop` event is only read between iterations, so any wait the loop performs must go through `_sleep`, which wakes on it.

## Goals / Non-Goals

**Goals:**

- No await in the collector can block forever. A request either answers, fails, or passes a deadline.
- An outage costs the schedule the time it lasts, and nothing else: no channel is recorded as failed for being due while the connection was down.
- A loop that is stuck for a reason nobody anticipated stops being `active (running)`.

**Non-Goals:**

- Diagnosing *why* a connection wedged. The deadline does not care, and a fix that had to recognise the failure would not have caught this one.
- Changing the request budget, the pacing, or the schedule. This changes what happens when a request does not come back, not when it does.
- Making `watch` survive a machine with no network for hours without restarting. Under a supervisor, a restart is a legitimate recovery.

## Decisions

### 1. The deadline wraps the request, not the helper

```python
while True:
    try:
        async with asyncio.timeout(settings.request_timeout_seconds):
            return await operation()
    except FloodWaitError as exc:
        ...
        await asyncio.sleep(seconds)  # outside the timeout scope
```

The `async with` opens and closes inside each attempt, so the sleep in the `except` branch and the next attempt each get a fresh deadline. A rate limit is still waited out in full — the one property of this helper that must survive the change.

`asyncio.timeout` rather than `wait_for`: the scope reads as what it is, and it does not require wrapping the operation in another coroutine object.

The expiry is raised as `RequestTimedOut`, a subclass of `TimeoutError` (and so, since 3.10, of `OSError`). The subclass matters: the collector already catches `OSError` per channel, so an unnamed `TimeoutError` would be recorded as that channel's failure and the connection would be reused for the next one — which is the second wedge. A named class is catchable *before* that handler. The `OSError` ancestry is what makes it safe anyway if some future call site forgets.

**On cancellation safety:** `asyncio.timeout` cancels the pending `iter_messages`, which leaves a `RequestState` in Telethon's `_pending_state` with a cancelled future. Telethon guards every result path with `if not state.future.cancelled()` — checked in `mtprotosender.py`, six call sites — so a late response to an abandoned request is discarded rather than raising `InvalidStateError` into the receive loop.

**Default 180s, floored above `flood_sleep_threshold`.** A history request that is answering takes single-digit seconds; one that is going to answer in three minutes has already cost more than the poll is worth. But Telethon sleeps a flood of up to `flood_sleep_threshold` *inside* the request, so a deadline below that would abort a wait the project deliberately takes. A config validator states the relation, so the two settings cannot be moved into contradiction — 180 against a 60s threshold leaves room for the sleep plus the retry that follows it.

### 2. A deadline drops the connection; it does not just fail the poll

A request that never came back means the connection is not usable, whatever `_user_connected` says. So `_poll_one` catches `RequestTimedOut` separately and disconnects the client, deliberately, before returning.

Without this, the wedge that hides behind a still-true `_user_connected` — precisely the state this incident produced — would repeat per channel: 180 seconds each, forever, with the connection check never triggering because the client claims to be connected. Dropping it makes the next cycle's check false, and the reconnect path handles the rest. One code path recovers both cases, which is the point.

The disconnect gets its own short deadline and swallows its own failures. It is cleanup: a wedged socket must not be able to wedge the teardown too.

**The channel is still recorded as failed.** This is the one place the change accepts an inaccuracy, and it is deliberate. The channel did nothing wrong, but "poll it again immediately" is what not recording it means — and since it is then the oldest overdue channel, it is first in the next batch, and a channel that reliably times out would head-of-line block the entire queue forever. One failure costs it one backoff step. In an outage at most one channel pays it, because the connection check stops the loop before the next one.

### 3. The connection is checked before every poll, not once per cycle

A batch is 25 channels and, at the configured pacing, a few minutes. A check at the top of the cycle would let a connection lost at channel 2 be discovered after 23 more channels had each failed with `ConnectionError: Cannot send requests while disconnected` and been recorded as failures, with the failure backoff pushing each of them out by up to a day. The queue would be poisoned by an outage that touched nothing but the socket.

So the batch loop breaks on `not client.is_connected()`, the same way it breaks on `stop`. The check is a flag read: no I/O, no cost worth reasoning about.

Reconnection is one `client.connect()` per cycle, under the same deadline, with `watch_reconnect_delay_seconds` between attempts. Telethon retries internally before that call returns, so a "failed attempt" here is already several. There is no escalating backoff and no maximum: the stall check below is what ends an outage that does not end on its own, and one mechanism for giving up is better than two that disagree.

`connect()` is safe to call on a client whose sender gave up — `MTProtoSender.connect` returns early only when `_user_connected` is true, and a sender that gave up has set it false. The session file, the auth key and the entity cache are untouched by a disconnect, so a reconnected client is the same client, holding the same lease.

### 4. The stall check is a task beside the loop, not a step inside it

**This decision changed during implementation, and the reason is worth keeping.** The check began as the first statement of each cycle, and a test written to reproduce the incident — a coroutine that never returns, in place of `poll_channel` — showed what that is worth: nothing. A check at the top of the cycle only runs if the loop is still reaching the top of its cycles, and the failure it exists for is a loop that stopped reaching it. It shared its subject's liveness, which is the same flaw that made `Restart=always` useless here, reimplemented one level up.

So `watch` now runs two tasks: `_cycles`, which is the loop as it was, and `_refuse_to_stall`, which owns a monotonic clock and nothing else. Whichever finishes first decides — the other is cancelled, and the first is awaited so its outcome propagates.

**`asyncio.wait`, not a `TaskGroup`**, and this too was found by a test rather than by reasoning. A `TaskGroup` re-raises everything as an `ExceptionGroup`, including an exception raised by the body it hosts — so `LeaseLostError` came back wrapped, missed the tuple `cli.py` catches, and printed a traceback where a written sentence used to be. The stall check would have been paid for by breaking an error path that already worked. Two tasks and an explicit `await` keep every failure the shape it was raised in; `test_a_lost_lease_still_arrives_as_itself` holds that line.

The watchdog sleeps exactly as long as it would take to stall, then re-reads the clock. Progress made in the meantime simply means the next sleep is longer; there is no polling interval to pick, and a healthy loop wakes it a handful of times a day.

Progress is **a poll that concluded**: stored, skipped, or failed with an answer. Two things are deliberately *not* progress:

- **A request that passed its deadline**, which taught the loop nothing. A loop timing out on every request is exactly what a restart is for.
- **A successful reconnect**, for the same reason. Reconnecting and then learning nothing is not working, however busy the log looks.

Three states that mean there is legitimately nothing to conclude *do* count, or the watchdog would fire on a healthy idle loop: quiet hours, an empty queue, and a schedule postponed by a rate limit.

Default 30 minutes. Long enough that a batch of deadlines cannot trip it — each of those is a concluded attempt — and short enough that a wedge costs half an hour rather than three days.

**A long outage does not, by itself, restart the process.** Worth stating because it is not what the first design did. A channel whose poll times out is rescheduled with its failure recorded, so during a real outage the queue drains into the future and the loop settles into "nothing due" — which is progress, and honestly so: the loop is running, retrying on the backoff, and will pick up when the network returns. What still trips the watchdog is the case it was written for: nothing due-and-concluding, because the loop is not getting anywhere at all.

### 5. `SessionLease.verify` commits

Unrelated to the hang and found because of it: `pg_stat_activity` showed the collector's lease connection `idle in transaction` for 2 days 19:52.

`acquire` commits after taking the lock, with a comment saying why — a session-level advisory lock survives the commit, and leaving the connection idle rather than idle-in-transaction keeps a server-side timeout from killing it. `verify` then runs a `SELECT` on that same connection every five minutes and never commits, re-opening the transaction and leaving it open. The comment in `acquire` is describing a property the class does not have.

`verify` commits after its read. Not autocommit for the whole connection: `acquire`'s explicit commit is load-bearing and reads as a decision, and a connection-wide mode change would make it look redundant.

## Risks / Trade-offs

- **A legitimate slow request now fails.** A request slower than 180s is abandoned and its channel recorded as failed. Nothing observed comes close; the schedule absorbs a failure by design.
- **`RequestTimedOut` reaches four other commands.** `backfill`, `metadata`, `resolve` and `manual` inherit the deadline through the shared helper. Each already treats an `OSError` as that channel's transient failure, and `RequestTimedOut` is one, so the existing handling is correct without being changed. Only the loop, which has to keep running afterwards, needs the connection drop.
- **The stall check can fire on a loop that is fine.** It would take a 30-minute stretch where channels are due and no poll concludes. Every wait the loop takes is bounded and every conclusion counts, so this needs an unanticipated wedge — which is what it is for. The cost of a false positive is a restart of a process designed to be restartable.
- **The watchdog cancels the loop mid-poll.** A stall is resolved by cancelling `_cycles` wherever it happens to be, which rolls back the transaction in flight. That is the same loss as any kill, and the loop is already built to survive one: the cursor and the schedule are committed per channel, so a restart resumes rather than repeats.
- **The deadline is not the bug's fix.** Telethon still loses these requests; we now stop waiting for them. If upstream fixes `_send_queue` on disconnect, the deadline stops firing and nothing here needs removing.
