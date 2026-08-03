## Why

The realtime goal — what is being reposted, viewed, reacted to or argued about *right now* — rests on a measurement the project cannot currently take. Every engagement number in the database is a single snapshot, read once when the backfill happened to walk that channel, on a post of arbitrary age. `raw_messages` is immutable by construction and the first fetch of a message id wins, so the view count stored for a post is whatever it was at that moment and can never be updated. That is the right design for a message body and the wrong shape for a counter, which is exactly why `docs/PLAN.md` calls for `message_metrics` as a separate table on a separate cadence.

Without a time series there is no spike detection at all. "Anomalously high" is meaningless as an absolute number — a 500k channel and a 3k channel are not comparable — and `notebooks/anomalous_posts.py` already spends most of its length working around the single-snapshot problem: it throws away the last month of history, restricts itself to posts mature at read time, and leans on ratios precisely because a ratio is the one measure that survives not knowing a post's age. It says so plainly. Snapshots make that workaround unnecessary going forward, which is a second, permanent payoff independent of any alert.

Doing this first, and alone, is a scheduling decision rather than a preference. The scoring this feeds needs baselines of the form "what this channel's posts normally have at age t", and those cannot be computed, borrowed or backfilled — they accumulate in wall-clock time. Every week this is not running is a week the alert phases cannot start from. Meanwhile the alerting itself has no such constraint: it is a pure pass over snapshots and can be written, tuned and replayed at any point afterwards. So the collection goes now and the judgement goes later, which also keeps the project's core split intact — the collector writes and does nothing else.

The measurements say this is affordable. Across 565 seed channels the inventory sees about 576 posts a day from roughly 165 distinct channels; the median channel posts 0.53 times a day, 210 channels post less than once in two days, and only 15 post five or more times daily. Polling every channel on one 15-minute schedule would be 54,000 `messages.getHistory` calls a day to observe 576 posts — 96 requests per post. A schedule derived from each channel's own posting rate and the age of its live posts lands near 3,000–4,000 requests a day, roughly one request every 20–25 seconds, which is gentler than the pacing the completed backfill already ran at.

## What Changes

- A new **`message_metrics`** table: append-only observations of `views`, `forwards`, reactions and comment count for one message at one moment. Append-only, never updated — it is a raw layer for counters, and the same rule applies to it as to `raw_messages`: nothing derived is stored here, and everything derived must stay re-computable from it.
- A new **`itgraph watch`** command: a long-running loop that polls in-scope channels, stores any new messages into `raw_messages`, and writes a metric snapshot per observed message. It derives nothing, scores nothing and sends nothing.
- **One request serves both jobs.** `messages.getHistory` returns each message with its counters as of the response, so a single window of a channel's recent history answers "are there new posts" and "what do the live posts look like now" together. Cost is therefore per channel per cycle, not per post, and a channel with four live posts refreshes all four for one request.
- A **per-channel poll schedule** in a new `poll_state` table, holding a `due_at` the worker reads and advances. The interval comes from the channel's own posting rate — computable from `raw_messages` without spending anything — and from the age of its youngest post, so a post is sampled densely while it is moving and not at all once it has settled. Channels that have gone quiet fall back to a long interval rather than dropping out.
- **`newest_fetched_id` finally gets read.** `BackfillState` has recorded it since the backfill change, documented as the high-water mark "that incremental collection will read". This is that reader; no per-channel table has to be migrated to add it.
- **A daemon's rate-limit policy differs from a batch job's, deliberately.** `FloodWaitTooLong` stops a backfill because a run with no deadline gains nothing by holding a connection open. A watcher must not exit on the same event: it pushes every `due_at` out past the wait and carries on. The distinction is stated in the spec rather than left to the implementation, because the two policies read as a contradiction otherwise.
- **The session file gets an exclusive lease.** This is the project's first always-on process, and until now every networked command was a one-shot that could assume it alone held the Telethon session. Two processes on one session file is corruption and forced re-auth, not a race worth losing to. Every networked command acquires the lease and refuses, with a message naming the holder, rather than starting alongside the watcher.
- **`CollectionCommand.WATCH`** is added, so a rate limit incurred by the loop files under the loop. The existing guarantee that a history walk spends no quota-bearing request applies here unchanged and gains a way to be checked: a `ResolveUsernameRequest` recorded under `WATCH` is the same regression it is under `BACKFILL`. The watcher takes its peer from the session cache through the existing `cached_peer`, never `get_entity`.
- **Waking from suspend does not fire a burst.** A queue keyed on `due_at` comes back from a closed laptop with every channel overdue at once, which is the one way this design can produce exactly the traffic shape it exists to avoid. Catch-up is clamped.
- **Quiet hours.** Polling drops off or stops overnight. Real accounts sleep, it removes a large share of the daily request count, and nothing in this corpus needs minute-level latency at 04:00.

Out of scope, deliberately:

- **All scoring and alerting.** No baselines, no z-scores, no thresholds, no `alerts` table, no aiogram, no bot. Those are separate changes reading these rows, and keeping them out is what will later allow a week of stored snapshots to be replayed against a candidate threshold instead of tuned one day at a time.
- **Incremental edge derivation**, and with it live repost-cascade detection. The watcher stores raw messages; `itgraph derive` turns them into edges as it already does. Making derivation incremental is the next change and is the one the cascade alert waits on.
- **Comments.** `replies.replies` is a counter on the post and is captured as one; reading the discussion thread itself remains the separate heavy phase it has always been.
- **Retention and thinning.** About 576 posts a day at roughly ten snapshots each is 5,800 rows a day, near 2.1M a year — small enough that a policy now would be a guess dressed as a safeguard. The arithmetic is recorded here so the question can be reopened with a number rather than a worry.
- **Edits and deletions.** A post whose text changes keeps its first-fetched payload, unchanged from today. A post that vanishes becomes visible in the snapshots as a series that stops, and nothing in this change interprets that.

One question is left open for `design.md`. `backfill_max_messages` caps how much any one channel may contribute, so that a handful of aggregators do not become most of the corpus, and a capped channel is currently closed for good. The recommendation is that the ceiling bounds *history* — how far back it is worth paying to walk — and not forward collection, because a channel at its ceiling is one of the 15 most active in the inventory and blinding the realtime product to exactly those is the wrong trade. The counter-argument, that the corpus then grows without bound, is real and the resolution belongs in design.

## Capabilities

### New Capabilities

- `metric-snapshots`: the engagement time series and the polling loop that produces it. Covers what a snapshot is and that it is never rewritten, which channels are polled and how often, that one request serves both new-post detection and metric refresh, the loop's rate-limit and catch-up policy, quiet hours, and the guarantee that the loop derives nothing and spends no quota-bearing request.

### Modified Capabilities

- `channel-inventory`: **Telegram Session Authentication** currently describes a session that is present and authorized, which was sufficient while every command was a one-shot. It gains exclusivity — the session is held by at most one process, a command that cannot take the lease refuses and says who holds it, and a lease does not outlive the process that took it.
- `message-backfill`: **Rate Limit Events Are Recorded** has a scenario asserting that two commands are told apart in `flood_events`. A third command exists now, and the scenario has to say so; the point of the column is that a method appearing under the wrong command is a detectable regression, and that only works if every command that can incur a limit is representable.

## Impact

- `src/itgraph/tg/watch.py` — new: the poll loop. Spends `messages.getHistory` and nothing else; reuses `cached_peer`, `pace`, `FloodRecorder` and `waiting_out_floods`, with its own halt policy.
- `src/itgraph/db/metrics.py` — new: snapshot writes. Nothing here reads a payload, matching `db/raw.py`.
- `src/itgraph/derive/metrics.py` — new: payload → the four counters, a pure function, alongside `derive/references.py`. The reactions-array handling that `notebooks/anomalous_posts.py` works out in SQL belongs here once and is tested here.
- `src/itgraph/db/poll.py` — new: what is due, and rescheduling it.
- `src/itgraph/db/session_lease.py` — new: the exclusive lease, as a Postgres advisory lock. No file, no PID, so a killed process leaves nothing to clean up.
- `src/itgraph/db/models.py` — `MessageMetric`, `PollState`, `CollectionCommand.WATCH`.
- `src/itgraph/cli.py` — `itgraph watch`; every existing networked command takes the lease.
- `src/itgraph/config.py` — poll cadence bounds, snapshot schedule, quiet hours, catch-up clamp. Conservative defaults, as with the backfill pacing.
- One Alembic migration: two tables, one enum value. `raw_messages`, `edges` and the inventory are untouched, and nothing already stored changes shape.
- `tests/` — `test_watch.py`, `test_metrics.py`, `test_poll.py`, `test_session_lease.py`; `test_cli.py` grows the lease cases. The mocked client must assert that a poll issues no `resolveUsername` and no `getFullChannel`, which is the invariant this loop is most likely to break by accident.
- `src/itgraph/CLAUDE.md` — four rows in the module table.
- `docs/PLAN.md` — the alert-bot row of the plan splits into collection and judgement; worth a line once this settles. The `arq` suggestion there is not taken, and the reason belongs in design: it requires Redis against a Postgres-only rule, to schedule one worker.
- No new runtime dependency. `aiogram` arrives with the bot, in its own dependency group, in a later change.
