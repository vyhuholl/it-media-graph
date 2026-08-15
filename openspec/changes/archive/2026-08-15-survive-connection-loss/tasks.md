## 1. The deadline, at the seam every request already passes through

- [x] 1.1 `config.py`: `request_timeout_seconds: float = Field(default=180.0, gt=0)`, documented as bounding one request and not a rate-limit wait. Not `watch_`-prefixed: it reaches every command through `waiting_out_floods`, and a name suggesting otherwise would be wrong at four of the five call sites
- [x] 1.2 `config.py`: a model validator refusing `request_timeout_seconds <= flood_sleep_threshold`, naming both settings and saying why — Telethon sleeps a short flood *inside* the request, so a deadline below the threshold aborts a wait this project has decided to take. Beside `_watch_bounds_are_ordered`, in the same shape
- [x] 1.3 `tg/backfill.py`: `RequestTimedOut(TimeoutError)`, carrying the elapsed deadline in its message. Subclassing `TimeoutError` gets `OSError` ancestry for free, so a call site that does not name it still treats it as the transient failure it is
- [x] 1.4 `tg/backfill.py`: in `waiting_out_floods`, run `operation()` inside `async with asyncio.timeout(settings.request_timeout_seconds)` and re-raise the expiry as `RequestTimedOut`. The scope opens inside the loop so each attempt gets a fresh deadline, and closes before the `except FloodWaitError` branch so the flood sleep is outside it. The docstring gains the distinction: this helper waits out rate limits without limit and waits on a request for a bounded time
- [x] 1.5 Tests in `test_backfill.py`: an operation that never returns raises `RequestTimedOut` within the deadline; a `FloodWait` longer than the deadline but under the halt threshold is still slept off in full and the retry then succeeds; the deadline does not fire on an operation that returns normally
- [x] 1.6 Test in `test_config.py`: a deadline at or below `flood_sleep_threshold` is refused, and the message names both settings

## 2. The loop keeps a working connection

- [x] 2.1 `config.py`: `watch_reconnect_delay_seconds: float = Field(default=30.0, gt=0)` — the gap between reconnect attempts, on top of the retries Telethon already makes inside `connect()`
- [x] 2.2 `tg/watch.py`: `_ensure_connected(client, stop) -> bool` — returns True when `client.is_connected()`; otherwise logs, calls `client.connect()` under `request_timeout_seconds`, and on failure sleeps `watch_reconnect_delay_seconds` through `_sleep` (so a stop still wakes it) and returns False. Catches `OSError` and `RPCError`; `ConnectionError` is an `OSError` and needs no separate arm
- [x] 2.3 `tg/watch.py`: call it at the top of each cycle, after the quiet-hours check and before any channel is selected. A cycle that cannot connect selects nothing, records nothing, and continues
- [x] 2.4 `tg/watch.py`: in the batch loop, break on `not client.is_connected()` beside the existing `stop` check. Per channel, not per cycle — a connection lost at channel 2 of 25 must not cost the other 23 a recorded failure and a backoff. A flag read, so it costs nothing
- [x] 2.5 `tg/watch.py`: `_drop_connection(client)` — `client.disconnect()` under a short deadline, swallowing and logging any failure. Cleanup that must not be able to wedge the loop it is cleaning up for
- [x] 2.6 `tg/watch.py`: `_poll_one` catches `RequestTimedOut` **before** the `OSError` arm, logs it against the channel, counts it in a `timed_out` stat, and calls `_drop_connection`. The channel is still rescheduled with the error recorded — task 2.7 says why
- [x] 2.7 Docstring on that arm: the channel did nothing wrong, and recording the failure anyway is deliberate. Not recording it leaves the channel the oldest overdue and therefore first in the next batch, so a channel that reliably times out would head-of-line block the queue forever. One failure costs one backoff step, and in an outage at most one channel pays it because 2.4 stops the batch
- [x] 2.8 `WatchStats.timed_out` and its clause in `line()`, beside `failed` and `skipped` — a degraded state that is not a channel failure is exactly what that report exists to make visible
- [x] 2.9 `tests/fakes.py`: `FakeTelegramClient` grows `is_connected()`, `connect()` and `disconnect()` over a `connected` flag, plus `connect_failures` (how many reconnects to fail before succeeding) and counters for both. Models Telethon: `is_connected` is a flag read, not a probe
- [x] 2.10 Tests in `test_watch.py`: a client that starts disconnected polls nothing and records no failure against the due channels; it reconnects and then polls them; a connection lost partway through a batch stops the batch and leaves the untouched channels' schedules alone; a reconnect that fails is retried on the next cycle
- [x] 2.11 Tests in `test_watch.py`: a request that passes its deadline is recorded against the channel, disconnects the client, and the loop carries on — the next cycle reconnects and polls the remaining channels

## 3. A loop that stops making progress stops

- [x] 3.1 `config.py`: `watch_stall_minutes: float = Field(default=30.0, gt=0)`, documented as the longest the loop may go without concluding a poll while channels are due
- [x] 3.2 `tg/errors.py`: `WatchStalled(RuntimeError)`. Here, not in `watch.py`, so `cli.py` can catch it without importing Telethon — the reason that module exists
- [x] 3.3 `tg/watch.py`: `_Progress`, a monotonic clock, advanced when a poll *concludes* (stored, skipped or failed with an answer) and on the three states that mean there is legitimately nothing to conclude: quiet hours, an empty queue, a postponed schedule. Deliberately **not** advanced by a request that passed its deadline or by a successful reconnect — a loop that times out on everything, or reconnects and learns nothing, is the state a restart is for. Success alone would be the wrong measure the other way: every channel might legitimately be uncollectable, and a restart would not fix a cold entity cache
- [x] 3.4 `tg/watch.py`: `_refuse_to_stall` runs as its own task beside `_cycles`, sleeping exactly as long as it would take to stall and re-reading the clock. **Not** a check at the top of each cycle: that only fires if the loop is still reaching the top of its cycles, which is precisely what the incident's loop stopped doing — a guard sharing its subject's liveness. `watch` joins the two with `asyncio.wait(FIRST_COMPLETED)` and **not** a `TaskGroup`: a group re-raises the body's own exception inside an `ExceptionGroup`, which would have left `LeaseLostError` unmatched by the tuple `cli.py` catches — buying the stall check by breaking an error path that already worked
- [x] 3.5 `cli.py`: add `WatchStalled` to the tuple `_run` catches, imported from `tg.errors` alongside `NotAuthorizedError`
- [x] 3.6 `cli.py`: the `watch` docstring gains a sentence — the loop absorbs rate limits and connection loss, and exits non-zero only when it has stopped making progress at all
- [x] 3.7 Tests in `test_watch.py`: a loop wedged *inside one await* — `poll_channel` replaced by a coroutine that never returns — raises `WatchStalled`, which is the incident and the whole case for 3.4; a loop that is idle, in quiet hours, or postponed by a rate limit does not, each run deliberately past the stall window rather than inside it; and a `LeaseLostError` out of the loop still arrives as itself rather than wrapped, which is what pins the second half of 3.4
- [x] 3.8 Test in `test_cli.py`: `WatchStalled` out of the loop exits 1 with its sentence and no traceback

## 4. The lease connection stops sitting in a transaction

- [x] 4.1 `db/session_lease.py`: `verify` commits after its read. Comment says what it is for: `acquire` commits deliberately so the connection is idle rather than idle-in-transaction, and a `verify` that never commits re-opens the transaction every five minutes and undoes that. An `idle_in_transaction_session_timeout` would then kill the connection, and killing it takes the lease with it
- [x] 4.2 Test in `test_session_lease.py`: after `verify`, the lease's own backend is not `idle in transaction` — read from `pg_stat_activity` — and the lock is still held

## 5. Close out

- [x] 5.1 `make validate` green — lint, mypy, pytest, ansible-lint
- [x] 5.2 `src/itgraph/README.md`: the `watch` section says what the loop survives on its own and the one condition under which it exits; the settings table gains the three new settings
- [x] 5.3 `src/itgraph/CLAUDE.md`: check the `tg/watch.py` and `tg/backfill.py` rows still describe what those modules do, and leave them alone if so
- [x] 5.4 `openspec validate survive-connection-loss --type change` green
- [x] 5.5 Restart `itgraph-watch` onto the new code and confirm from the journal that it polls, and that a poll concludes
