# Tasks

Tasks marked **(operator)** reach a server, YouTube, a chat or the test environment, and are the operator's: the agent sends no request to YouTube or the Bot API, starts no bot and runs no CronJob. Every task that changes code ends on a green `make validate` (hooks through `prek`); every task that changes the role also on `make test-e2e`. Migrations are verified on a scratch database whose name ends in `_test`, never on the working one. Fixtures are synthetic: no real channel or video id, title or count, and fake ids spelled as obviously fake. Logs name ids only. No value from the production database enters a file, an artifact or a commit — aggregates only.

## 0. Settled readings before anything scores

- [x] 0.1 **(operator)** The one-off pass of past uploads: a Job made from `itgraph-yt-refresh` with `YOUTUBE_REFRESH_AFTER_DAYS=1`, outside the refresh's hour, deleted once read (design.md, Migration Plan, 1). Verify, as `itgraph_reader`: past uploads of seeds with a reading taken since the pass, against the year's count; the general pool's spending that day.

## 1. Telegram, pinned before it is touched

- [x] 1.1 A characterisation test: on a synthetic fixture of Telegram posts and readings — several kinds, a thin kind, comments with zeros, an album, a burst, a channel on the fallback — record `refresh_baselines` at a fixed `now` (every curve point, factor, spread, band spread, dispersion, reference and its kind) and `run_scoring` in replay (every spike, its crossing and fold), as data in the test. Verify: green on today's code before anything in group 2 is changed.
- [x] 1.2 A hidden `--now` on `itgraph baselines` and `itgraph score` (design.md, Decision 12). Verify: without it the commands behave as before; with it a refresh's `completed_at` and a replay's window are that moment.

## 2. `scoring/`, platform-blind

- [x] 2.1 `AgeBands` in `curves.py`; `TELEGRAM_BANDS` from today's edges, `REFERENCE_BAND` and `REFERENCE_SETTLED` kept as Telegram's for their callers; `Curve` carries its bands; `band_of`, `reference_at`, `Baseline.spread_at` and `expected` read them. Verify: 1.1 and Telegram's suite unchanged; band names identical to those stored.
- [x] 2.2 Generic keys (PEP 695) through `Observation`, `Spike`, `Head`, `Placed`, `fold`, `fit_factor`; `burst_heads` keeps Telegram's integer keys, YouTube having no bursts (design.md, Decision 10); `Spike` gains the moment of the reading its score came from, unset for Telegram. Verify: `make typecheck` with no new ignore; 1.1 unchanged; a test folding string-keyed spikes.
- [x] 2.3 The fitting core to a pure `scoring/fit.py` — `_fit`, `_residuals`, `_dispersion`, `_rolling`, `_recent_reference` — and `_best_per_post` beside it, every floor and setting an argument; Telegram's `refresh.py` and `run.py` pass what they read today. Verify: 1.1 unchanged; `git grep settings src/itgraph/scoring/fit.py` finds nothing.

## 3. What a video is

- [x] 3.1 `derive/yt_videos.py`: the duration in seconds from `contentDetails.duration`, `None` where unreadable; the format — `short`, `long`, or none for a video with `liveStreamingDetails` or no duration (design.md, Decision 2). Verify: tests of "Formats Are Scored Apart" and "Broadcasts Are Not Scored" — 40 s, 150 s, 180 s, 181 s, an hour, `P0D`, a day-long `P1DT2H`, a premiere, a missing duration.

## 4. Schema

- [x] 4.1 One revision: types `yt_metric`, `yt_video_format`; tables `yt_baseline_runs`, `yt_channel_baselines`, `yt_metric_baselines`, `yt_curve_points` as design.md, Decision 8, reusing `baseline_reference` and `yt_channel_kind`; the exporter's role `SELECT` on `completed_at`, `channels_in_scope`, `channels_with_baseline` of `baseline_runs` and `yt_baseline_runs`. The downgrade revokes and drops. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; under `SET LOCAL ROLE` the exporter reads those columns and is refused a median, a curve point and a factor; `itgraph_reader` reads all four tables.

## 5. The YouTube refresh

- [x] 5.1 `db/yt_baselines.py`: per metric and format, the readings of scored videos published within the window, with kind, channel, age and publication; each channel's 1260m values per format, oldest first; the settled medians — one reading per video, the latest in the window; storing a run; loading the newest complete one. Verify: tests on a `_test` database — `other`, unlabelled and non-seed channels absent; broadcasts absent; one settled reading per video, the latest; a counter absent stays absent; an interrupted run unread.
- [x] 5.2 `scoring/yt_refresh.py`: Decisions 5–7 through `scoring/fit.py` with `YOUTUBE_BANDS`; comments with no fallback; the summary per format — fitted, borrowed, skipped, channels on each reference. `itgraph yt baselines [--now]`. Verify: tests of "The Reference Is The Channel's Newest Videos Where It Has Enough", "Too Few Newest Videos Fall Back To The Settled Ones", "Baselines Are Stored Per Run And Carry Their Parameters" and "Comments Are Scored As Counts"; a run on a database with no reading completes with everything skipped and exits zero.

## 6. The YouTube scoring pass

- [x] 6.1 `scoring/yt_run.py`: the readings of scored videos published within 8.5 days up to `now`; best per video across readings and signals, the comment minimum first; crossing; already raised per kind; fold against the open heads of the last 24 hours by the video's current channel; the summary of Decision 9. Verify: tests of "A Video Is Measured Against Its Own Channel, Format And Age", "Each Signal Is Scored Against Its Own Curve", "Age Comes From The Reading, In Contiguous Bands", "One Video Raises One Alert Per Signal", "A Channel's Spikes Within A Window Are One Event", "A Channel Without Enough History Is Not Scored, And Is Reported".
- [x] 6.2 Raising in `db/yt_alerts.py`: kind, `scored_at` the best score's reading, band 1, value, `expected` for comments, `folded_into`; the unique constraint absorbs a second pass. Verify: tests that the alert names a stored reading, that a second pass writes nothing, and that Telegram's `alerts` gets no row; the message of change 6 renders a raised alert of each kind, a comment alert with its count.
- [x] 6.3 `itgraph yt score [--threshold] [--now]`. Verify: tests of "Scoring Reads Only Collected Data" — no request, no quota row, no key needed; only `yt_alerts` changes.

## 7. Replay

- [x] 7.1 `--replay [--since] [--list]`: each day's baselines refitted in memory as of its start, Moscow time, with the refresh's code; the day's readings scored against them; folds against none; the report per language and day, per signal and format, and the gaps between one channel's crossings; each crossing followed to its newest reading — held, faded, too early; several `--threshold` from one fit, and `--comments-min`. Verify: tests of "Scoring Can Be Replayed Without Writing" — no run and no alert written; a replay of the present names the live pass's videos; a video on day five scored against day five's fit.

## 8. Settings

- [x] 8.1 `config.py`: the settings of design.md, Decision 13, validated like Telegram's counterparts — the settled window's ends ordered, the recent minimum not above the newest count, the fold window and threshold positive, the metrics not empty. Verify: a test per refusal.
- [ ] 8.2 **(operator)** The default threshold and comment minimum from 12.2's replay, written into `config.py` and `defaults/main.yml`, with the replay's table of volume against threshold per language into `docs/PLAN.md`. Verify: the schedule and configuration tests hold the new values.

## 9. Series

- [x] 9.1 `metrics/series.py`: `itgraph_baselines_completed_timestamp_seconds{platform}` and `itgraph_baselines_channels{platform,state}` from the newest completed run of each platform, 0 without one. Verify: tests of the modified "The Series Come From What Is Already Recorded" and "Metrics Carry Nothing YouTube Said", including a run in progress after a completed one, under the exporter's role.

## 10. Role

- [x] 10.1 `itgraph-yt-score` and `itgraph-yt-baselines` in `__itgraph_cronjobs_always`, labelled for the database alone, config and database URL only; `itgraph_yt_score_schedule`, `itgraph_yt_baselines_schedule` and `itgraph_youtube_alert_spike_z` in `defaults/main.yml`; `YOUTUBE_ALERT_SPIKE_Z` in the ConfigMap. Verify: `tests/test_deploy_role.py` holds design.md, Decision 15's table; tests of "YouTube Scoring Runs In Every Environment" and the modified secrets and egress requirements; `make test-e2e`.
- [x] 10.2 The restore's trial runs `itgraph yt baselines` and reads back a new completed YouTube run; the report says so. Verify: `make test-e2e` asserts the run and the report line; a stale copy's YouTube passes exit zero.
- [x] 10.3 `e2e.sh`: the two CronJobs rendered without a YouTube key; a scoring pass on synthetic readings raising one folded event into the test chat's topic. Verify: `make test-e2e`.

## 11. Docs

- [x] 11.1 `docs/PLAN.md`, YouTube: what Context measured, the decisions, the change 7 row. `src/itgraph/README.md`: the two commands, the replay, the summaries. `src/itgraph/CLAUDE.md`: rows for `scoring/fit.py`, `scoring/yt_refresh.py`, `scoring/yt_run.py`, `db/yt_baselines.py`, and the changed rows. `deploy/runbook.md`: the CronJobs and the series. Verify: no value from the production database beyond aggregates; `docs/*.html` untouched.

## 12. Rollout

- [ ] 12.1 **(operator)** A release; the role in the test environment; a restore from a fresh full dump. Verify: the restore's report names the YouTube baseline run; the series of both platforms served.
- [ ] 12.2 **(operator)** In the test environment: `itgraph yt baselines`, then `itgraph yt score --replay --late-settled --since <days from 2026-11-02> --threshold 3.3 --threshold 3.7 --threshold 4.1 --threshold 4.5 --threshold 5.0`, and the same with `--comments-min 5` and `20`; the output brought back. Verify: the agent picks the lowest threshold whose volume is inside about 2–5 a day in English and 1–2 in Russian and whose crossings mostly held, and says why; the operator's decision recorded.
- [ ] 12.3 **(operator)** Telegram unchanged: on one restored copy, `itgraph baselines --now T` and `itgraph score --replay --now T --since 7` under the previous image and this one. Verify: the runs' rows equal but for ids and moments; the listings identical.
- [ ] 12.4 **(operator)** A release carrying 8.2's values, through the test environment, then pinned in production; the first release, whose threshold is not yet priced, goes to the test environment alone. Verify: the first `yt baselines` completes and the series shows it; the first day's YouTube alerts per topic against 12.2's volume.
