## 1. Schema

- [x] 1.1 `db/models.py`: `MessageMetric` — `channel_id`, `msg_id`, `observed_at` as the composite primary key; `views`, `forwards`, `comments` as nullable ints; `reactions` as nullable `JSONB`. Composite `ForeignKeyConstraint(["channel_id", "msg_id"], ["raw_messages.channel_id", "raw_messages.msg_id"], ondelete="CASCADE")` — it is what makes a snapshot of a message the raw layer does not hold impossible, and it costs no index because it references that table's primary key
- [x] 1.2 Index `observed_at` on its own: the alert pass will read "everything since the last run", and the primary key's leftmost prefix cannot serve that
- [x] 1.3 Docstring on `MessageMetric` in the house style — why append-only, why NULL is not zero, and why no reaction total is stored (a sum is a derived measure, the same trade `Edge` refuses when it declines to store the interval between the two dates it carries)
- [x] 1.4 `db/models.py`: `PollState` — `channel_id` primary key and FK, `due_at` (indexed), `last_polled_at`, `posts_per_day`, `consecutive_empty`, `last_error`. Docstring must say why this is not a column on `BackfillState`: that table has terminal statuses and polling has no terminal state
- [x] 1.5 Add `WATCH = "watch"` to `CollectionCommand` and extend its docstring — it currently enumerates which command owns which quota-bearing method, and the watch loop is a third command that must own none
- [x] 1.6 Generate the Alembic revision. Hand-edit it: **Postgres refuses to use a new enum value in the transaction that added it**, so `ALTER TYPE collection_command ADD VALUE 'watch'` must not sit in the same revision body as anything writing that value. Downgrade drops the two tables and leaves the enum value stranded, with a comment saying `ALTER TYPE ... DROP VALUE` does not exist
- [x] 1.7 Verify the revision on a scratch database whose name ends in `_test`. Read `alembic upgrade --sql` first. The working database gets a full dump before any upgrade, per the backup rule

## 2. The session lease

- [x] 2.1 `db/session_lease.py`: acquire via `pg_try_advisory_lock` on a key derived from the resolved session-file path, so two different session files do not block each other and two commands on one session do. Non-blocking — a held lease is an immediate refusal, never a wait
- [x] 2.2 The lease is held on a **dedicated connection kept for the process's lifetime**, not one taken from the pool. An advisory lock belongs to a session, and a pooled connection returned between statements takes the lock with it — this is the one bug in this change that would put two writers on one session file
- [x] 2.3 Record who holds it (command name, pid, host, started_at) somewhere the refusing process can read, so the error names the holder rather than saying "in use". A tiny table or the advisory lock's own catalog view — either is fine, but the message must be specific
- [x] 2.4 Every command that connects to Telegram acquires the lease first: `backfill`, `resolve`, `metadata`, `add`, `dialogs`, `auth`, `watch`. Read-only commands that touch no session do not
- [x] 2.5 Losing the lease is fatal for a long-running holder: if the dedicated connection drops, exit rather than reconnect and assume the lease survived
- [x] 2.6 `tests/test_session_lease.py`: a second acquisition of the same key fails immediately; a different key succeeds; a dropped connection frees the lease with nothing to clean up; the refusal message names the holder

## 3. Payload → counters, as a pure function

- [x] 3.1 `derive/metrics.py`: one function, payload → the four counters, no network, no session, alongside `derive/references.py`
- [x] 3.2 Distinguish absent from zero. The payload stores an absent field as JSON `null`, so "this channel publishes no reactions" and "nobody reacted" are different results and only the second is a zero. `notebooks/anomalous_posts.py` reconstructs this per channel in SQL precisely because the current data lost it — it is captured correctly here, once
- [x] 3.3 Reactions come back per emoji, not summed. A `reactions.results` that is present but empty is an empty mapping; a missing `reactions` object is absent
- [x] 3.4 A payload that is not a `Message` — a `MessageService` "channel photo changed" event — yields no snapshot at all rather than a row of NULLs
- [x] 3.5 Album parts are **not** collapsed here. Each part is its own message with its own counters, and merging them is a counting decision that belongs to analysis, exactly as `Edge` stores `grouped_id` without applying it
- [x] 3.6 `tests/test_derive_metrics.py`: absent vs zero for reactions and comments, per-emoji preservation, service messages, a post with no view count

## 4. Writing snapshots

- [x] 4.1 `db/metrics.py`: batch insert of snapshots for one channel. Nothing here reads a payload, matching `db/raw.py`
- [x] 4.2 Snapshots and the payloads they describe commit in one transaction, payloads first — the foreign key from 1.1 is what enforces the ordering, and a poll that stored messages but died before its snapshots must not leave the two inconsistent
- [x] 4.3 `ON CONFLICT DO NOTHING` on the primary key: two observations inside the same clock tick are one row, not a crash
- [x] 4.4 `tests/test_metrics_db.py`: a second snapshot is a second row; an earlier snapshot is never rewritten; a snapshot for a message not in the raw layer is refused by the database

## 5. The schedule

- [x] 5.1 `db/poll.py`: the due query — in-scope channels ordered by `due_at`, with a missing row treated as due now, so `poll_state` seeds itself on the first pass
- [x] 5.2 The snapshot schedule as a pure function: post age → the next unelapsed sample, over the configured offsets (+15m, +30m, +1h, +2h, +4h, +8h, +24h, +48h and the horizon). **Computed from the post's current age, never from what the previous sample was supposed to be** — that is what makes a missed sample skipped instead of replayed, and it means no wall-clock delta or suspend detection is needed anywhere
- [x] 5.3 A channel's `due_at` is the earliest next sample over its posts still inside the horizon, floored by the configured minimum gap after the last poll so a burst of posts does not produce a burst of polls
- [x] 5.4 A channel with nothing live falls back to an interval derived from `posts_per_day`, clamped to the configured idle bounds
- [x] 5.5 `posts_per_day` is computed from `raw_messages` and cached on the row, refreshed on a slow cadence — recomputing it per tick would cost more in queries than the polling costs in requests
- [x] 5.6 `consecutive_empty` and `last_error` lengthen a channel's interval; a successful poll resets them
- [x] 5.7 `tests/test_poll.py`: the schedule at each age; a post past the horizon yields no sample; several live posts collapse to one `due_at`; an eight-hour gap produces exactly one poll per channel and no backlog; the idle interval respects its clamps

## 6. The loop

- [x] 6.1 `tg/watch.py`: select due channels, poll one at a time, `pace()` before each request, through `waiting_out_floods` with a `FloodRecorder` on `CollectionCommand.WATCH`. Never concurrent — Telegram's limits are per account, so parallel workers reach the same ceiling faster and look worse doing it
- [x] 6.2 The peer comes from the existing `cached_peer`, which asks the *session* rather than the client. `client.get_input_entity` falls through to `contacts.resolveUsername` on a miss, and in a loop that would spend the day's tightest quota within the hour
- [x] 6.3 Window size is derived — `posts_per_day × horizon`, clamped to `[10, 100]` — and the walk stops early at the first message older than the horizon that is already stored
- [x] 6.4 Store new payloads, advance `BackfillState.newest_fetched_id` and `Channel.last_post_at`, write the snapshots, commit. `newest_fetched_id` has been recorded since the backfill change as the mark "incremental collection will read"; this is that reader, and it reads it in place rather than copying it
- [x] 6.5 **Catch `PeerNotCached` before the generic handler.** It is not an `RPCError` for a reason: `classify` files the underlying `ValueError` as `PERMANENT`, and a permanent failure drops the channel out of scope for good. In a loop the same trap is worse — it retires the channel silently and forever
- [x] 6.6 **Catch `FloodWaitTooLong` at the loop level**, also before the generic handler, and convert it: push every `due_at` past the wait, sleep, continue. It is deliberately not an `RPCError` so that the per-channel handler cannot see it. Nothing is recorded as a failure against the channel that happened to be in flight
- [x] 6.7 Per-channel failure isolation: record against the channel, back its interval off, continue with the next
- [x] 6.8 Quiet hours from config, in an explicit timezone — a naive local time is wrong the moment this runs anywhere but the operator's laptop. An empty window means always on. Leaving the window does not trigger catch-up
- [x] 6.9 Graceful shutdown on SIGINT/SIGTERM: finish or abandon the poll in flight without a partial commit, release the lease, exit non-zero only on error
- [x] 6.10 A periodic summary line: polled, stored, snapshotted, skipped for no cached peer, failed, and how far behind the schedule is
- [x] 6.11 `tests/test_watch.py`. The fake client must assert the invariant this loop is most likely to break by accident: **a poll issues no `resolveUsername` and no `getFullChannel`**. Also — one request serves both jobs; a poll finding nothing new still snapshots; a capped channel is still polled forward; a long flood postpones every channel and does not exit; a killed process leaves committed rows and a consistent cursor
- [x] 6.12 Extend `tests/fakes.py` so `FakeTelegramClient` returns messages carrying counters that can change between calls — the current fake has no notion of a value that moves, which is the whole subject here

## 7. CLI and configuration

- [x] 7.1 `config.py`: snapshot offsets and horizon, idle interval bounds, minimum gap between polls of one channel, window-size clamps, quiet-hours window and timezone, `posts_per_day` refresh cadence, catch-up behaviour. Conservative defaults, with the reasoning in comments as the backfill pacing settings have
- [x] 7.2 `cli.py`: `itgraph watch` — takes the lease, runs until stopped. Body short, logic in `tg/watch.py`
- [x] 7.3 `cli.py`: a read-only status command reading `poll_state` — how many channels are overdue and by how much, when the last poll happened, how many snapshots were written recently. It must **not** take the session lease, or it could not be run while the loop it reports on is running
- [x] 7.4 `tests/test_cli.py`: `watch` refuses when the lease is held; the status command works while it is held

## 8. Documentation

- [x] 8.1 Four rows in the module table in `src/itgraph/CLAUDE.md`: `tg/watch.py`, `db/metrics.py`, `db/poll.py`, `db/session_lease.py`, plus `derive/metrics.py`
- [x] 8.2 `src/itgraph/README.md`: how to run the loop, what the status command says, and that the session is now exclusive — the operator has to stop the loop to run a backfill
- [x] 8.3 `docs/PLAN.md`: the alert-bot row splits into collection and judgement, and the `arq` suggestion is recorded as not taken, with the reason
- [x] 8.4 A line stating the rule the scoring pass will depend on: **read `observed_at − published_at`, never the intended slot.** Snapshots are irregular by design and the schedule is not a promise

## 9. Close out

- [x] 9.1 `make validate` green — lint, mypy, pytest, coverage at or above the configured floor
- [x] 9.2 `openspec validate add-metric-snapshots` green
