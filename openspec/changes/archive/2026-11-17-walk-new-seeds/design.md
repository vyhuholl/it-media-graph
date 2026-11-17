# Design

## Context

See proposal.md for why. What shapes the approach:

- **One process holds the session, and it is the poll loop.** `watch` takes the session lease at start and keeps it. Every other networked command refuses while it runs, `itgraph backfill` included, which is why a walk today means stopping collection. So a walk that needs no operator has to happen inside the loop.
- **The walk and the poll already share one cursor.** `tg_backfill_state` holds `oldest_fetched_id` (the walk's, backwards) and `newest_fetched_id` (the poll's, forwards, through `advance_newest`, which never moves it back). The first poll of a new seed creates its row in status `pending`. A walk started after that does not overwrite the poll's forward mark, and storing a message twice is a no-op. Nothing here needs a new table.
- **`backfill_channel` walks a whole channel in one call:** a page per iteration, rows and cursor committed together, the ceiling counted from the rows, `complete` recorded at the end. The loop needs the same walk one page at a time.
- **The loop's idle time is most of its time.** It makes 3–4 thousand requests a day at a 6-second pace, so most ticks find nothing due and sleep. A new seed's walk is 3 pages at the median and 20 at most.
- **Quiet hours are already the loop's.** It makes no request between 02:00 and 07:00 Moscow time, whatever is due.
- **YouTube's walk already selects new seeds.** `channels_to_walk` returns every seed whose walk never began or did not end, and a run with none makes no request. Only its schedule is daily.

## Goals / Non-Goals

**Goals:**
- A new Telegram seed's history arrives with no operator and no stop of collection, without costing the polls a sample.
- One walk implementation, used by both `itgraph backfill` and the loop, so that the ceiling, the cursor and failure handling cannot drift apart.
- A seed waiting for its walk is visible to the shared monitoring.

**Non-Goals:**
- Resolving usernames inside the loop: change 2.
- Deepening a walk that is complete.
- Any change to what a poll reads or how often.

## Decisions

### 1. The walk lives in the poll loop

The loop already holds the session and spends most of its time idle. Walking there costs no lease handoff, no stop and no second process.

Rejected:
- **A CronJob that takes the session.** It would have to stop the loop to get the lease, and that would cost every poll due meanwhile its sample — the price the operator pays by hand today, paid on a schedule.
- **A lease handoff,** with the loop releasing the session on request and taking it back. Two processes coordinating one session file is the risk the lease exists to remove.

### 2. A seed awaits its walk until its walk is complete; the depth is a setting

What is walked: every seed in scope — `in_scope()`, the predicate every networked pass shares: a seed, not a chat, no permanent failure — whose `tg_backfill_state.status` is not `complete`. That is no row yet, `pending` (the poll's), `running` (interrupted), `skipped` or a transient `failed`.

Where to: `history_walk_since`, a setting defaulting to `2025-07-24T00:00Z`, the depth 534 of the 550 seeds were walked to. The ceiling is `backfill_max_messages`, 2,000, the setting `itgraph backfill` uses, so the two cannot disagree.

A seed whose walk is `complete` is done for the loop, whatever its cutoff. Every seed is complete today, so the release walks nothing until a seed is marked. The 16 with a later cutoff stopped at the ceiling, and a seed someone walked to a later `--since` keeps the depth they chose.

Rejected:
- **"Complete to the configured cutoff"** (`was_completed_to`) as the test. It would silently deepen a seed an operator had walked shallower on purpose. On the 16 capped seeds it would ask nothing, but it would read their row count on every idle tick to find that out.
- **A cutoff relative to now** ("a year back"). The operator asked for the common date, so that a new seed's graph covers the same period as everyone else's.

### 3. One page of one walk per idle tick, shared with `itgraph backfill`

`backfill_channel` is split. A step function, `walk_page`, does what one iteration does today:
- read the cursor and the row count;
- ask for one window, paced by `backfill_request_delay`;
- keep what is above the cutoff;
- store rows and cursor in one transaction;
- report whether the channel is now done, complete or capped, and record `complete` if so.

`itgraph backfill` loops over it, with the same results and the same report as today. Its tests are the regression check.

One thing changes for the command, and it changes towards the spec. The ceiling is now counted from the rows before every page. The old loop took the count once and then subtracted every message a page kept, so a message already stored by a poll counted twice. A channel the loop had polled for a while would stop short of its ceiling by that overlap. Now it stops at the ceiling in rows, which is what "Per-Channel Message Ceiling" asks: the messages a channel holds are what is counted, and "rows collected by earlier runs count too". `itgraph backfill`'s tests pass unedited: none of them stored a message twice.

A seed can already hold its ceiling before its first page. Its polls stored that many, which a busy channel marked late could do. Then `walk_page` asks for nothing and records nothing (`asked` false), as `itgraph backfill` has always left such a channel. The loop then records the walk complete at the date of the oldest message held, never at `history_walk_since`, which the rows do not reach. Without that record the seed would be chosen again on every idle tick.

In the loop, after the due polls of a tick, if nothing is due:
- take the seed awaiting its walk that was marked earliest (`reviewed_at`, then id), skipping any still inside its hour of retry (Decision 4);
- ask for one page of it;
- go round again: quiet hours, connection, due polls, then the next page.

The loop sleeps only when there is neither a poll due nor a page to walk. One seed is walked to the end before the next is started. Starting a different seed waits out `backfill_channel_pause_*` since the last page, measured, not slept: until it has passed, the loop polls or idles. A page Telegram answered is progress for the stall check, as a poll is, whatever it stored.

Scope is read anew before every page, so a seed rejected mid-walk gets no further page.

### 4. A walk that could not run waits an hour, in the loop's memory

A seed whose peer the session does not hold is recorded `skipped`, with the reason, as `itgraph backfill` records it: no username is resolved. A transient failure is recorded `failed` with its kind. Either way the loop keeps `not_before = now + 1 h` for that seed in a dict, and another seed waiting is walked meanwhile. A permanent failure is recorded as such and takes the channel out of scope, which ends its polls too, as today.

In memory rather than in a column: `tg_backfill_state.updated_at` is touched by every poll through `advance_newest`, so it cannot time a retry. A column of its own would be a revision for a convenience. A restart forgets the dict and tries each waiting seed at once — one request each at most, and a lookup of the cache with none at all for a skipped one.

### 5. A rate limit stops the walk with the polls

A page goes through `waiting_out_floods`, as every request does. A short wait is slept inside it, holding up the loop as a poll's does. One past the halt threshold raises `FloodWaitTooLong`. The loop handles it as it handles a poll's: it postpones every poll past the wait and records the event under `watch`. It also holds every walk until the same moment. Without that, the next idle tick would ask again during the wait.

### 6. Two series, and a grant of two columns

- `itgraph_telegram_seeds_awaiting_history`: the count of seeds Decision 2 would walk.
- `itgraph_telegram_oldest_awaiting_history_seconds`: now minus the earliest `reviewed_at` among them, 0 when none.

Both are worked out at scrape time from `tg_channels` and `tg_backfill_state`, as every series is. `reviewed_at` is when the channel became a seed, which is when its wait begins. Every seed has one, measured on 2026-11-17.

One revision grants the exporter `SELECT (reviewed_at)` on `tg_channels` and `SELECT (status)` on `tg_backfill_state`. It already reads `tg_id`, `status`, `is_chat` and `channel_id`, `failure_kind`. A test holds it to those columns: no username, title, kind or note, and no cursor or failure detail.

The alert asked of the infrastructure repository fires on `itgraph_telegram_oldest_awaiting_history_seconds > 86400`. A day covers a night of quiet hours and the hourly retries. What it catches is a seed the session cannot walk — one never resolved, which is the operator's cue to resolve it until change 2 does — or a loop that has stopped walking.

### 7. YouTube's walk at half past every hour

`itgraph_yt_backfill_schedule` becomes `30 * * * *`. Its starting deadline goes from 82,800 s to 3,300 s, so a late start never meets the next hour's run, and its active deadline stays 3,000 s. `concurrencyPolicy: Forbid` stays.

The old 11:30 put a large walk early in a fresh quota day. A walk now is a seed or two at about 9 units each. A round of a hundred is about 900 units, which the general pool's floor of 2,000 already protects whatever the hour.

## Risks / Trade-offs

- **[The walk cannot be tried in a test environment]** → a test environment does not collect, and its seeds come complete from the restored copy. Mitigation: the step and the loop's handling are tested against the fake client, and `itgraph backfill`'s suite covers the split. In production the first seed marked after the pin is watched through the series and `itgraph channels --backfill`.
- **[A page holds up the loop]** → a page is one request after a 4-second pace, and is taken only when nothing is due. A poll that falls due during it waits seconds, inside the schedule's tolerance.
- **[A seed the session cannot walk waits for ever]** → by design, until something resolves it. The series and the day's alert make it visible, and change 2 resolves approved candidates.
- **[A restart retries every waiting seed at once]** → at most one request per waiting seed, and none for one the cache does not know.
- **[YouTube's CronJob runs 24 times a day]** → 23 of them with nothing due take seconds and no unit.

## Migration Plan

1. Release, then deploy to the test environment. The revision grants the exporter its two columns, the YouTube CronJob is hourly where a key is configured, and both new series read 0 on the restored copy.
2. Deploy to production. Nothing is walked at first, since every seed is complete.
3. The operator marks a seed (`itgraph mark … --seed`). The walk follows within minutes outside quiet hours: `itgraph channels --backfill` shows it complete, the series return to 0, and its edges appear after the next `derive`.
4. Ask the infrastructure repository for the alert and the panel of Decision 6.

Rollback: revert the pin. The revision's downgrade revokes the two columns. Rows the walk stored are history like any other.
