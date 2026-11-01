## Context

Third YouTube change. It inherits the conventions of changes 1 and 2 unchanged:
- one `httpx` client;
- every request booked in the ledger before it is sent;
- API data only in `yt_raw_*`;
- expiry first in every `yt` command through `_run_yt`;
- ids only in logs;
- `scripts/on-host.sh` for every networked run.

Motivation is in `proposal.md`; requirements are in the four specs.

**Reused:**
- `yt/client.py`, and the ledger's `POOL_OF`;
- `yt/videos.by_ids`, with its parts and fields made a parameter;
- `yt_schedule.horizon` and `next_check_of_upcoming`;
- `derive/yt_videos.facts_of` and `counters_of`;
- the advisory-lock pattern of `db/yt_watch.watch_lock`;
- `db/backup.EXCLUDED_DATA`.

**Not carried over from Telegram's `itgraph backfill`:**
- **The persistent `offset_id` cursor.** A page token's lifetime is undocumented.
- **Request pacing and FloodWait handling.** The budget is quota, not rate.
- **A fixed `--cutoff` date.** Retention makes the window roll.
- **A ceiling counted across runs.** Rows expire, so a running total means nothing.

**The application.** Its text is not kept. What it promised is in `docs/PLAN.md` ("Filed on 2026-10-25"), `docs/index.html` and `docs/privacy.html`, and those are the requirements here. On 2026-10-26 YouTube said the application is under review.

### Facts checked against the documentation, 2026-11-01

| Fact | Source |
|---|---|
| `playlistItems.list`: 1 unit; `maxResults` 0–50; `pageToken` / `nextPageToken`. Nothing on order, depth, a maximum of items, or how deleted, private, members-only or scheduled videos appear. Errors 404 `playlistNotFound`, 403 `playlistItemsNotAccessible` | developers.google.com/youtube/v3/docs/playlistItems/list |
| "YouTube adds all uploaded videos to a playlist associated with the channel"; the official samples page until `nextPageToken` is absent | …/youtube/data/sample_requests; …/code_samples |
| `snippet.position` is "The order in which the item appears in the playlist"; what sets it in an uploads playlist is not said | …/docs/playlistItems |
| `videos.batchGetStats`: parts `id`, `snippet` (`publishTime` only), `statistics` (views, likes, comments), `contentDetails` (`duration`, `durationMillis`), plus `summary.failedVideoIds` | …/docs/videos/batchGetStats |
| `videos.batchGetStats`: no id maximum, no `fields` parameter listed, "does not define any error messages that are unique"; "Authentication is required … against non-public videos, but is not required for public videos"; 1 unit from its own bucket of 10,000 a day | same; revision history 2026-06-03 |
| III.E.4.b counts "the number of videos in a playlist" among statistics; "an API Client must not store statistics retrieved as Non-Authorized Data for more than 30 days" | developers.google.com/youtube/terms/developer-policies (updated 2026-10-15) |
| III.E.4.d: "temporarily store limited amounts of Non-Authorized Data for as long as is necessary for the purposes of the API Client but not longer than 30 calendar days … delete or refresh". The phrase "limited amounts" occurs nowhere else and is not defined | same |
| III.E.4.e: "reflect metadata changes and viewcount updates as quickly as possible" | same |
| III.E.4.h: "must not (i) replace API Data with similar, independently calculated data, or (ii) access or use API Data to create new or derived data or metrics" | same |
| III.D.3: "If your API Client's use case changes, to use any allocated quota for the new use case, you must notify YouTube … by resubmitting an API Compliance Audit" | same |
| The amendment: metrics "from statistical endpoints for up to 36 calendar months"; "video titles, creator names, descriptions, and comment text … must still follow the 30-day refresh and deletion policy" | …/terms/derived-metrics-policy (updated 2026-09-14) |
| Since change 2's check, nothing touches what itgraph reads: on 2026-10-15 a `madeForKids` clarification in `videos.list`. The `brandPartner` part of 2026-07-07 is a documented link from a video to a brand partner's `channelId` | …/v3/revision_history |

### Measured, 2026-10-26 to 2026-11-01, 105 seeds

| Figure | Value |
|---|---|
| Upload rate U | 0.22 a channel a day: 639 publications in 28 days. 0.28 from the loop's new videos a day, 26–30 October. PLAN assumed 0.5–1.5 |
| Publications in 28 days per seed | median 2, p90 18, maximum 45; 37 seeds published nothing |
| `videoCount` | median 456, p90 1,914, maximum 8,159; 27 seeds above 1,000; 80,931 in all |
| Poll loop spending | about 490 units a day: `channels.list` 288 (three calls a pass above 100 seeds), `playlistItems.list` about 113, `videos.list` about 91 |
| General pool spending | about 1 unit a day (`refresh`); 119 on the import day |
| Order of the newest page | the 8,159-video channel had 28 videos from the last 28 days on it, against about 0.17 for a random page of 50 |
| Payload size | 1,280 bytes on average (p50 1,083, p99 3,368); descriptions p50 693, p99 4,384 characters |
| `videos.list` with 50 ids | the first pass on the server read 655 candidates in 50-id batches with no `not_found` and no error |
| Counters | none absent in 677 readings; 5 zero like counts, 63 zero comment counts |
| The move | restored from a full dump on 2026-11-01 at about 02:00 Moscow. `yt_raw_*` came back empty and `yt_channel_polls` did not. Every seed's first listing on the server was therefore recorded as `count` (655 videos), and the per-path history from before the move is lost |

## Goals / Non-Goals

**Goals:**
- A year of every seed's uploads, available on the day change 4 can read it, and kept current from then on without walking again.
- A mature reading of every past upload from the day acceptance is recorded, with no migration on that day.
- Nothing kept longer than a reader needs it: no counters before acceptance, no year of descriptions before change 4.
- The undocumented parts of the API measured by the walk itself.

**Non-Goals:**
- Parsing anything out of descriptions: change 4.
- Fixing the poll loop's labelling after a restore. It is its own fix.
- `videos.batchGetStats`.
- Choosing the maturity window. Change 7 measures it once acceptance allows; this change only makes the depth reach past every plausible candidate.

## Decisions

### Payloads now, kept fresh from change 4 on, counters from acceptance

Decided by the operator on 2026-11-01. Edges and pending mentions may be created under the base rules, and live no longer than the payload they were parsed from. That is what `youtube-data-retention`'s "API Data Has One Home" already demands of anything worked out from a video's data. So a year-deep graph exists only while a year of payloads is kept fresh.

The four options priced in the explore, for 100 and 500 channels:

| Option | Changes 4–5 | Change 7 | Cost |
|---|---|---|---|
| a year with counters, kept fresh | a year | a reading of every past upload on acceptance day | 430 / 2,140 once; 6–12 / 30–60 a day |
| walk and let it expire | a graph that is gone in 30 days | a second walk | 430 / 2,140 per walk |
| **payloads only, kept fresh, counters from acceptance** | a year | as the first | as the first |
| defer | the loop's 29 days, a twelfth of a year | a walk on acceptance day | 0 now |

The chosen row costs the same as the first. It keeps no counter that nothing may use — statistics are not scored before acceptance. It also avoids leaving a 25-day-old count in a payload, which III.E.4.e would question.

**When it starts.** Before change 4 there is no reader, and holding a year of descriptions for weeks with nothing reading them is hard to call "necessary for the purposes" (III.E.4.d). Walking again costs 430 units, under 8% of the general pool. So unattended walking and the refresh of past uploads are switched off until change 4 lands, or until acceptance if that comes first.

**The switch is `youtube_backfill_enabled`**, a setting written from the deploy variable of the same name, off by default. It gates three things:
- whether the CronJob exists;
- whether `yt refresh` keeps past uploads;
- whether a walk record outlives 30 days.

A walk started by hand works either way. With the switch off, its data expires 30 days after it was fetched, and that is what the verification run needs.

It is a setting, not a constant like `DERIVED_METRICS_ACCEPTED_ON`. It records an operational state rather than a policy fact, and the CronJob it governs is the deploy's anyway.

### A year, rolling, counted from each channel's walk

On "limited amounts", decided on 2026-11-01:
- 365 days as `youtube_backfill_days`, revisited at 500 channels;
- the fields already trimmed to `docs/privacy.html`'s list;
- no counters;
- deleted when the channel leaves scope.

A setting refuses a depth that does not lie past the horizon.

The depth counts back from the moment each channel's walk begins, and a channel is walked once. The poll loop feeds the year at the front. The refresh keeps past uploads younger than the depth and stops at the back, where they expire. Nothing has to walk again.

### A page at a time, stopping where the listing crosses the depth

```
 playlistItems.list(maxResults=50, pageToken) ── 1 unit
   items: videoId, videoPublishedAt? · nextPageToken
     ├─ dated inside the horizon, or stored already ────► skip (the poll loop's)
     ├─ dated before the depth ─────────────────────────► skip; this page is the last
     └─ dated inside the depth, or undated ─────────────► ask
                                │
 videos.list(≤ 50 ids, the loop's fields less statistics) ── 1 unit
     ├─ not in the answer ─────────► unreturned += 1
     ├─ broadcast yet to start ────► store, due at its scheduled start (the loop's)
     ├─ published inside the depth, past the horizon ─► store, due_at NULL, via backfill
     └─ anything else ─────────────► drop
 commit: the videos and the walk record's counters, together
```

**Why stop on the crossing page.** A page is paid for whole, so finishing it costs nothing. Whole playlists would cost 1,790 / 8,930 units against 430 / 2,140, mostly for the 27 seeds with over a thousand uploads.

**The order is counted, not trusted.** The walk keeps the earliest date seen so far, and an item dated later than it adds one to `out_of_order`. The newest page is newest-first by measurement (see the table above); the pages after it are unverified.

The first run walks five small seeds `--whole` — to the end of the playlist, storing only what the depth admits. That checks the order end to end for about 25 units. If items turn out out of order, the remedy is whole walks bounded by the ceiling, and no table changes.

**Undated items** are asked about, as the poll loop asks about them: one may be a broadcast yet to start.

**`fields`:**
- the listing gains `nextPageToken`;
- it does not ask for `pageInfo/totalResults`, which III.E.4.b counts as a statistic ("the number of videos in a playlist");
- `yt/videos.py` gains a second parts/fields pair, the loop's less `statistics`, used for past uploads until acceptance and replaced by the loop's own after it.

Rejected:
- trusting the order and stopping at the first older item — no saving once a page is paid for;
- whole playlists always;
- `search.list` with `publishedBefore` — it spends discovery's 100 calls.

### Resuming without page tokens

Each page and the `videos.list` after it are committed together. A walk that stops leaves `finished_at` NULL, and the next run walks that channel again from its newest page, counters reset. Videos already stored are not asked for again, so the waste is listing pages of one channel: at most 12 at the measured maximum of 587 a year.

Rejected:
- **Keeping `nextPageToken`.** Its lifetime is undocumented, and a stale token fails loudly at best and skips silently at worst.
- **Keeping the oldest date reached**, as Telegram does. It is a video's publication date, and would be API data outside its one home. A capped channel's reach is read from its stored videos while they last.

### A ceiling of 1,000 videos per walk

The measured maximum is 587 a year, 1,174 at twice the rate. The ceiling binds nothing today and caps a future high-volume seed at about 40 units.

It counts per walk, not per channel across runs as on Telegram. Rows expire, so a running total would count videos no longer there. Zero lifts it.

### Broadcasts yet to start are handed to the poll loop

Change 2 accepted missing a broadcast yet to start that the listing dates before the horizon. The walk asks about such items anyway. Storing one with `due_at` from `next_check_of_upcoming` makes it the loop's, which checks it at its start and gives it up a week later, as it does its own. That costs no extra request.

It stays recorded as found by `backfill`. `watch-status` counts it among tracked videos, but not under any of the loop's paths.

### What the walk writes

- Its own `INSERT … ON CONFLICT DO NOTHING` into `yt_raw_videos`, never `store_videos`, which updates on conflict. A video the loop tracks keeps its payload, schedule and path.
- `discovered_at` is the fetch moment and `discovered_via` is `backfill`, a new value of `yt_discovery_path`.
- After acceptance, a reading from the same answer, through the same insert as the loop's readings.

### Past uploads kept fresh by the refresh

`yt refresh` gains a third selection after channels and after the accepted readings. While the switch is on, it takes every video meeting all of these:
- of a seed;
- `due_at` NULL;
- not a broadcast yet to start;
- published inside the depth;
- `fetched_at` older than the refresh age.

It covers the loop's finished videos and the walk's alike. The command stays `refresh` and the pool general.

Cost: 6–12 units a day for 100 channels, 30–60 for 500. A re-walk every 25 days would cost 17–30 and 86–150, so the refresh wins.

Before acceptance the request leaves out `statistics`, so a finished video's payload loses its last count at its first refresh — the latest count is what the policy calls replaceable. After acceptance every such fetch is a reading. That gives change 7 a measured curve past 28 days, a point every 25 days, at no extra cost.

The accepted-readings selection now also reaches past the depth. A reading kept 36 months needs its payload refreshed for as long, at about 19 units a day for 100 channels and 100–190 for 500 by the third year.

### The walk record, kept with what it describes

```sql
CREATE TABLE yt_backfill_walks (
    channel_id           text PRIMARY KEY REFERENCES yt_channels ON DELETE CASCADE,
    cutoff               timestamptz NOT NULL,   -- started_at − depth: ours
    started_at           timestamptz NOT NULL,
    finished_at          timestamptz,            -- NULL: did not end, walked again
    whole                boolean NOT NULL DEFAULT false,
    pages                integer NOT NULL DEFAULT 0,
    videos               integer NOT NULL DEFAULT 0,
    out_of_order         integer NOT NULL DEFAULT 0,
    undated              integer NOT NULL DEFAULT 0,
    unreturned           integer NOT NULL DEFAULT 0,
    capped               boolean NOT NULL DEFAULT false,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_error           text
);
```

- **Ours.** Times, a flag and counts of its own work and of what the API did. It holds no title, date or count YouTube returned. The counters are telemetry like `yt_watch_passes`'s — the operator's decision of 2026-10-26, extended here.
- **Left out of every dump**, by its name in `EXCLUDED_DATA`. The move is the reason: `yt_channel_polls` outlived the payloads it described and made every first listing look like a moved count. A walk record outliving its videos would claim a walk that is not there. After a restore every seed is walked again, for 430 / 2,140 units.
- **Forgotten:**
  - when the channel stops being a seed — in the review's transaction (`review-apply`, `mark`);
  - when it is marked gone — in `mark_gone`'s;
  - while the switch is off, once its walk ended more than 30 days ago — in `_run_yt`'s expiry, before anything else.
- **Due:** a seed not gone, with no record or with `finished_at` NULL. A failing channel is tried at the next run. A daily run needs no backoff.

Rejected:
- `yt_channel_polls`: the loop's, in dumps, with another lifecycle;
- a `yt_raw_` name: it holds no API data, and that prefix says it does.

### Spending: the general pool, a floor, a limit

- **`POOL_OF[BACKFILL] = GENERAL`.**
- **The floor**, `youtube_backfill_general_floor` (2,000). Before each request the walk compares the general pool's cap less spent less the request's cost with the floor, and stops below it. It is a courtesy on top of the cap, which stays the hard limit. Settings refuse a floor at or above the cap.
- **Measured:** the general pool spends about a unit a day. Change 5 will spend it on `forHandle` lookups and `channelSections.list`.
- **`--limit N`** counts requests, as `yt add`'s does.
- **Exits.** A stop at the floor, on a spent pool or on an exhausted day exits 0, saying when spending resumes, as the poll loop does. So does a busy lock. A `YtError` exits 1. A daily job that stops at the floor did its job, and a red one would teach the operator to ignore it. This differs from `yt refresh`, whose stop exits 1, deliberately.

With 4,000 units a day available:
- 100 channels take one run;
- 500 take one day, at twice the rate too;
- whole playlists for 500 would take three.

### One walk at a time, beside the poll loop

`pg_try_advisory_lock(bigint)` on a `NullPool` connection, with a constant key of its own, unequal to `WATCH_LOCK_KEY`. Telegram's lease uses the two-integer form, so neither can block the other.

A lock shared with the poll loop was rejected. For the 5–20 minutes of a walk, every fifteen-minute pass would exit busy and lose the early readings, the loop's most valuable. The two never write the same video: the horizon splits them, the walk skips stored videos, and the ledger's booking is safe under concurrency.

### Commands

- `itgraph yt backfill [CHANNEL…] [--whole] [--limit N]`:
  - channels are named by id or handle, as for `mark`;
  - `--whole` is refused without named channels, before the database;
  - a named seed is walked even if walked already;
  - a named channel that is not a seed is refused.
- The run ends with one line: `complete: 37 of 37 channels walked, 412 pages, 7120 videos, 2 broadcasts handed to the poll loop; out of order 0, undated 3, not returned 5, capped 0`, with `, listing failures N` when there were any. A quiet stop heads it `stopped:` and adds the reason on the next line — at the floor, `the history walk leaves 2000 units of the general YouTube quota pool to the other commands, and no more than that is left today; the next quota day begins 2026-11-02 08:00 UTC (11:00 MSK here)`. A failure heads it `failed:` and puts what YouTube said on standard error.
- `itgraph yt backfill-status`, as the spec lists. It reads the walk records, `yt_raw_videos` by path and the ledger.

### The schedule

| Field | Value | Why |
|---|---|---|
| CronJob | `itgraph-yt-backfill` | — |
| `schedule` | `30 11 * * *`, Moscow, as `itgraph_yt_backfill_schedule` | the Pacific midnight is 10:00 in summer and 11:00 in winter, so 11:30 is always early in a fresh quota day |
| `startingDeadlineSeconds` | 82800 | a run missed while the node was down starts the same day, as the refresh's does |
| `activeDeadlineSeconds` | 3000 | a 500-channel walk is 11–21 minutes, estimated |
| `concurrencyPolicy` | `Forbid` | — |
| `backoffLimit` | 0 | — |

The CronJob exists only with the key and the switch; the playbook deletes it otherwise, as it deletes the other two. `tests/test_deploy_manifests.py` holds the row.

### Settings

| Setting | Default | Check |
|---|---|---|
| `youtube_backfill_enabled` | `False` | — |
| `youtube_backfill_days` | 365 | past `youtube_watch_horizon_days` |
| `youtube_backfill_max_videos` | 1,000 | ≥ 0; 0 lifts it |
| `youtube_backfill_general_floor` | 2,000 | ≥ 0, below the general cap |

On the deploy side: `youtube_backfill_enabled` (false) writes `YOUTUBE_BACKFILL_ENABLED` into `itgraph-env`, and `itgraph_yt_backfill_schedule` sets the schedule.

### Not used: `videos.batchGetStats`

- **Before acceptance** no counter is wanted.
- **After acceptance** the refresh must call `videos.list` anyway, because a reading is removed with its video's payload. The counters come in the same unit.
- **No `channelId` and no `liveStreamingDetails`**, so a reassignment goes unseen and a stream's age cannot be counted from its start.

Adopting it later would touch:
- `docs/index.html` (methods);
- `docs/privacy.html` (`durationMillis`, the date);
- `docs/PLAN.md`;
- the ledger, which would need exhaustion per bucket and a pool chosen by method. Today `quotaExceeded` ends the day for every pool.

### No notice to YouTube

III.D.3 ties the notice to using "any allocated quota for the new use case". itgraph asked for no quota increase. The use case is the one the site states: analytics of these channels, and "finding channels to review". The methods are unchanged. Decided by the operator on 2026-11-01.

Change 2's design summarised III.D.3 without that condition; `docs/PLAN.md` records it in full.

### Documentation

The wording below is proposed. The pages are public statements the application points at, so the operator words them before archiving, not before implementing.

- **`docs/index.html`, "What it does with YouTube", after "Public statistics of new videos":**
  > **Past uploads.** For channels in scope, itgraph also lists up to a year of earlier uploads and reads their titles and descriptions, to see which other channels they mention or link to — to map how the channels refer to one another, and to find channels to review. It does not read their view, like or comment counts.
- **`docs/index.html`, Methods:**
  > `playlistItems.list` and `videos.list` for new and past uploads, and for the statistics of new ones.
- **`docs/privacy.html`, "How it is used and shared", the first item, extended:**
  > …and to see which other channels the videos mention or link to, so that those channels can be reviewed.
- **`docs/privacy.html`, "How long it is kept", the first item, extended:**
  > A channel's uploads of the past year are fetched again before their data is 25 days old while the channel is in scope; an upload more than a year old, or of a channel no longer in scope, is not fetched again and is deleted within 30 days.
- **`docs/privacy.html`:** "Last updated".
- **`docs/PLAN.md`:**
  - the measured rate and costs, replacing the estimates in "The budget is quota";
  - the edge decision under "Policy before engineering";
  - III.D.3's condition;
  - what `videos.batchGetStats` lacks;
  - `brandPartner` as a source for change 4;
  - when the switch is turned on;
  - the restore lesson and the follow-up fix;
  - the maturity window left to change 7;
  - change 3's row.
- **`src/itgraph/README.md` (Russian):**
  - `yt backfill` and `--whole`;
  - the first run;
  - the switch and when to turn it on;
  - `yt backfill-status`;
  - quiet exits;
  - the refresh of past uploads;
  - a restore re-walks.
- **`src/itgraph/CLAUDE.md`:** rows for `yt/backfill.py` and `db/yt_backfill.py`.
- **Root `CLAUDE.md`:** nothing. "API data lives only in `yt_raw_*` … none of it in backups" holds as written.

## Risks / Trade-offs

- [Pages after the first are not newest-first] → Counted on every walk. The first run walks five seeds whole. The remedy, whole walks under the ceiling, changes no table.
- [A page token dies inside a walk] → The client stops on the 4xx and the run exits 1. The channel's record stays unfinished and is walked from the top next time.
- [Members-only, private or deleted items in the listing] → Counted as not returned, or stored if YouTube returns them. The first run says which.
- [The last 28 days understate the year] → At twice the rate a walk of 500 still fits one day under the floor, and it resumes.
- [Channels found by discovery publish differently from the first 105] → Same: the walk is bounded per run and resumes.
- [A year of descriptions is read as more than "limited amounts"] → The depth is a setting. The site states it. It is revisited at 500 channels.
- [The switch is turned on without change 4] → Data with no reader. The README and PLAN say when to turn it on.
- [The switch stays off after change 4] → History expires 30 days after any manual walk. `backfill-status` says the walk is disabled.
- [The refresh fails for over 30 days with the switch on] → Videos expire while their walk records remain, so no re-walk follows. `backfill-status` shows walk-found videos falling. A stop that long already shows on every other YouTube command.
- [The loop and a walk meet on one video] → Kept apart by the horizon and the stored-video rule. `DO NOTHING` on conflict. At worst the walk skips a video the loop took a moment before.
- [A restore] → A re-walk for 430 / 2,140 units. The loop's mislabelled first listing is a separate fix.
- [A refresh without counters replaces the loop's last payload of a finished video] → Only the latest count goes, which the policy treats as replaceable.

## Migration Plan

1. **Revision A:** `ALTER TYPE yt_command ADD VALUE 'backfill'` and the same for `yt_discovery_path`, alone in the revision, with an empty downgrade, as for every enum value since `b4e1d7c93af2`.
2. **Revision B:** `yt_backfill_walks`. Its downgrade drops the table. Both revisions are verified with `upgrade head`, `downgrade -1` and `upgrade head` on a `_test` database only, and autogenerate then reports nothing.
3. **The backup exclusion** lands with or before revision B, so the pre-migration dump on the host is already the new kind.
4. **Deploy** with the switch off: no CronJob, `YOUTUBE_BACKFILL_ENABLED` false.
5. **First real run, by the operator:**
   - `scripts/on-host.sh yt backfill <five seeds with videoCount ≤ 300> --whole`, then `yt backfill-status` and `yt quota`. That settles paging, order, undated and not-returned items, and the cost per page. The data expires in 30 days;
   - optionally, `scripts/on-host.sh yt backfill --limit 100`, a bounded ordinary walk.
6. **With change 4:** `youtube_backfill_enabled: true` and a deploy. The next morning the CronJob walks every seed, about 430 units.

Rollback: switch off, then `alembic downgrade -1` after a backup. The enum values stay. Nothing outside `yt_*`, `db/backup.py` and the two review paths changes.

## Assumptions

Confirmed by the operator on 2026-11-01 (explore, questions 1–11):
- edges and pending mentions may be created before acceptance and live with their payload;
- payloads only until acceptance, kept fresh by the refresh, switched on with change 4 or at acceptance, whichever is first;
- a year's depth as a setting, revisited at 500 channels;
- no reading from history before acceptance; after it, a reading on every fetch, in the poll loop's table; the maturity window left to change 7;
- depth counted from each channel's walk; stop on the crossing page with the order counted; the first run walks five small seeds whole;
- a ceiling of 1,000 per walk; no page token kept between runs;
- the general pool with a floor of 2,000 and `--limit`; no `videos.batchGetStats`;
- the loop's horizon and stored videos left to it; broadcasts yet to start handed over; the `backfill` path; `watch-status` counting only the loop's paths; a lock of its own;
- freshness through the refresh, not re-walks;
- seeds only, every language; a daily CronJob within the floor, the first run by hand; gone channels skipped;
- walk records out of dumps;
- the site updated, no notice under III.D.3;
- `yt backfill` and `yt backfill-status`; quiet exits; 11:30 Moscow; the loop's fields less the counters;
- the restore mislabelling fixed separately.

Not covered by the requirements — open, settled by measurement or the first run:
- the last 28 days' rate stands for the year (twice the rate priced alongside);
- the next 400 channels distributed like the first 105;
- newest-first order past the first page;
- how deleted, private, members-only and scheduled items appear in the listing;
- page tokens outliving one walk's few seconds;
- 0.3–0.6 seconds a request;
- the 37 seeds silent in October costing at most a unit each more than priced.

Decided in this design, open to veto:
- the switch as a setting with a deploy variable, gating three things;
- the walk record's counters as telemetry, extending the 2026-10-26 decision;
- the forgetting rules: out of scope, gone, 30 days while off;
- quiet exits on a quota stop, as the poll loop's and unlike the refresh's;
- the ceiling per walk;
- `--whole` only with named channels, and a named seed walked again;
- no backoff for a failing channel;
- the proposed site wording.

## Open Questions

None that change the specs or the tasks. The wording of the two pages is the operator's, before archiving.
