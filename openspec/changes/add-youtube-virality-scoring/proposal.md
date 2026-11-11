# Proposal

## Why

The task as received names "what took off" on English-language and Russian-language IT YouTube. Change 6 built the YouTube alert queue, its routes and its wording, and no YouTube alert exists yet: nothing scores a video. Telegram's scoring is measured and calibrated, and none of its numbers carry over — YouTube views accrue over days, a third of the uploads are Shorts, and the first hours of a view counter lag.

## What Changes

- **A video is scored against its own channel, its format and its age**, as a Telegram post is: `expected = reference × factor × curve(age)`, `z = log(actual / expected) / spread` for views and likes, and comments as counts — negative binomial, with a minimum count, as on Telegram. Each signal against its own curve; no ratio, no combined score.
- **Two populations by format, worked out from the stored payload on reading and never stored per video:** short-form (no broadcast, 180 s or less) and long-form (no broadcast, longer). Streams and premieres are not scored, and the pass counts them.
- **A channel's reference** is the median 24-hour value of its newest videos of that format, where it has enough; otherwise the median of its settled videos — one reading each, 29 to 120 days old — scaled by its kind's factor. Comments take no settled fallback, as on Telegram.
- **Curves and spreads per channel kind and format**, borrowed from the format's pool where a kind is too thin, spreads per age band with a floor of distinct videos. The bands are contiguous, around the poll loop's reading ages, from 3 hours to 8.5 days; readings outside them raise nothing.
- **Baselines are stored per run, refreshed daily**, in tables of YouTube's own; a run is read only once complete.
- **The scoring pass raises into `yt_alerts`:** one alert per video and kind, under its best metric; a channel's spikes within 24 hours are one event, folded into the first. Seeds labelled `en` or `ru` only.
- **Replay** is the same pass at an earlier moment, writing nothing, refitting the baselines in memory for each day it scores, and reporting the volume per language and the folds. The threshold and the comment minimum are YouTube's own settings, priced by it before the first deploy.
- **`scoring/` becomes platform-blind** — its age bands and keys are parameters — and Telegram's scoring and alerts do not change: proven by Telegram's suite as it is, by a characterisation test recorded before the refactor, and by the old and the new image's runs compared on one restored copy.
- **Operations:** two scheduled passes, `yt score` every 15 minutes after the poll loop and `yt baselines` daily after the refresh, reaching the database alone and running in every environment, since they need no key; two series for the shared monitoring, of both platforms' baseline runs; the restore's trial makes a YouTube baseline run too.
- **Docs:** `docs/PLAN.md`'s YouTube section records what was measured and decided; `src/itgraph/README.md` and the module map describe the commands. `docs/*.html` is not touched.

**Out of scope:**

- A periodic list of what took off, per language: a change of its own, next (the operator's decision).
- Calibration of the threshold, the minimum and the fold window on weeks of production data: a change of its own, as `calibrate-virality-scoring` was.
- Streams and premieres; a settled fallback for comments; a long-age curve normalising settled readings.
- The one-off read of past uploads that makes settled readings exist before the refresh cycle would: an operator's pass by hand, on the running image, before this change is deployed (design.md, Migration Plan).

## Capabilities

### New Capabilities

- `youtube-virality-scoring`: how a YouTube video is judged to be doing unusually well for its channel, format and age — formats, references, curves, spreads, the scoring pass, folding, thresholds and replay.

### Modified Capabilities

- `collection-monitoring`: two series of the baseline runs of each platform; what the exporter may read of them.
- `collection-deploy`: the YouTube scoring passes are analysis jobs — the database's URL and nothing else, no internet — and run whatever the YouTube key.
- `database-backups`: the restore's trial makes a YouTube baseline run beside Telegram's; a stale copy fails neither YouTube pass.

## Impact

- **Code:** `scoring/` (bands and keys as parameters, the fitting core pure), new `scoring/yt_refresh.py` and `scoring/yt_run.py`, `derive/yt_videos.py` (format), new `db/yt_baselines.py`, `db/yt_alerts.py` (raising), `db/models.py`, `config.py`, `cli.py` (`yt baselines`, `yt score`; a hidden `--now` on Telegram's `baselines` and `score`), `metrics/series.py`. No new dependency.
- **Schema:** one Alembic revision: `yt_baseline_runs`, `yt_channel_baselines`, `yt_metric_baselines`, `yt_curve_points`, the types `yt_metric` and `yt_video_format`, and the exporter's column grants on both platforms' run tables.
- **Role:** two CronJobs, their schedules in the schedule test, the restore's trial, `e2e.sh`.
- **Infrastructure repository:** alert rules and a panel on the two new series.
- **Production:** YouTube alerts begin, in both topics. Telegram's alerts do not change.

## Decision record

Brought as twelve questions in the explore session of 2026-11-11, on the production database's aggregates: readings since 2026-11-01 only, a week of the poll loop's having been lost in the move to the server, when dumps carried no YouTube rows; no settled reading of any past upload, the history walk having asked for none. The operator's answers:

> 1. б. Список потом отдельным change.
> 2a. б. b5b0c15 уже задеплоен в релизе v0.4.0.
>
> Остальное как рекомендуешь. Отдельный чат у тестового окружения есть.

Then, to the one follow-up question — the one-off pass by hand on the host, today, or through an option of `yt refresh` and a release: «Первый.»

Confirmed as of 2026-11-11:

1. "What took off" is both alerts and a periodic list per language; the list is a change of its own, after this one.
2. Settled readings of past uploads are made at once by a one-off pass, now that b5b0c15 runs in production, rather than waiting for the refresh cycle; `yt_raw_video_stats` is not thinned.
3. The channel's reference: its own newest videos at 24 hours where it has enough — 20 newest, at least 10, none older than 180 days; otherwise the settled median in [29; 120] days, at least 10 videos, scaled by the kind's factor.
4. Views and likes as log-ratios, comments as negative-binomial counts with a minimum of their own; a counter YouTube did not report is absent, never zero.
5. Short-form (180 s or less, no broadcast) and long-form scored apart; streams and premieres not scored, and counted.
6. Curves per channel kind and format, borrowed from the format's pool where a kind is thin; language is not a dimension of the curve; the band floor is 100 distinct videos.
7. One threshold for both languages, YouTube's own, priced by replay; target volume about 2–5 alerts a day in English and 1–2 in Russian.
8. A channel's spikes within 24 hours are one event; no burst collapse.
9. `scoring/` shared, Telegram's results unchanged, proven by tests and by runs compared on a restored copy.
10. Baselines stored per run, as Telegram's.
11. The scoring passes as CronJobs of their own, running in every environment; the test environment has its own YouTube chat; baseline-run series for both platforms; nothing new for the restore to neutralise.
12. Alerts raised from 3 hours to 7 days of age.
13. The one-off pass is run by hand on the host, on the running image, with the refresh age lowered for that run (design.md, Migration Plan).

**Not covered by the requirements** — chosen in `design.md`, not by the operator:

- The bands' edges and names, and the reference band of 21–30 hours (design.md, Decision 4).
- One settled reading per video, the latest inside the window (design.md, Decision 5).
- Comments take no settled fallback, as on Telegram, so they are scored only on channels with a reference of their own (design.md, Decision 6).
- An alert names the reading its best score came from, not the reading at which it first crossed (design.md, Decision 9).
- The replay refits baselines in memory for each day it scores (design.md, Decision 11).
- The names of the two series, and that the counts they read are stored on the run row (design.md, Decision 14).
- The one-off pass as a Job made from the scheduled refresh, rather than a command run in another pod (design.md, Migration Plan).
