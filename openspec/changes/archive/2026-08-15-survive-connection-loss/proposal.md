## Why

On 13 August 2026 at 00:15 MSK the collector stopped collecting and did not stop running. It was found 67 hours later, still `active (running)` under systemd, with 551 channels due and the oldest overdue by two days and twenty hours. Nothing alerted, because from every angle anything looks at, the process was healthy: a live PID, an open database connection, no error after the first minute.

What happened is a hole in a library, and the hole is worth stating exactly, because the fix follows from its shape rather than from "add a timeout somewhere".

Telegram closed the connection. Telethon began its automatic reconnect, and during that reconnect the loop issued its next history request. `MTProtoSender.send` accepts a request whenever `_user_connected` is true — and that flag stays true for the whole of a reconnect — so the request was accepted and queued into `_send_queue`. The send loop had already been cancelled by `_reconnect`. Six minutes later the reconnect gave up for good, and `_disconnect(error)` failed every future in `_pending_state`… which is the wrong collection. Our request was never sent, so it was never in `_pending_state`; it was in `_send_queue`, which nothing drains and nothing fails. The future it belongs to will not be resolved by any code path that exists.

`await` on that future is where the process spent the next two days and twenty hours.

Everything downstream then failed to notice, and each of those is its own gap:

- **The loop has no deadline.** `waiting_out_floods` waits out rate limits forever by design, and that is right; but the request inside it can also wait forever by accident, and nothing distinguishes the two.
- **`Restart=always` cannot fire**, because the process never exits. A supervisor restarts what dies, and this did not die.
- **`SIGTERM` does not stop it either** — confirmed during the investigation. The handler sets an `asyncio.Event` the loop reads between iterations, and the loop never reaches its next iteration. Only `SIGKILL` ends this process, which means `TimeoutStopSec=90` is the real stop mechanism and a clean shutdown is not available in exactly the state where one is wanted.
- **Nothing watches for silence.** `itgraph watch-status` would have shown it in one line, but only to someone who thought to ask.

This is not specific to the poll loop. Every Telegram request in the project goes through `waiting_out_floods`, so a backfill can wedge the same way; it is less costly there only because a human is watching it.

## What Changes

- **Every Telegram request gets a deadline.** `waiting_out_floods` runs the request under `asyncio.timeout` and raises `RequestTimedOut` when it passes. The deadline covers the request only — a `FloodWait` slept off in the handler is outside it, so waiting out a rate limit is unaffected, which is the one behaviour that must not change. One edit, at the seam every request in `tg/` already passes through, so `backfill`, `metadata`, `resolve` and `manual` are covered by the same line as `watch`.
- **A request that passes its deadline drops the connection.** A deadline means the connection is not usable, whatever the sender believes about itself. The loop tears it down rather than issuing the next request over it, which turns a wedge into an ordinary reconnect instead of into a second wedge.
- **The loop checks it is connected, and reconnects when it is not.** Before each poll, not once per batch: a connection lost mid-batch would otherwise fail the remaining 24 channels in a couple of minutes and push each of them out by the failure backoff. While it cannot connect, the loop polls nothing, records nothing against any channel, and retries on a delay.
- **A loop that stops making progress exits, so the supervisor can restart it.** The loop tracks when it last completed a poll of any outcome — stored, skipped or failed. If nothing at all completes while channels are due, it raises `WatchStalled` and exits non-zero. That is the catch-all: it does not know why the loop is stuck, and does not need to.
- **Also fixed, found in the same investigation:** `SessionLease.verify` leaves its connection `idle in transaction` — the hung process had held one that way for 2 days 19:52. `acquire` commits deliberately to avoid exactly that state; `verify` then re-opens it on every check and never commits. A server-side `idle_in_transaction_session_timeout` would kill that connection, and killing it takes the lease with it — turning a routine setting into a `LeaseLostError` that stops the collector. Two lines, in this change because it was found here.

Out of scope:

- **Patching or forking Telethon.** The bug is real and upstream's to fix; the deadline makes it survivable without owning a fork of the library the whole project rests on.
- **Alerting the operator that collection has stopped.** It is the obvious next thing and it is a different change: the bot, a threshold, and a decision about what silence is worth interrupting someone for. This change makes the loop recover on its own; it does not make it tell anyone.
- **`Type=notify` and a systemd watchdog.** A stronger version of the stall check, paid for with an sd_notify dependency and a unit that only works under systemd. The in-process check needs neither and stops a hand-run loop too.
- **Reducing the reconnect window.** Telethon's own `connection_retries` and `retry_delay` are left alone. Faster giving-up is not the problem; not noticing that it gave up is.

## Capabilities

### Modified Capabilities

- `message-backfill`: **Rate Limit Compliance** gains the boundary between the two kinds of waiting — a rate limit is waited out without limit, a request that answers nothing is abandoned on a deadline.
- `metric-snapshots`: a new requirement, **The Loop Survives A Lost Connection**, covering the deadline, the reconnect, what is and is not recorded against a channel during an outage, and the deliberate exit when the loop stops making progress.

## Impact

- `src/itgraph/config.py` — `request_timeout_seconds`, `watch_reconnect_delay_seconds`, `watch_stall_minutes`, and a validator keeping the deadline above `flood_sleep_threshold` (Telethon sleeps a short flood *inside* the request, so a deadline below it would abort a wait the project has decided to take).
- `src/itgraph/tg/errors.py` — `WatchStalled`, beside `NotAuthorizedError`, so `cli.py` catches it without importing Telethon.
- `src/itgraph/tg/backfill.py` — the deadline in `waiting_out_floods`, and `RequestTimedOut`.
- `src/itgraph/tg/watch.py` — the connection check, the reconnect, the deadline's handling, the progress clock.
- `src/itgraph/db/session_lease.py` — `verify` commits.
- `src/itgraph/cli.py` — `_run` catches `WatchStalled`; the `watch` docstring says the loop exits when it stops making progress.
- Tests: `test_backfill.py`, `test_watch.py`, `test_session_lease.py`, `test_config.py`, and `is_connected`/`connect`/`disconnect` on `FakeTelegramClient`.
- No schema change, no migration, no new request type, and no change to how many requests the loop makes.
