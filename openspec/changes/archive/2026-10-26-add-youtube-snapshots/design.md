## Context

Second YouTube change. Motivation is in `proposal.md`; requirements are in the three specs.

What is reused: `yt/client.py` and `yt/channels.py` (with `fields` added), the ledger's `book(pool=…)`, which already takes a pool, `store_payloads` and `mark_gone`, `schedule.next_sample_at`, and the backup exclusion `yt_raw_*`, which covers the new tables by their names. What does not carry over from Telegram's `itgraph watch`: the session lease, quiet hours, the reconnect machinery and the stall watchdog. They exist because of a user account and a long-lived MTProto connection, and this has neither.

Those are the requirements this change is held to.

### Facts checked against the documentation, 2026-10-26

| Fact | Source |
|---|---|
| `playlistItems.list`: 1 unit; `maxResults` 0–50, default 5. Nothing said about order, delay, upcoming or private items | developers.google.com/youtube/v3/docs/playlistItems/list |
| `contentDetails.videoPublishedAt` is the video's publication time; `snippet.publishedAt` is when it was added to the playlist | …/docs/playlistItems |
| The uploads playlist id is `contentDetails.relatedPlaylists.uploads` of the channel; deriving it from the channel id is not documented | …/docs/channels; …/guides/implementation/videos |
| `videos.list`: 1 unit; `id` is a comma-separated list, no maximum stated; `maxResults` is not supported with `id`; 404 `videoNotFound` | …/docs/videos/list |
| `snippet.channelId` "can change over time, such as when a video is reassigned to a different channel"; `liveBroadcastContent` is `live`, `none` or `upcoming`; `liveStreamingDetails` is present for upcoming, live or completed broadcasts, `actualStartTime` only once one begins; `dislikeCount` is private since 2021, `favoriteCount` always 0. Nothing said about hidden likes or disabled comments | …/docs/videos |
| `channels.statistics.videoCount` counts public videos only; no update frequency stated | …/docs/channels; revision history 2020-10-08 |
| `fields` filters nested properties; "the API allows, and actually requires, the retrieval of partial resources" | …/v3/getting-started |
| `activities.list`: 1 unit per channel; nothing said about which activity types a channel request returns | …/docs/activities/list |
| Push notifications via PubSubHubbub are part of the Data API: uploads, title and description changes, "near real-time", to a callback server | …/guides/push_notifications |
| `videos.batchGetStats` (2026-06-03): 1 unit "in its own granular quota bucket", 10,000 a day; returns `publishTime`, view/like/comment counts, `duration`, and `failedVideoIds`; no id maximum stated. `videos.insert` and `search.list` have had their own buckets since 2026-06-01 | revision history; …/docs/videos/batchGetStats |
| RSS `feeds/videos.xml`: intermittent 404 since about 2025-12-20; random 200/404/500 reported on 2026-10-19; a 15-minute edge cache | discuss.ai.google.dev; FreeTube #8443; giacomomicoli/feedreader #19 |
| Postgres advisory locks: a single 64-bit key and two 32-bit keys are spaces that "do not overlap" | Context7, PostgreSQL 17 docs, 9.28.10 |

## Goals / Non-Goals

**Goals:**
- A reading of every seed video's counters at increasing ages, from within minutes of upload to 28 days.
- Discovery priced per fifty channels rather than per channel.

**Non-Goals:**
- Sub-fifteen-minute latency. The fastest useful reaction is measured in hours on YouTube.
- Complete early curves.
- Using `videos.batchGetStats`. See "Readings through `videos.list`".

## Decisions

### Discovery watches the video count, and checks it daily

```
                     every 15 min                       once a day per channel
                          │                                     │
  channels.list ×⌈N/50⌉ ──┤ videoCount ≠ stored, or none?       │
  (payload replaced)      ├── no ──► nothing                    │
                          └── yes ─► owed listing ◄─────────────┘ (deferred when ahead of pace)
                                        │
                     playlistItems.list(maxResults=50), one channel, 1 unit
                                        │
                     unknown ∧ (published inside horizon ∨ not yet started) → candidate
```

Units a day, with U uploads per channel per day (not measured: **not covered by the requirements**):

| Discovery | 100 channels | 500 channels | |
|---|---|---|---|
| `playlistItems` every 15 min | 9,600 | 48,000 | over the whole quota |
| `playlistItems` hourly | 2,400 | 12,000 | |
| `playlistItems`, interval from the channel's rate, 1–6 h | ~480 | ~2,400 | a typical channel's first reading at ~3 h |
| `activities.list` | as above | as above | no batch form; reliability for uploads undocumented; not a declared method |
| WebSub | 0 | 0 | needs a reachable callback; the host admits SSH only |
| `search.list` | 100 calls a day | impossible | discovery's bucket |
| **video count + daily check** | **342 / 442** | **1,710 / 2,210** | U = 0.5 / 1.5 |

The chosen row is ⌈N/50⌉·96 + U·N + N. Readings add ~95 units a day for 100 channels and ~130 / ~290 for 500. **Total: 440–540 for 100 channels, 1,830–2,500 for 500**, inside the 3,000 reserve, which therefore stays.

The count is a cheap trigger, not the truth. It counts public videos only, an upload and a deletion in one interval cancel out, and how quickly it moves is undocumented. The daily check is what bounds a miss to a day, and recording which path found each video measures how often the count misses. If it misses often, the fallback is the interval-from-rate row, with no change to the tables.

The trigger compares with the count in the stored payload before it is replaced. A changed count sets `list_owed` on the channel's poll row **in the same transaction** that replaces the payload. Otherwise a pass that stopped between detecting and listing would have consumed the change: the next pass would see the new count as stored and list nothing. The debt is cleared only after the candidates that listing revealed are stored, so a pass that dies between listing and reading lists the channel again next time, for one unit.

A listing for any reason sets `listed_at`, and the daily check is due 24 h after it. That spreads the checks without a random source (`tg/pacing.py` stays the only one): after the first pass they drift apart with each channel's own uploads.

A listing answered 404 or 403 (not quota) is the channel's failure. It is recorded, the debt stays, and the next attempt waits min(2^failures h, 24 h), so a broken channel costs a unit a day rather than 96. Every other error stops the pass, as change 1's commands stop.

Gone seeds are still asked for, so a channel that comes back is noticed, as `yt refresh` does. Their uploads are not listed.

### Readings through `videos.list`, fifty to a request, folded with discovery

One request per fifty videos returns payload and counters together. The payload replaces the stored one and the counters become a reading.

A pass reads, in one ordered list split into fifty-id requests: the candidates found by this pass's listings first, then due videos youngest first. When spending is ahead of pace, videos older than 7 days are left out. Ordering by age makes a pool that runs out mid-pass cost the oldest readings rather than the youngest. A candidate that turns out to be old is dropped without being stored.

`videos.batchGetStats` would move these units into a bucket of their own and return no titles at all. The main bucket is not tight, so this stays recorded in `docs/PLAN.md` as an option.

Fill-to-fifty was rejected: it would read not-yet-due videos in the spare slots of a request. On Telegram the same effect multiplied rows six-fold for nothing.

### The schedule

Ages: 1 h, 2 h, 4 h, 8 h, 12 h, 18 h, 24 h, 36 h, 2 d, 3 d, 4 d, 5 d, 7 d, 10 d, 14 d, 21 d, 28 d, plus the reading at discovery. Horizon 29 d. As settings, `youtube_watch_sample_offsets` (minutes) and `youtube_watch_horizon_days`, validated as Telegram's are.

The horizon is bounded by usefulness, not by retention: a reading at day 28 may legally live to day 58.

`next_sample_at` gains a `horizon` parameter. Telegram passes its own, the default keeps today's behaviour, and the Telegram tests are the check. The YouTube-specific parts live in a new pure `yt_schedule.py`:

- **Origin:** `liveStreamingDetails.actualStartTime` if present, else `snippet.publishedAt`. Both stay in the payload, so scoring can choose differently later.
- **Upcoming:** due at `min(scheduledStartTime, fetched_at + refresh age)`; after the scheduled start, hourly; after 7 days, finished. An upcoming video is fetched and stored but gives no reading.
- **Finished:** `due_at` NULL when past the horizon, or when the video's channel is not watched.
- **Pace:** allowed = cap × (elapsed share of the Pacific quota day) + `youtube_watch_pace_margin` × cap, 10% by default. The day's start comes from `yt_quota.py`, so 23- and 25-hour days need nothing extra.
- **Listing backoff,** as above.

A missed reading is skipped, never replayed, and age is `observed_at` minus origin, never a schedule slot: the rule `schedule.py` already states.

Rejected: coalescing near-due readings into the current pass. At fifteen-minute passes the cost is dominated by the number of passes that read anything, and coalescing would add a second knob to save tens of units.

### The tables

- **Nothing about a video lives outside `yt_raw_*`.** That covers the id, the channel link, the schedule and how it was found. They are worked out from API data and go with it.
- **`channel_id` is a stored generated column,** so Postgres keeps it equal to the payload and the two cannot drift. There is no FK to `yt_channels`: a video may move to a channel not in the inventory.
- **`published_at` is not a column:** a text-to-`timestamptz` cast is not immutable and cannot be generated. Python computes `due_at`, and selection needs nothing else.
- **`bigint`:** counts arrive as strings and exceed 2³¹.
- **Absent is not zero:** a counter missing from the answer is NULL, as in `derive/metrics.py`. Hidden likes and disabled comments are undocumented; the first real data will show how they appear.
- They are in full dumps, not in the inventory tier, as `poll_state` is not.
- **Why a pass table.** A oneshot every fifteen minutes has no process to ask what it last did, so `watch-status` reads that from here. At 96 rows a day it needs no pruning.

### Fields, everywhere, and the trim

Every request carries `part` and `fields`. The `fields` strings are constants next to their parts:

- `channels.list` — `part=snippet,contentDetails,statistics`, `fields=items(id,snippet(title,description,customUrl,country,defaultLanguage),contentDetails/relatedPlaylists/uploads,statistics(viewCount,subscriberCount,hiddenSubscriberCount,videoCount))`
- `playlistItems.list` — `part=contentDetails`, `fields=items/contentDetails(videoId,videoPublishedAt)`
- `videos.list` — `part=snippet,contentDetails,liveStreamingDetails,statistics`, `fields=items(id,snippet(publishedAt,channelId,title,description,liveBroadcastContent),contentDetails/duration,liveStreamingDetails(actualStartTime,actualEndTime,scheduledStartTime,scheduledEndTime),statistics(viewCount,likeCount,commentCount))`

These are exactly `docs/privacy.html`'s lists once the page gains two phrases: "whether the subscriber count is shown", which review already displays, and "the ID of the channel it belongs to". `concurrentViewers` is not asked for.

The second revision trims stored channel payloads to the same shape with one `UPDATE … jsonb_build_object(…)`, so the page is true on the day it is deployed rather than 25 days later. Its downgrade does not restore the dropped keys: they are API data the old code never read, and the next fetch under the old code would bring them back.

A malformed `fields` string is a 400, which stops the pass loudly. The operator's first run settles it.

### Pools: chosen by command, never borrowed

`YtCommand.WATCH` books against `YtQuotaPool.WATCH`, and every other command against `GENERAL`. The mapping is one table in the ledger, not a parameter a caller passes, so no call site can book watch work against general. Nothing borrows in either direction: the caps are settings, and borrowing would turn a misjudged setting into an invisible one.

A spent pool or an exhausted day ends a pass with **status 0**. A fifteen-minute timer turning red thirty times before the reset is noise that would train the operator to ignore the unit. The state shows in `watch-status` and `yt quota`. Any other failure — persistent 5xx, a timeout, a 400 — is non-zero, as for the other commands. The watch command therefore handles `QuotaError` itself before `_run_yt` would map it to 1.

### One pass, under a timer, under a lock

`itgraph-yt-watch.timer`:
- fires every fifteen minutes (`OnCalendar=*:0/15`, overridable as `yt_watch_schedule`) with `Persistent=true`;
- starts a oneshot `itgraph-yt-watch.service` with `TimeoutStartSec=10min`. A oneshot has no start timeout by default, and a hung pass would otherwise silently block every later one;
- is enabled only when `youtube_api_key` is set, exactly as the refresh timer is.

systemd does not start a unit that is already active, which covers the timer racing itself. A pass started by hand is covered by `pg_try_advisory_lock(bigint)` on a dedicated `NullPool` connection, with a constant key. Telegram's lease uses the two-integer form, and Postgres documents the two key spaces as not overlapping, so neither can block the other. A busy lock means a status-0 exit and one sentence.

Rejected: a long-lived daemon like `itgraph watch`. It would need the reconnect, stall and lease machinery that exists for an MTProto connection, to save a Python start-up every fifteen minutes. Quiet hours: they exist to make an account look human, there is no account, and Moscow's night is the American evening, when uploads peak.

### A pass, in order

2. The lock; busy → exit 0.
3. `check_not_exhausted`; exhausted → record the pass, exit 0.
4. Detect: every seed, fifty to a request; payloads replaced; gone and back as in a refresh; changed counts made owed.
5. List: owed and first listings always; daily checks unless ahead of pace; backoff respected.
6. Read: candidates, then due videos youngest first, without those over 7 days when ahead of pace. Stored, read, scheduled; videos YouTube did not return are removed; a 404 batch is asked again id by id; debts are cleared for channels whose candidates are stored.
7. Record the pass.

Each request's results are committed before the next request is made.

### Formats are not told apart

Shorts, long-form, live and premieres are all tracked; nothing stored names a format. That places it in change 7. The facts it will need — `duration`, `liveBroadcastContent`, `liveStreamingDetails` — are in the payload.

### Telemetry is ours

`discovered_at` and `discovered_via` measure itgraph's own pipeline, not YouTube content.

### Settings

`youtube_watch_sample_offsets`, `youtube_watch_horizon_days` (29, past the last offset), `youtube_watch_check_hours` (24), `youtube_watch_defer_older_than_days` (7), `youtube_watch_pace_margin` (0.10), `youtube_watch_upcoming_give_up_days` (7). The cadence is the timer's, a deploy variable, and the code assumes none.

### Documentation

- `docs/index.html`: Methods — "`channels.list` for the inventory and to notice new uploads"; the status panel — statistics tracking in use, alerts in development.
- `docs/privacy.html`: the channel and video field lists as above; "fetched again at most every 25 days" becomes "Channel data is fetched again before it is 25 days old. Plus "Last updated".
- `docs/PLAN.md`:
  - the discovery cost and the reserve, against the earlier estimate that priced snapshots only;
  - `videos.batchGetStats` and the granular buckets;
  - change 2's row.
- `src/itgraph/README.md` (Russian): `yt watch`, `yt watch-status`, the timer, what a quiet exit means, the first-run check.
- `src/itgraph/CLAUDE.md`: rows for the new modules.

## Risks / Trade-offs

- [`videoCount` lags or misses] → The daily check bounds a miss to a day, and the path record measures it. The fallback, an interval from the channel's rate, changes no table.
- [Upload rate U is a guess] → The first two weeks of `yt quota` measure it, and the reserve is a setting.
- [An upcoming video whose playlist date is older than the horizon] → It is not a candidate, and the stream is missed until it is published anew. Undocumented, rare in IT, accepted, and stated in the spec as a scenario of "What Counts As A New Video". Asking about every such item instead would cost about a unit per listing — nearly all of a listing's fifty are old — and double the price of discovery.
- [`fields` syntax or a part combination refused] → A 400 stops the first pass loudly; fixed before anything else.
- [A batch with a dead id answered 404 as a whole] → Asked again id by id, as `channels.list` is.
- [Hidden counters appear as 0 rather than absent] → It would show as zeros on videos whose pages show none. Checked on the first real data; NULL handling is already there.
- [A hung pass] → `TimeoutStartSec` kills it. Its advisory lock dies with its connection, and its committed work stays.
- [The first pass lists every seed at once: 100–500 units] → Inside the pool, and first listings are never deferred.
- [Telegram's `next_sample_at` changes signature under a live loop] → A default keeps its behaviour, and its tests run unchanged.
- [The trim is one-way] → It drops only keys nothing reads. They return with the next fetch under the old code if this change is rolled back.

## Migration Plan

1. Revision A: `ALTER TYPE yt_quota_pool ADD VALUE 'watch'` and the same for `yt_command`, alone in the revision, with an empty downgrade, as the project has done for every enum value since `b4e1d7c93af2`.
2. Revision B: the four tables, `yt_discovery_path` and `yt_pass_outcome`, and the trim. Its downgrade drops the tables and types and leaves the trim. Both revisions are verified with `upgrade head`, `downgrade -1` and `upgrade head` on a `_test` database only, and autogenerate then reports nothing.
3. The playbook installs the two units. The timer is enabled because the key is set. The migration takes its pre-migration dump, as every migration does.
4. First real pass, by the operator: `scripts/on-host.sh yt watch`, then `yt watch-status` and `yt quota`. That settles `fields`, the 404 shapes, absent counters and the first listing's cost. A surprise is a fix before the timer's next firing matters.

Rollback: disable the timer, then `alembic downgrade -1` after a backup. Nothing outside `yt_*` changes except `next_sample_at`'s signature.

## Assumptions

Confirmed by the operator on 2026-10-26 (explore, questions 1–12):
- discovery by video count with a daily check;
- readings through `videos.list`;
- the schedule and the 29-day horizon;
- every format, none classified;
- seeds only, every language;
- gone videos removed with their readings;
- the first listing takes recent videos;
- a timer with a lock and no quiet hours;
- pacing, quiet exits and no borrowing;
- `next_sample_at` shared, the rest in `yt_schedule.py`;
- `fields` everywhere, including the trim of change 1's payloads;
- telemetry as ours;

Not covered by the requirements — open, and settled by the first real data:
- `videoCount` moves within minutes of an upload;
- new uploads sit within the newest fifty playlist items;
- `videos.list` takes fifty ids;
- a missing id is dropped from `items` rather than failing the batch;
- hidden likes and disabled comments are absent, not zero;
- premieres pass through upcoming → live → none with `liveStreamingDetails`;
- U between 0.5 and 1.5.

Decided in this design, open to veto:
- the root `CLAUDE.md` wording above;
- the pass table;
- the 10-minute start timeout;
- the listing backoff of 2^failures hours, capped at 24.
