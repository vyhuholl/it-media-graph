## 1. Storage

- [x] 1.1 Add the `FloodEvent` model: `id`, `occurred_at`, `method`, `seconds`, `command`, `channel_id`, `halted`. `channel_id` is a nullable FK to `channels.tg_id` — a flood during resolution, or before a walk starts, has no channel.
- [x] 1.2 Add the `collection_command` enum with `backfill` and `resolve`, following the `_pg_enum` pattern the other enums use.
- [x] 1.3 Index `occurred_at` and `method`. Every question asked of this table filters on one or both: "what happened lately" and "how often this method".
- [x] 1.4 Generate and review the migration. Check `upgrade head` and `downgrade base` on a scratch database, then confirm `alembic check` is quiet.

## 2. Extracting the method name

- [x] 2.1 Add `method_name(request)` — walks `.query` while the request is one of Telethon's nesting wrappers, then returns the innermost class name. See `design.md`: the outer name would file every event under `InvokeWithLayerRequest`.
- [x] 2.2 Do not import `telethon.errors.rpcbaseerrors._NESTS_QUERY`. It is private and Telethon generates these classes; match by class name the way `classify` already does, and treat an unrecognised shape as "no more unwrapping to do".
- [x] 2.3 Return `unknown` for a request of `None`. It is a legitimate value — the fakes produce it, and refusing to record would lose the duration too.
- [x] 2.4 Unit-test the unwrapping against a nested request, a doubly-nested one, a bare one, and `None`. This is the part that silently produces a useless table if it is wrong.

## 3. Writing an event

- [x] 3.1 Add `db/floods.py` with `record_flood(...)`, taking its own session.
- [x] 3.2 Write on a short-lived session of its own, not the caller's. `waiting_out_floods` is called mid-transaction from `backfill_channel`, which commits per batch — committing or rolling back the caller's session inside a flood handler is how a run loses history it already fetched.
- [x] 3.3 Swallow and log **any** failure to record. Telemetry that can turn a survivable rate limit into a crashed run is worse than no telemetry.
- [x] 3.4 Record from `waiting_out_floods`, on both branches: the wait that is slept off (`halted=False`) and the wait that raises `FloodWaitTooLong` (`halted=True`).
- [x] 3.5 Add the `command` argument to `waiting_out_floods` and thread it through. Both commands call it, so it cannot be inferred there — this is the reason this is not a two-line diff.
- [x] 3.6 Pass the channel being walked where there is one, and nothing where there is not.

## 4. Reading it back

- [x] 4.1 Add `itgraph floods`: recent events, newest first — time, method, seconds, command, channel, whether it halted the run.
- [x] 4.2 Add `--since` and a per-method summary: count and longest wait per method over the window. That is the shape that answers "is this the same thing as last time", which is the question the table exists for.
- [x] 4.3 State in the output that a row does not necessarily mean a request was sent — Telethon refuses a method that is already under a wait, and that refusal arrives as a `FloodWaitError` too. See `design.md`; a reader who assumes otherwise will overcount what a run spent.
- [x] 4.4 Say something useful when the table is empty, rather than printing a header over nothing.

## 5. Tests

- [x] 5.1 Cover the spec scenarios: a slept-off wait is recorded, a halting wait is recorded as halted, the method name is the unwrapped one, a missing request records `unknown`.
- [x] 5.2 Test that a failing recorder does not break FloodWait handling — the wait is still slept or still halts, and the run's outcome is unchanged.
- [x] 5.3 Test that recording does not disturb the caller's transaction: a flood arriving mid-batch must leave already-committed rows intact and must not commit a partial batch.
- [x] 5.4 Test that `backfill` and `resolve` record their own `command`, since telling them apart is half the point.
- [x] 5.5 No network, as everywhere else. The existing fakes raise `FloodWaitError` already; give them a request object so the method name has something to unwrap.

## 6. Documentation

- [x] 6.1 Document `itgraph floods` in README, next to the FloodWait section that currently tells the operator to wait and re-run.
- [x] 6.2 Write down what the table can and cannot answer: it records long floods only, because Telethon sleeps through short ones itself, and a row is not proof a request was sent.
- [x] 6.3 Note the question this was built to settle — whether `backfill` and `resolve` share a limiter — and that answering it needs events from both commands over the same period.

## 7. Validation

- [x] 7.1 `make validate` clean.
