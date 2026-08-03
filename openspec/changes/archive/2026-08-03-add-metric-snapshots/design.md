## Context

Everything the collector has done so far is a batch job: a command starts, walks a bounded amount of work, and exits. `itgraph watch` is the first process meant to stay up, and almost every difficulty in this change comes from that one difference rather than from the measurement itself.

Three constraints shape it. The account is real and losing it costs more than any amount of data, so the loop must be quieter than the backfill that already ran, not louder. The session file admits one process, which was free to ignore while every command was a one-shot. And the machine is a laptop that sleeps, so the loop is interrupted constantly, at arbitrary points, for arbitrary durations — an operating condition the batch commands only ever met as a crash.

What is being measured is small. Across 565 seed channels the inventory sees ~576 posts a day from ~165 distinct channels, and the distribution is severely skewed:

```
  posts/day     channels     what they need
  ───────────────────────────────────────────────────────────────
  ≥ 5              15        near-continuous attention
  0.5 – 5         229        attention while a post is young
  < 0.5           210        a check every few hours, no more
  silent 30d      111        a check, rarely
```

A single 15-minute schedule over all of them is 54,000 requests a day to observe 576 posts. The schedule is therefore the design.

## Goals / Non-Goals

**Goals:**

- A per-message engagement time series, dense while a post is moving and absent once it has settled.
- New posts enter `raw_messages` continuously, so the graph stops going stale between backfills.
- A request budget in the low thousands per day, sustained, with no burst larger than the backfill's.
- Survive suspend, network loss and process death without a corrupt cursor or a catch-up stampede.
- Leave every judgement — what counts as a spike — to a later pass over these rows.

**Non-Goals:**

- Scoring, thresholds, alerts, the bot. Named here only to be excluded.
- Sub-minute latency. The fastest useful reaction to a spiking post is measured in tens of minutes.
- Complete coverage of every post's early curve. Quiet hours and suspend will both cost first snapshots, and the design absorbs that rather than fighting it.

## Decisions

### One request per channel per poll, covering both jobs

`messages.getHistory` returns each message with its counters as of the response. So one window of a channel's recent history answers "is there anything new" and "what do the live posts look like now" at once, and the cost of watching a channel is per poll, not per live post.

The window size is derived, not fixed: `posts_per_day × horizon_days`, clamped to `[10, 100]`. At the median channel this is the floor of 10; at the most active channel in the inventory (28.9/day over a 2-day horizon ≈ 58) it is still one request. The clamp's upper end is the point: **in practice every channel is covered by a single `getHistory` call**, and a channel that ever needs two is a channel posting more than 50 times a day, which this inventory does not contain. Fetching a fixed 100 every time would cost the same in requests and more in bytes, for no gain.

The walk stops early at the first message older than the tracking horizon and already stored — there is nothing to learn from re-reading a settled post.

### The snapshot schedule decays, and the channel's `due_at` is the minimum over its live posts

Per post, measured from its publication:

```
  +15m  +30m   +1h    +2h      +4h        +8h          +24h            +48h
   │     │      │      │        │          │             │               │
   ●─────●──────●──────●────────●──────────●─────────────●───────────────●   stop
   └── the early curve, where a spike is                 └── the slow half:
       distinguishable from a normal post                    forwards, comments
```

Nine samples over two days. The early density is where views and reactions separate a spike from an ordinary post; the tail is there because forwards and comments accrue far more slowly, and forwards are the signal `docs/PLAN.md` calls the most valuable.

A channel's `due_at` is the earliest next sample over its live posts, floored by a minimum gap so a channel publishing an album or a burst does not get polled three times in a minute. With a median of 0.53 posts a day, most active channels carry exactly one live post and the channel's schedule simply *is* that post's schedule. Channels with nothing live fall back to an idle interval derived from their own posting rate, clamped to `[30m, 12h]` — frequent enough that a post is discovered while it is still young, rare enough that 210 near-silent channels cost almost nothing.

Rough arithmetic: ~165 channels a day carrying live posts at ~10–12 polls each, plus ~400 idle channels at 2–6 checks, lands near 3,000–4,000 requests a day. One request every 20–25 seconds, against a backfill that ran at one every 4.

### Overdue work is skipped, not caught up

This is the decision that makes suspend survivable, and it is the opposite of what a job queue normally does.

A single worker polling sequentially behind `pace()` cannot burst — that much is already true. But a laptop that wakes after eight hours has 565 overdue channels, and grinding through all of them back to back is a sustained elevated rate for hours, which is the shape this design exists to avoid. Worse, it would be spending requests on samples that are now worthless: a snapshot due at post-age 30 minutes, taken at post-age eight hours, is not a late sample of the early curve. It is a different measurement wearing its name.

So a missed sample is dropped. On each poll the schedule is recomputed from the post's *current* age and the next unelapsed slot is taken; the elapsed ones are gone. A post that slept through its first four samples gets its +8h sample at +8h, and its early curve is simply missing. The alternative — replaying the backlog — buys nothing and costs exactly the traffic pattern that endangers the account.

The consequence propagates: **scoring must read `observed_at − published_at` and never assume the intended slot.** Snapshots are irregular by design, so the age is a fact about each row rather than a property of the schedule. That is stated here because it is the sort of assumption a later pass makes silently.

### `message_metrics` is a raw layer for counters

```
  message_metrics
    channel_id   bigint   ─┐
    msg_id       bigint   ─┴── FK → raw_messages (channel_id, msg_id)
    observed_at  timestamptz
    views        int  null
    forwards     int  null
    reactions    jsonb null
    comments     int  null

    PK (channel_id, msg_id, observed_at)
    ix (observed_at)
```

Append-only, never updated. The same discipline as `raw_messages`: the collector writes observations and nothing else derives from them at write time.

**Reactions stay per-emoji, and no total is stored.** Storing the sum would put a derived measure in an observation table — the trade `Edge` already refuses when it declines to store the interval between two dates it carries. The per-emoji breakdown is also a real signal rather than storage for its own sake: a post accumulating 🤡 and one accumulating ❤️ are opposite events that a sum reports identically, and separating approval from derision is exactly the kind of question this table exists to make answerable later. If summing over jsonb becomes the alert pass's bottleneck, the answer is a materialized view, not a denormalized column.

**NULL is not zero.** A channel with reactions switched off publishes no reactions object, which is a different fact from a post nobody reacted to, and `notebooks/anomalous_posts.py` already has to reconstruct that distinction per channel because the current data loses it. Here it is preserved at the source.

**The foreign key onto `raw_messages` is load-bearing.** It makes a snapshot of a message the raw layer does not hold impossible, which pins the write order: payload first, snapshot second, one transaction. The composite key it references is that table's primary key, so it costs no new index.

### Scheduling lives in `poll_state`; the cursor stays in `backfill_state`

`BackfillState.newest_fetched_id` has been recorded since the backfill change and documented as the high-water mark "that incremental collection will read". This is that reader, and it reads it in place rather than copying it. Position and timing are then owned by exactly one table each.

```
  poll_state
    channel_id        bigint   PK, FK → channels
    due_at            timestamptz   ix
    last_polled_at    timestamptz null
    posts_per_day     float    null   -- cached estimate, recomputed periodically
    consecutive_empty int             -- backs off a channel that has gone quiet
    last_error        text     null
```

Merging this into `backfill_state` was considered and rejected: that table's `status` column has terminal values — `complete`, and a capped channel that is finished for good — and polling has no terminal state at all. One column meaning both "this walk is over" and "this channel is checked forever" is where the confusion would start.

### The `backfill_max_messages` ceiling bounds history, not forward collection

The open question from the proposal, resolved as recommended there; say so if you disagree, because it is a judgement about corpus composition rather than a technical constraint.

The ceiling exists so a few aggregators do not become most of the corpus, and it answers the question "how far back is it worth paying to walk this channel". Forward collection asks nothing of the sort: the request is already being spent to refresh metrics, storing the message it returned is free, and the channels that reach the ceiling first are the 15 most active in the inventory — precisely the ones a realtime product cannot afford to be blind to.

The counter-argument is unbounded growth, and it deserves a number rather than a worry: 576 posts a day is ~210k a year against ~208k currently stored. The corpus roughly doubles in a year, dominated by the same handful of channels. That is affordable, and it is measurable, so the question can be reopened against data. What does *not* change is the backward walk — a capped channel stays capped, and no `--since` reopens it.

### The session lease is a Postgres advisory lock

`pg_try_advisory_lock` on a constant key, held on a dedicated connection for the process's lifetime, taken by every networked command.

Non-blocking, so a second command refuses immediately and names the holder instead of hanging on a lock nobody expects. Session-scoped, so a killed process releases it when its connection dies — there is no stale PID file and nothing to clean up after an unclean exit, which is the failure mode a lockfile is famous for and the reason it is not used here. `flock` would also survive a kill, but it is local to the filesystem, and the alert bot may later run somewhere else while this stays put; a lock in the database is the one that keeps meaning something when the processes are not on the same machine.

The dedicated connection is not optional and not from the pool: an advisory lock belongs to a session, and a pooled connection returned between statements takes the lock with it. If that connection drops, the lease is silently gone — so its loss is treated as fatal, the watcher exits, and the supervisor restarts it into a clean acquisition. Reconnecting and assuming the lease survived is the one behaviour that could put two writers on one session file.

### A daemon backs off where a batch job halts

`FloodWaitTooLong` stops a backfill, and that is correct for work with no deadline: holding a connection open for hours buys nothing, and a wait that long is the shape of a daily quota, which waiting does not answer. A watcher cannot exit on the same event — the product is that it is running.

`waiting_out_floods` is reused unchanged, including its halt. The watcher catches `FloodWaitTooLong` at the loop level and converts it: every `due_at` moves past the reported wait, the loop sleeps, and collection resumes. Nothing is recorded against the channel that happened to be in flight, exactly as the backfill treats it. The distinction is that a halt propagates out of a batch run and is absorbed by a loop — one policy, two callers, rather than a second flood handler to keep in sync with the first.

Short waits are unchanged: Telethon sleeps through anything under `flood_sleep_threshold`, and the recorder files the rest under the new `CollectionCommand.WATCH`.

### No `arq`

`docs/PLAN.md` suggests "a simple Postgres-backed queue plus one worker (arq)". The queue is taken; the runner is not. `arq` is a Redis job queue, and adding Redis contradicts the standing Postgres-only rule to schedule a single sequential worker whose entire dispatch logic is one indexed `due_at` query. The loop is an `asyncio` `while True` over that query. If the work ever becomes concurrent or distributed the decision is worth revisiting, but concurrency is the one thing this design must not have — Telegram's limits are per account, so parallel workers reach the same ceiling faster and look worse doing it.

### Quiet hours are accepted losses, not a gap to compensate

Polling stops overnight on a configurable local window. Real accounts sleep, it removes a large share of the daily request count, and nothing in this corpus needs minute-level latency at 04:00.

A post published at 03:00 gets its first snapshot at wake, aged four hours, and its early curve does not exist. No attempt is made to make up for it — see the skip-don't-catch-up decision, of which this is the same argument on a schedule instead of an accident. Such posts are simply less scoreable on the early signals and remain fully scoreable on the late ones.

## Risks / Trade-offs

**Sustained polling is a different risk profile from a bounded backfill, and no measurement here can prove it safe.** → Total volume lands below the backfill's request rate, the loop spends no quota-bearing method, concurrency is structurally absent, and quiet hours mean the pattern has a daily rhythm rather than being flat around the clock. The honest position is the same one `tg/pacing.py` already states about jitter: this is defensible caution against a mechanism nobody outside Telegram can confirm.

**A cold entity cache silently reduces coverage.** A channel the session file has never seen is skipped by `cached_peer`, and in a loop that skip repeats forever without anyone noticing. → The run reports skipped channels as a count, not a log line, so a session file that lost its cache is visible rather than inferred.

**Reconstructing the posting rate on every tick would cost more in queries than the polling costs in requests.** → `posts_per_day` is cached on `poll_state` and recomputed on a slow cadence. It is an input to a schedule, not a measurement anyone reads.

**The early curve will have holes** — from suspend, quiet hours, and floods. → Accepted, and made safe by making age a per-row fact. The risk is not the holes; it is a later pass assuming they are not there, which is why the `observed_at − published_at` rule is written down at design time rather than discovered during scoring.

**Two tables and a daemon exist for weeks before anything reads them.** → That is the schedule this change was separated to protect: baselines accrue in wall-clock time and nothing can shorten it. The interim payoff is real but quiet — the offline analytics stop being restricted to posts mature at read time.

## Migration Plan

One Alembic migration: `message_metrics`, `poll_state`, and the `WATCH` value on the `collection_command` enum. No existing table changes shape and no stored row is rewritten, so the downgrade is a clean drop of two tables — and, as ever, it is exercised against a scratch database, never the working one.

`poll_state` starts empty and is populated lazily: a channel with no row is due immediately, so the first pass over the inventory is the seeding pass. It is also the largest burst this design ever produces, and it is bounded by the same sequential pacing as every other pass — a first run is simply a long one.

Rollback is stopping the process. Nothing else depends on these tables yet, which is the whole reason this change is first.

## Open Questions

- **Where the process is supervised.** `launchd` matches the existing backup jobs and is the obvious answer while collection stays on this machine; it is left out of this change because it is deployment, not behaviour, and the answer changes if the always-on machine does.
- **Whether the idle interval should track time of day** beyond the quiet-hours switch. IT-media posting is concentrated in working hours, so a channel checked every 6 hours is checked twice into an empty night. Worth measuring from the snapshots this change produces, rather than guessing now.
