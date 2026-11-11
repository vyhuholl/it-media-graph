# Design

## Context

See `proposal.md`, Why, and its decision record. The requirements are in `specs/`. What shapes the approach, read on 2026-11-11 from the production database as `itgraph_reader`, aggregates only:

- **What exists.** Readings since 2026-11-01 02:15 MSK alone: 35,687 readings of 6,337 videos. Dumps carried no YouTube rows until b5b0c15, so the move to the server restored the YouTube tables empty, and the laptop's volume was removed with them; about six days of the poll loop were lost. 1,938 videos were watched from their publication: 1,847 read before two hours, 1,661 reached 24 hours, 1,234 four days, 585 seven days, 49 ten days, none fourteen. One reading per video per scheduled age — unlike Telegram, a video is not read again because a neighbour is due.
- **No settled reading.** The 51,065 past uploads the history walk stored on 2026-11-01 and 02 carry no `statistics`: the walk asked for none until b5b0c15. Their payloads reach the refresh age on 2026-11-26 and 27, when the refresh reads them all at once — about 1,010 units over two days — unless a one-off pass reads them first.
- **Upload rate,** 28 days by publication: English 3,746 (134 a day, 0.27 a channel a day; personal 0.13, the other kinds 0.42–0.46), Russian 1,197 (43 a day, 0.25). No upload in 90 days: 198 of 500 English seeds, 54 of 169 Russian. The ten busiest channels upload 26% of the English videos and 46% of the Russian.
- **Batches.** Of consecutive uploads of one channel over 90 days, 14% follow the previous one by under a minute, 25% under an hour, 42% under six hours, 65% under a day — conference channels publish talks in batches.
- **Formats,** 90 days, 15,016 videos of English and Russian seeds: 28% are 60 s or less, 17% 61–180 s, 47% longer, 8% carry `liveStreamingDetails` (streams and premieres). Russian: 57% at 180 s or less. `#shorts` in the title or description marks 12% of the short ones. Within one channel, at 24 hours, the views of a video of 60 s or less run 0.76 of its long-form median, of 61–180 s 0.73, of a broadcast 1.55; likes per view 1.53%, 2.75%, 1.29% for the long-form.
- **Counters at 24 hours,** 937 long-form videos: likes absent on 2.7%, comments absent on 5.4%, zero comments on 46.5% (shorts about 48%).
- **The first hours of the view counter lag.** At one hour 10% of videos read zero views; between consecutive readings the views stayed put on 14% of videos at one hour and 23% at two, against 2% at eight; the median goes from 60 at two hours to 212 at four, ×3.5, while likes go ×1.5.
- **Growth,** median fraction of the 24-hour value:

  ```
                1h    2h    4h    8h   12h   24h   36h    2d    3d    4d    5d    7d
  views  long  .03   .06   .25   .54   .72  1.00  1.15  1.24  1.39  1.49  1.54  1.70
  views  short .06   .12   .29   .53   .70  1.00  1.17  1.27  1.44  1.55  1.58  1.59
  likes  long  .17   .30   .46   .67   .80  1.00  1.08  1.16  1.25  1.33  1.37  1.48
  ```

  Views still grow at seven days: there is no saturation like Telegram's at eight hours.
- **Who can have a settled reference,** long-form videos published 29–120 days ago: at least 10 on 121 English seeds and 35 Russian (at least 5: 177 and 57; at least 20: 66 and 19); short-form, at least 10: 94 and 49. Of the 282 English and 103 Russian seeds that published long-form in 90 days, [29; 180] with at least 10 would cover 166 and 49, holding 92% and 87% of their uploads. Videos published 29–90 days ago: 9,922; 29–120: 14,450; in a year: 50,472.
- **Who can have a reference of its own soon:** at least ten long-form uploads in 45 days — 67 English and 22 Russian seeds; in 90 days 125 and 40.
- **Telegram's scoring** (`scoring/`): the age bands, the reference band and its settling age are module constants of `curves.py`; keys are `tuple[int, int]` throughout `curves.py`, `score.py` and `fold.py`; `refresh.py` and `run.py` read `settings` and Telegram's tables directly. `counts.py` is platform-free.

## Goals / Non-Goals

**Goals:**

- Alerts in both YouTube topics, on a threshold priced by replay on YouTube's own data.
- Every number measured on YouTube, stored with the run that measured it, and refitted daily as the data grows.
- Telegram's scoring and alerts unchanged, and shown to be.

**Non-Goals:**

- The weekly list (a change of its own, next), the calibration on weeks of production data (a change of its own), streams and premieres.
- A curve beyond 8.5 days, or one normalising settled readings by age.

## Decisions

### 1. What is scored

A video is scored while its channel, as YouTube attributes it now, is a seed labelled `en` or `ru` with a kind. `other` is not scored — change 6's decision. A video with `liveStreamingDetails` is not scored and is counted. A video's age is the reading's `observed_at` minus its publication; broadcasts, which alone have an actual start, are not scored.

### 2. Formats

Worked out on reading from the stored payload, never stored per video — the loop's spec forbids storing a format, and nothing here contradicts it: a baseline per channel and format says nothing about any one video.

| format | rule |
|---|---|
| `long` | no `liveStreamingDetails`, `contentDetails.duration` over `youtube_scoring_short_max_seconds` (180) |
| `short` | no `liveStreamingDetails`, duration at most 180 s |
| — | with `liveStreamingDetails`, or without a readable duration: not scored, counted |

180 s is YouTube's limit for a Short since October 2024, and the measurement agrees: 61–180 s videos read like the 60-s ones, not like long-form. Some 2-minute videos that are not Shorts are scored as short-form; they are short-form either way. `#shorts` is not read: it marks one short video in eight. Undocumented playlists and `/shorts/` requests stay unused.

### 3. Signals

Views and likes as Telegram's views: `z = ln(max(observed, 0.5) / expected) / spread(band)`. Comments as counts (Decision 6). Each against its own curve; no ratio, no combined score. A counter absent from a reading is not scored and is no zero; a channel whose likes are hidden on every video has no like reference and is not scored on likes.

### 4. Age bands and the alerting window

Contiguous, one scheduled reading age in each, named for their lower edge in minutes as Telegram's are:

```
  band     180m  360m  600m  900m  1260m  1800m  2520m  3600m  5040m  6480m  8640m  | 12240m
  span     3–6h  6–10h 10–15h 15–21h 21–30h 30–42h 42–60h 60–84h 84–108h 4.5–6d 6–8.5d|
  reading   4h    8h   12h   18h    24h    36h    2d     3d     4d     5d     7d
```

- **From three hours:** the counter lags before (Context). The readings at one and two hours are kept in the raw layer and scored by nothing.
- **The reference band is 1260m, 21–30 hours,** and its settling age 30 hours: early enough for a channel's own reference to fill within weeks, late enough that the counter has caught up. Every fraction past it is above 1, as the curve says.
- **Readings past 8.5 days raise nothing** — the operator's 3 hours to 7 days. A pass looks at videos published within the last 8.5 days.
- A reading lands within a pass interval of its age; deferred readings of videos over seven days old still fall inside the 7-day band unless deferred by more than a day and a half.

The bands are `scoring/` constants of YouTube's, as Telegram's are of Telegram's (Decision 12).

### 5. The channel's reference

Per channel, format and metric — a channel's Shorts and its talks are two references:

- **Its own:** the median 1260m value of its newest `youtube_baseline_recent_videos` (20) videos of that format, needing at least `youtube_baseline_recent_min_videos` (10), none older than `youtube_baseline_recent_max_days` (180). Factor 1. Built only from videos settled (30 hours old) before the moment it is taken for; for a residual, before the video's own publication — `reference_at`, unchanged.
- **The fallback:** the median of its settled videos of that format, one reading per video — the latest whose age is inside [`youtube_baseline_mature_days`, `youtube_baseline_mature_max_days`] = [29, 120] — needing at least `youtube_baseline_min_channel_videos` (10). Scaled by the kind's factor. The latest rather than the first: views accrue for months, and the refresh reads a past upload every 25 days, so a video holds two to four readings in the window and the latest is the nearest to settled.

Recorded per channel as `recent` or `mature`, Telegram's `baseline_reference` type, whose two values mean the same on both platforms. [29; 120] with at least 10 covers, of the last 90 days' uploads, 84% of the English long-form and 79% of the Russian, 89% and 97% of the short-form; the bias that growth puts into a 120-day window is measured once settled readings exist, against [29; 90] and [29; 180], and the window moves with calibration.

### 6. Comments

Telegram's model, unchanged: the reference the mean of the newest videos' 1260m counts, zeros kept; the curve a ratio of sums; negative binomial with a dispersion per kind and format by likelihood; the z of the upper tail, ceiling 40; mean floor 0.05. **No settled fallback,** as on Telegram: a settled count is a final count, and its factor would be fitted on the commented videos alone. Until channels fill their own references comments are scored on few of them — 67 English and 22 Russian by mid-December, about. A minimum count `youtube_alert_spike_comments_min`, 10 to start, priced by the replay (Decision 11).

### 7. Curves, factors, spreads

Per kind × format × metric, from the readings of videos published within `youtube_baseline_window_days` (28):

- the curve as Telegram's — the median per-video fraction of the 1260m value, zeros out, for views and likes; a ratio of sums, zeros in, for comments;
- the factor, the median of 1260m value over the channel's settled median, per kind and format;
- the spread per band, by MAD, against the reference each video would have been scored against; a band keeps its own only past `youtube_baseline_min_band_samples` (20) readings and `youtube_baseline_min_band_spread_posts` (100) videos, and borrows the pooled figure otherwise;
- a kind too thin for its own fit borrows the format's pool, the whole fit, flagged `borrowed`.

Language is no dimension: the curve is a shape, normalised per video, and the replay reports both languages apart to show whether it should be.

### 8. Storage

One revision, YouTube's own tables beside Telegram's, as `docs/PLAN.md` decided for every table keyed by an id:

```
yt_baseline_runs      id, started_at, completed_at,
                      window_days, mature_days, mature_max_days, min_channel_videos,
                      recent_videos, recent_min_videos, recent_max_days,
                      min_band_samples, min_band_spread_posts,
                      channels_in_scope, channels_with_baseline, channels_on_fallback
yt_channel_baselines  run_id → runs CASCADE, channel_id → yt_channels CASCADE,
                      format, metric, median, samples, reference      PK (run, channel, format, metric)
yt_metric_baselines   run_id, kind yt_channel_kind, format, metric,
                      factor, spread, samples, dispersion, borrowed     PK (run, kind, format, metric)
yt_curve_points       run_id, kind, format, metric, band, fraction, spread, samples
                                                                       PK (run, kind, format, metric, band)
types                 yt_metric (views, likes, comments), yt_video_format (short, long)
```

`yt_alerts` is unchanged. A run is read only once `completed_at` is set; older runs stay, as Telegram's. The medians are itgraph's numbers about channels, derived from readings and refitted from them — as `yt_alerts.expected` is — and copy nothing YouTube said about any one video. `itgraph_reader` reads the new tables through its default privileges; the bot's role needs none of them.

### 9. The scoring pass and the alert

`itgraph yt score`: load every reading of a scored video published within 8.5 days, up to `now`; score each reading in a band on every signal; per video, the best score across readings and signals, and the moment it first crossed `youtube_alert_spike_z` — the comment count under its minimum set aside first, as Telegram's. A video already raised for that kind is not raised again.

The alert: `kind` the best signal's (`views_spike`, `like_spike`, `comment_spike`), `scored_at` **the reading the best score came from**, `band` 1, `value` the z, `expected` for comments. `scored_at` is what the message reads a comment's count back from, and `value` and `expected` are that reading's — the first crossing is used only to order a channel's spikes. A `Spike` carries that reading's moment beside `crossed_at`; Telegram's leaves it unset and reads nothing new.

The pass makes no request, books no quota, and changes nothing but `yt_alerts`. Its summary: videos in the window, scored, crossed, raised and folded; channels in scope, with a baseline, on the fallback; broadcasts and unreadable durations not scored; the baselines' age.

### 10. Folding

Telegram's `fold`, a channel's spikes within `youtube_alert_spike_fold_hours` (24) of its head being one event, the channel being the video's current one. 24 rather than 6: readings are hours to days apart, so one event's videos cross at different scheduled ages. No burst collapse: a YouTube video is never published in parts, and a batch of talks is distinct videos, which folding already makes one message. The window is measured in the replay — the gaps between one channel's crossings — and moves with calibration.

### 11. Replay

`itgraph yt score --replay [--since DAYS] [--threshold Z]` is the live pass at earlier moments, writing nothing. Unlike Telegram's, which scores the period against the current run, it **refits the baselines in memory as of the start of each day (Moscow)** and scores that day's readings against them: the curves are fitted on the days they score otherwise, and with two weeks of data that is most of the fit. The refit is the refresh's own code with its writes left out. It reports, per language and day, crossed, heads and folds; per signal and format; channels on each reference; the gaps between one channel's crossings; and, with `--list`, each head and its folds by video id.

The threshold and the comment minimum are priced on it before the first deploy — the volume per language against the target of about 2–5 a day in English and 1–2 in Russian — on a restored copy in the test environment, by the operator, since no itgraph command runs against the working database.

### 12. `scoring/`, platform-blind

- `curves.py`: an `AgeBands` value — edges in minutes and the reference band — with `band_of`, its bands, the reference band and its settling age. `TELEGRAM_BANDS` is today's edges exactly, so the stored band names are unchanged; `YOUTUBE_BANDS` is Decision 4's. `Curve` carries its bands; `reference_at` takes them.
- Keys generic (PEP 695): `Observation`, `Spike`, `Head`, `Placed`, `fold` and `fit_factor` over a channel key and a post key, Telegram's `int`s and YouTube's `str`s.
- The fitting core of `refresh.py` — `_fit`, `_residuals`, `_dispersion`, `_rolling` — moves to a pure `scoring/fit.py` taking its floors and reference settings as arguments; `refresh.py` passes the settings it reads today. `run.py`'s `_best_per_post` likewise, with the comment minimum as an argument.
- Telegram's orchestration stays where it is and reads what it reads. YouTube's is new: `scoring/yt_refresh.py`, `scoring/yt_run.py`, `db/yt_baselines.py`, and raising in `db/yt_alerts.py`.

**Proof that Telegram did not move**, in this order: a characterisation test recorded before the refactor — Telegram's refresh and replay on a synthetic fixture, every stored curve point, factor, spread, dispersion and reference, and every spike and fold — and held equal after it; Telegram's existing suite unchanged; and on a restored copy in the test environment, `itgraph baselines --now T` and `itgraph score --replay --now T` under the previous image and this one, their runs and listings compared. `--now` is a hidden option added to both; it changes nothing without it.

### 13. Settings and commands

`config.py`, each with Telegram's validation where it has a counterpart: `youtube_alert_spike_z` (default from the replay, task 8.2), `youtube_alert_spike_metrics` (views, likes, comments), `youtube_alert_spike_comments_min` (10), `youtube_alert_spike_fold_hours` (24), `youtube_scoring_short_max_seconds` (180), `youtube_scoring_replay_days` (14), `youtube_baseline_window_days` (28), `youtube_baseline_mature_days` (29) under `youtube_baseline_mature_max_days` (120), `youtube_baseline_min_channel_videos` (10), `youtube_baseline_recent_videos` (20) / `_recent_min_videos` (10) / `_recent_max_days` (180), `youtube_baseline_min_band_samples` (20), `youtube_baseline_min_band_spread_posts` (100), `youtube_baseline_refresh_days` (2: older baselines are named stale in the pass's summary).

Commands: `itgraph yt baselines [--now]` and `itgraph yt score [--replay] [--since] [--threshold] [--list] [--now]`. Neither needs a YouTube key, and both say so in their help. Logs name video and channel ids only.

### 14. Series

Two, for both platforms, from the run rows alone:

| Series | Type | From |
|---|---|---|
| `itgraph_baselines_completed_timestamp_seconds{platform}` | gauge | the newest completed baseline run, 0 when none |
| `itgraph_baselines_channels{platform,state}` | gauge | that run's channels, `state` `in_scope` or `with_baseline` |

The exporter's role gains `SELECT` on `completed_at`, `channels_in_scope` and `channels_with_baseline` of `baseline_runs` and `yt_baseline_runs`, nothing else. A failed pass is already the shared monitoring's (failed CronJobs); what was missing is a pass that succeeds on stale baselines. The infrastructure repository is asked for an alert on baselines older than two days per platform and a panel.

### 15. Schedules, role, environments

| CronJob | schedule | starting deadline | active deadline | reaches |
|---|---|---|---|---|
| `itgraph-yt-score` | `6-59/15 * * * *` | 600 | 600 | the database |
| `itgraph-yt-baselines` | `30 5 * * *` | 82800 | 10800 | the database |

`yt score` six minutes after each poll pass, whose runs take 9–13 s; `yt baselines` half an hour after `yt refresh`, which brings the settled readings. Both in `__itgraph_cronjobs_always`, labelled for the database alone, with the config and the database's URL and no other secret: they need no key, so they run in every environment, the test environment included, where they are tried on restored data. `youtube_alert_spike_z` reaches them as `itgraph_youtube_alert_spike_z` in `defaults/main.yml` → `YOUTUBE_ALERT_SPIKE_Z` in the ConfigMap, as Telegram's. On a copy the scoring raises what restored readings cross, once, into the test environment's own YouTube chat; what production raised is in the copy and closed by `neutralise.sql`, so nothing repeats.

### 16. Backups and the restore

Full dumps carry the new tables. `neutralise.sql` has nothing new to close: the baselines hold no note and nothing that acts, and the scoring acts only through `yt_alerts`, already closed. The read-back is unchanged: no new field of a payload is stored. The restore's trial runs `itgraph yt baselines` beside `itgraph baselines` and reads back a new completed YouTube run.

### 17. Docs

`docs/PLAN.md`, YouTube: the measurements of the Context, the decisions, and the replay's table of volume against threshold once priced; the change 7 row. `src/itgraph/README.md`: the two commands, the replay, what the summary's counts mean. `src/itgraph/CLAUDE.md`: rows for the new modules. The runbook: the two CronJobs and the new series. `docs/*.html` is not touched.

## Risks / Trade-offs

- **[Two weeks of data price the first threshold]** → order of magnitude, not tuning: the replay sees about 30 English and 10 Russian crossings. The cap of 10 a day per topic bounds a mistake; calibration follows as its own change after six to eight weeks.
- **[The fallback carries most channels for months]** → counted in every summary and in the series; the bias of the 120-day window measured before calibration.
- **[Comments scored on few channels]** → accepted, as on Telegram; a settled fallback for counts is calibration's question.
- **[Format by duration misfiles some videos]** → a short video that is not a Short is scored among short-form; a Short over 180 s cannot exist.
- **[The view counter lags]** → no band before three hours; the spread per band absorbs what remains.
- **[Batches of talks]** → one message per channel per 24 hours by folding; the folds are reported.
- **[The refactor moves Telegram]** → characterisation test first, then the comparison on a restored copy (Decision 12).
- **[A test environment raises alerts from restored readings]** → into its own chat, once each; what production raised stays closed.

## Migration Plan

1. **(operator) The one-off pass,** now that b5b0c15 runs in production: the past uploads read at once, each answer stored as a reading. The console runs `itgraph <args>` with no environment of its own, so the refresh age cannot be lowered for one run through `scripts/on-host.sh`. Decided by the operator: by hand on the host, on the running image, as a Job made from the scheduled refresh with the refresh age set to a day —

   ```
   kubectl -n <namespace> create job yt-refresh-once --from=cronjob/itgraph-yt-refresh --dry-run=client -o yaml \
     | kubectl set env --local -f - YOUTUBE_REFRESH_AFTER_DAYS=1 -o yaml \
     | kubectl create -f -
   ```

   — the scheduled refresh's pod, egress and Secrets, with one variable, which takes precedence over the ConfigMap's. Outside the refresh's own hour, since a Job made by hand is not under the CronJob's `Forbid`; deleted once read. About 1,700 units from the general pool: the year of past uploads (about 1,010), and the channel payloads and featured lists older than a day, which it renews too. The next refresh cycle of past uploads moves to 25 days after it.
2. Implement, `make validate` and `make test-e2e`; the revision verified on a `_test` database.
3. **(operator)** A release; the role in the test environment with a restore from a fresh full dump; `itgraph yt score --replay --since 14` there, then the threshold and the comment minimum written into `defaults/main.yml` and `config.py`; Telegram's comparison under both images (Decision 12).
4. **(operator)** A release carrying those values, through the test environment, then pinned in production; the first release, unpriced, never reaches production. The first `yt baselines` runs at 05:30; until it completes, `yt score` reports no baselines and raises nothing.
5. Rollback: revert the pin. The new tables stay unread; the downgrade drops them.

## Open Questions

None. The one-off pass's mechanism, asked here first, was decided by the operator: by hand on the host (Migration Plan, 1), rather than an option of `yt refresh` reading past uploads alone, which would have needed a release first.
