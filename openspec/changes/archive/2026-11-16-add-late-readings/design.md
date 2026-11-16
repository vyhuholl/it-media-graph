# Design

## Context

See `proposal.md` (Why, and the decision record). The requirements are in `specs/`. What shapes the approach, read from the code and from the production database on 2026-11-16 (aggregates only):

- **Telegram.** `tg/watch.py` fetches a channel's newest `window_size(posts_per_day)` messages in one `getHistory` request (at least 10, at most 100), stores every payload it has not stored, and keeps a reading only of posts younger than `watch_horizon_hours` (49). The rest are dropped as "a number that has stopped moving". The poll's due time comes from the posts inside the horizon (`next_channel_poll`). With none, it comes from the posting rate: 30 minutes to 12 hours, stretched up to ×5 for an empty channel, so about 2.5 days at most. About 2,500 polls a day store readings.
- **How far the window already reaches.** Over 550 seeds: median 0.40 posts a day, so a window spans a median of 16.7 days of posts. It spans at least 7 days for 73% of seeds, at least 30 days for 37%, and less than 3 days for 9%.
- **What Telegram scoring reads.** Every fit and score finds a reading's band by age (`AgeBands.band_of`), and Telegram's bands end at the alerting window. The mature medians read `raw_messages` (`fetched_at − date`), not `message_metrics`. A reading past 49 hours is in no band.
- **YouTube.** The poll loop reads a video from 1 hour to 28 days. Past that, `yt refresh` (daily at 05:00 Moscow, general pool, command `refresh`) fetches a past upload's whole payload again before it is 25 days old, and stores a reading from the same answer. 58,354 videos are stored: 51,059 found by the walk, 14,539 aged 29–120 days and 38,464 older. No video has two readings past 29 days.
- **The settled reference** (`scoring/yt_refresh._settled`) takes, for each video, its *latest* reading between 29 and 120 days. 14,498 videos have one. None has two.
- **Quota** (the ledger's last two weeks): the general pool spends 5 units a day of its 6,000; the poll loop spends 1,780–2,300 of its 3,000.

## Goals / Non-Goals

**Goals:**

- Readings of every post and video at ages nothing reads today, on a schedule fine enough for a weekly judgement: Telegram to 30 days, YouTube to a year.
- No change to what the Telegram account sends.
- No change to any score, alert or list on the day it ships, and none later without a change that says so.

**Non-Goals:**

- Judging late readings (change C), Telegram's weekly list (change B). See proposal.md, Out of scope.

## Decisions

### 1. Telegram: the counters the window already brings

The operator's answer 2: no request added and none changed. The window already reaches a median of 17 days, so keeping what it returns gives most seeds most of the month.

Considered:

- **Asking for 100 messages every poll.** That is the same number of requests, as `window_size`'s docstring notes. But every answer grows up to tenfold, and the shape of the account's traffic changes. Rejected by the operator's answer.
- **`messages.getMessagesViews`.** A request of its own, rate limits undocumented, and it returns views, forwards and replies with no reactions. Rejected.

The cost is that coverage follows the posting rate. A channel posting more than about 3 times a day keeps its posts in the window for under 3 days, so it gets few late readings or none. Change C has to say so wherever it ranks across channels.

### 2. The late ages and the late horizon

`watch_late_offsets_days = (3, 4, 5, 7, 10, 14, 21, 30)` and `watch_late_horizon_days = 35`:

- **The ages are roughly geometric.** Each is 1.25–1.5 times the one before, so the readings are dense where views still move (median ×1.12 from day 1 to day 2) and thin later. They start a day past the last regular sample (48 hours), so each comes from a poll that would happen anyway.
- **They stop at 30 days** because the window reaches past 30 days for only 37% of seeds, and a monthly reading is enough for a weekly list.
- **The horizon is 35 days, not 30.** An idle channel can be polled as rarely as every ~2.5 days, and the 30-day reading needs room to be taken.
- **About 8 rows a post at most:** ~576 posts a day, so ≤ 4.6 thousand rows a day against ~38 thousand today.

Considered:

- **A reading on every poll.** Up to 100 posts × 2,500 polls a day. Rejected.
- **A ratio rule** ("read again once the age is 1.5× the last reading's"). This gives the same spacing, but it is not a list of ages the configuration can show and a validator can check.

Settings are validated at load: ascending, the first age past `watch_horizon_hours`, and the horizon past the last age.

### 3. A late reading is taken at the first poll past its age

A post is read when the largest late age it has passed is later than the age of its newest reading. A post with no reading counts as never read. At most one reading per post per poll.

This is the one reading in the project taken after its moment, and deliberately so. The rule that a missed sample is dropped (`schedule.py`) exists so that an outage does not turn into a burst of requests. A late reading makes no request, so that reason does not apply. Dropping a late reading because the poll came hours after its age would lose most of them: an idle channel is polled hours to days apart. The age stays a fact about the row (`observed_at − date`), as every scoring pass already treats it.

A late age passed by more than one reading at once leaves one row (spec: "Several late ages passed at once give one reading"). Nothing is replayed.

### 4. Where it lives in the code

- **`schedule.py`** gains `late_offsets()`, `late_horizon()` and a pure `late_reading_due(published_at, newest_reading_at, now) -> bool`. These follow Decisions 2 and 3; there is no database or clock here.
- **`db/metrics.py`** gains the newest reading of each of a set of posts of one channel: one `GROUP BY msg_id` on the primary key `(channel_id, msg_id, observed_at)`, asked only for the window's posts past the horizon.
- **`tg/watch.poll_channel`** adds to `readings` the posts past the horizon that `late_reading_due` names, in the same transaction and with the same `observed_at`. `PollOutcome` counts them, and the loop's report line says how many.
- **Unchanged:** `window_size`, `next_channel_poll`, `live_post_dates`. Tests pin that the request's `limit` and the next due moment are identical with and without posts due a late reading (spec: "Late Readings Cost No Request").
- **Scoring:** a test pins that a Telegram baseline refresh and a scoring pass give identical results on a database with and without late readings.
- **Found while applying.** That test failed on one column: `metric_baselines.samples` grew by about a quarter. `db/baselines.observations` loaded every reading of a post in the fit window, and `fit` stores `len(entries)` as the samples. No band reaches a late reading, so factors, spreads, curves, references and alerts were all already identical. `observations` now takes `horizon` (the tracking horizon, passed by `scoring/refresh.py`) and leaves out readings past it. The stored count stays what it was, and a refresh does not load on the order of a hundred thousand rows it would ignore. The characterisation record is unedited and still matches.

### 5. YouTube: a step of `yt refresh`, not a command of its own

The refresh already runs daily from the general pool, keeps the same set of videos (`past_uploads_due_for_refresh`'s predicate without the payload's age), and lands its readings half an hour before the baselines. The late readings become its last step: channels, then featured lists, then past uploads' data, then late readings. The order matters for one reason: fetching a past upload's data again takes a reading, so a video refreshed in this run is not due a late reading in the same run.

Considered:

- **A command and CronJob of its own** (`yt late`). A second regular job to schedule, grant, graph and alert on, for work the refresh's job already frames. Rejected.
- **The poll loop.** Its reserve is 76% spent and runs out at about 850 seeds; ~400 more units a day would bring that forward. Rejected.
- **`videos.batchGetStats`.** Its own bucket of 10,000, a unit a call. But its id limit is undocumented (50 per a third-party changelog only), it has no `fields` parameter, and the ledger would need to know a third bucket. Kept as the reserve should the main bucket become tight (`docs/PLAN.md` already says so).

### 6. A late reading leaves the payload as it was

The request is `videos.list` with `part=statistics`, `fields=items(id,statistics(viewCount,likeCount,commentCount))`: a new `Asked` value, `COUNTS`, in `yt/videos.py`. It costs the same unit as the full shape, carries no descriptions (the bulk of an answer), and rewrites no row of `yt_raw_videos`. A full shape daily would rewrite ~20 thousand payloads of ~2 kB a day. They are TOASTed, and they carry a generated `channel_id`, so no update is HOT.

The cost is that a payload's own `statistics` lag the newest reading by up to 25 days. Nothing reads counts from a payload: readings are written from an answer by `derive/yt_videos.counters_of`, and scoring, alerts and the weekly list read `yt_raw_video_stats`. A task checks this. A video moved to another channel is noticed by the 25-day refresh of its data, as now.

A video the answer does not hold is removed with its readings by `remove_videos`, as every request for videos does. A whole batch answered 404 is asked id by id (`by_ids` already does).

### 7. The cadence, and why a daily interval comes due early

A past upload is due a late reading when its newest reading was taken before `now − interval + 3 h`:

- **The interval** is 1 day up to `youtube_late_daily_until_days` (120) days of age, and `youtube_late_weekly_interval_days` (7) after, up to the walk's depth.
- **Age** counts from `publishedAt`, as the past uploads' predicate does. At these ages a broadcast's hours between scheduling and start do not matter.
- **The three hours of slack.** A run starts at 05:00 give or take a minute, and takes minutes. A reading taken at 05:00:40 is not a day old at 05:00:20 the next morning, so without slack a "daily" video would be read every other day.

120 days is the settled window's upper edge, and the age to which the poll loop's own data show growth still moving. Weekly after that gives one increment per week of change C's list.

The due query reads each video's newest reading by the primary key `(video_id, observed_at)`: about 58 thousand index probes a day. Settings are validated at load: the daily tier past `youtube_watch_horizon_days`, the weekly interval at least a day.

Priced at today's stock: 14.5 thousand daily and 38.5 thousand weekly, so ~20 thousand readings and ~400 units a day. At the upload rate measured on 2026-11-11 (177 videos a day), the year settles at ~450 units a day and ~8 million rows a year. The first run after deploy reads everything (all ~52 thousand videos are due), ~1,050 units, once.

### 8. The settled reference takes the earliest reading in its window

With daily readings, "the latest reading between 29 and 120 days" becomes the reading nearest a video's current age, capped at 120. A month later every channel's settled median would sit higher, and every fallback z lower, without any change saying so. 193 of the 221 channels scored at the threshold's pricing were on the fallback. The earliest reading in the window stays put: for a video the loop finished, it is its first reading past 29 days, and for one the walk found already settled, its single reading. No video has two readings in the window today, so the switch changes no reference when it ships. The kind factor is fitted from the same rule in the same run, so it stays consistent with it. A replay with `--late-settled` takes the earliest reading up to its later bound.

Considered:

- **Keeping "latest" and re-pricing the threshold.** The reference would keep drifting for 90 days as readings accumulate, so one re-pricing would not hold.
- **The reading nearest a fixed age**, such as 60 days. Most past uploads were walked at a single age that is not 60.

### 9. The refresh records itself

`yt_refresh_runs`: `id`, `started_at`, `finished_at`, `outcome` (the existing `yt_pass_outcome`: `complete`, `pool_spent`, `exhausted`, `failed`), `late_due`, `late_read`, `error`. One row is written per run at its end, in a transaction of its own. A process killed mid-run writes none, and so does not count as complete. Rows are kept: one a day.

The exporter gains `SELECT (finished_at, outcome)` on it, and serves `itgraph_youtube_last_complete_refresh_timestamp_seconds`. The infrastructure repository is asked for an alert when the newest complete refresh is older than 30 hours (a daily run, plus a slow run and a restart), and a panel.

Considered:

- **A record of the late step alone.** The refresh is the regular job. Its channel step has the payload-age series, but its featured lists, past uploads and now late readings had none. One record covers the job.
- **Inferring runs from the request journal.** Requests made is not a run completed. Rejected.

### 10. Nothing new for Telegram's monitoring

Late readings ride inside the watch loop, a daemon whose polls, lag and failures are already series. A series of late readings would need `message_metrics`, which the exporter must not scan. The loop's report line counts them.

### 11. The restore

Late readings and refresh records act on nothing. `neutralise.sql` and `readback.sql` do not change. A test environment holds no YouTube key, so no refresh runs there. Its series shows the restored copy's newest complete refresh, or 0. The infrastructure repository's alert is production's, as the poll pass's is.

### 12. Documentation

- **`docs/PLAN.md`:** the measurements of 2026-11-16 under "Detecting strong reactions" and under YouTube "Scoring". The order A → B → C under "Order of changes". The sentence "then never again" on Telegram's samples, corrected.
- **`src/itgraph/README.md`:** the late readings in the loop and the refresh, the new series in its table, and what the refresh's report line says.
- **The module map** (`src/itgraph/CLAUDE.md`): `schedule.py`, `yt_schedule.py`, `db/metrics.py`, `yt/videos.py`, `yt/refresh.py` and `db/yt_videos.py` rows updated.
- **The site is not touched.** The operator decided on 2026-11-16 that the mismatch between `docs/privacy.html` and the code is ignored (proposal.md, decision record).

## Risks / Trade-offs

- **[Telegram coverage follows the posting rate]** → Accepted with the operator's answer 2. Change C states which channels it can judge.
- **[A late reading silently changes Telegram scoring]** → Bands end at the alerting window, and the mature medians read `raw_messages`. A test pins identical results (Decision 4).
- **[The settled reference drifts]** → Decision 8.
- **[The general pool runs out before the late step]** → The step is last, and the refresh runs late in Google's quota day (05:00 Moscow is 18:00–19:00 Pacific). A discovery round could leave too little. The run is then recorded as stopped and the series shows it. "Older than the interval" means a missed day is read the next day, with no backlog beyond one day's tier.
- **[A payload's counts lag its readings]** → Nothing reads them (Decision 6). A task checks every reader of `statistics`.
- **[YouTube lowers a count]** (spam removal, verification) → Stored as reported. A negative increment is change C's to handle.
- **[Dumps grow]** → ~8 million YouTube rows a year, plus ≤ 1.7 million Telegram rows. Full dumps grow by well under a gigabyte a year compressed. The off-site store's caps are $0 by design, so a dump that no longer fits fails loudly: watch the dump size after the first month.
- **[The first run after deploy spends ~1,050 units]** → Within the general pool's 6,000. It is said in the runbook step of the deploy.

## Migration Plan

1. The revision adds `yt_refresh_runs` and the exporter's grant. A full dump is taken before it, as for every revision.
2. **Test environment first:** deploy the pinned release on a restored copy. The revision applies, the exporter serves the new series (0, or the copy's moment), the watch loop is not run there, and no refresh runs (no key). `make test-e2e` covers the role.
3. **Production:** move the pin. The first watch-loop polls store late readings of posts already past their ages, a one-off ~18 thousand rows. The first refresh reads every past upload (~1,050 units).
4. **Rollback:** revert the pin. The revision's downgrade drops `yt_refresh_runs` only. Late readings are ordinary readings and stay.

## Open Questions

- **`videos.batchGetStats`' id limit**: deferred with the method itself (Decision 5).
