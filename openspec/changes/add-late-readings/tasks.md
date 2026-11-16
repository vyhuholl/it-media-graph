# Tasks

Tasks marked **(operator)** reach a server, Telegram, YouTube or the test environment, and are the operator's. The agent sends no request to Telegram or YouTube, runs no collector, refresh or CronJob, and points nothing at the production database.

Rules for every task:

- Every task that changes code ends on a green `make validate` (hooks through `prek`). Every task that changes the role also ends on a green `make test-e2e`.
- Migrations are verified on a scratch database whose name ends in `_test`.
- Fixtures are synthetic: fake ids are spelled as obviously fake, and no real channel, video, title or count is used. Logs name ids only.
- No value from the production database enters a file, an artifact or a commit. Aggregates only.
- Telegram's scoring and alerts do not change: `tests/test_telegram_scoring_record.py` and Telegram's suite stay green and unedited.

## 1. Schema

- [ ] 1.1 One revision adds `yt_refresh_runs` as described in design.md, Decision 9, reusing `yt_pass_outcome`, and grants the exporter's role `SELECT (finished_at, outcome)` on it. The downgrade revokes and drops. Verify on a `_test` database:
  - upgrade, downgrade, upgrade;
  - autogenerate reports nothing;
  - under `SET LOCAL ROLE` the exporter reads those two columns, and is refused `late_due`, `late_read`, `error` and any write.

## 2. Telegram: late readings from the window

- [ ] 2.1 `config.py` gains `watch_late_offsets_days` `(3, 4, 5, 7, 10, 14, 21, 30)` and `watch_late_horizon_days` `35`, with a validator: ascending, the first age past `watch_horizon_hours`, the horizon past the last age. `schedule.py` gains `late_offsets()`, `late_horizon()` and a pure `late_reading_due(published_at, newest_reading_at, now)` (design.md, Decisions 2 and 3). Verify with tests in `tests/test_schedule.py`:
  - a test per refused setting;
  - the scenarios "A post that has passed a late age is read", "Several late ages passed at once give one reading", "A post already read at its latest late age is not read again", "Past the late horizon a post is not read", and a post with no reading at all.
- [ ] 2.2 `db/metrics.py` gains the newest reading of each of a set of one channel's posts: one grouped query on the primary key, and a post with none is absent from the result. Verify with tests on a `_test` database: several readings per post, posts with none, and another channel's readings not mixed in.
- [ ] 2.3 `tg/watch.poll_channel` adds to `readings` the window's posts past the horizon that `late_reading_due` names, in the same transaction with the poll's `observed_at`. `PollOutcome` counts them and the report line says how many. Verify with tests in `tests/test_watch.py` against the mocked client:
  - the scenarios of "Posts Past The Horizon Are Read At Late Ages" and "Late Readings Cost No Request": the request's `limit` and the channel's next due moment are identical with and without posts due a late reading, and a post older than the window is not asked for;
  - the modified "Tracking ends at the horizon" and "A late reading does not replay a missed sample".
- [ ] 2.4 Scoring is untouched by late readings. On a `_test` database with synthetic readings, a Telegram baseline refresh and a scoring pass (live and replay) give results identical to the same database without late readings. Verify: the new test passes. `tests/test_telegram_scoring_record.py` is green and unedited.
- [ ] 2.5 Docs:
  - `src/itgraph/README.md`: late readings in the loop's section, and the report line.
  - `src/itgraph/CLAUDE.md`: the rows of `schedule.py`, `db/metrics.py` and `tg/watch.py`.
  - `tg/watch.py`: the comment "a number that has stopped moving" replaced by what design.md, Context says.
  
  Verify: the README names the two settings and their defaults, and no production value appears.

## 3. YouTube: late readings in the refresh

- [ ] 3.1 `yt/videos.py` gains the `Asked` value `COUNTS` (`part=statistics`, `fields=items(id,statistics(viewCount,likeCount,commentCount))`). Verify with tests: the request names exactly these parts and fields, the scenario "A late reading asks for the counts alone", and every field it names appears in `docs/privacy.html`'s list of what is read about videos.
- [ ] 3.2 `config.py` gains `youtube_late_daily_until_days` `120` and `youtube_late_weekly_interval_days` `7`, validated: the daily tier past `youtube_watch_horizon_days`, the weekly interval at least a day. `yt_schedule.py` gains a pure `late_reading_due(published_at, newest_reading_at, now)`: the interval by age, coming due three hours early, nothing past the walk's depth (design.md, Decision 7). Verify with tests in `tests/test_yt_schedule.py`:
  - a test per refused setting;
  - "A young past upload is read daily", "An older past upload is read weekly", "A daily run does not slip a day", and the boundary at 120 days.
- [ ] 3.3 `db/yt_videos.py` gains the past uploads due a late reading: `past_uploads_due_for_refresh`'s predicate without the payload's age, each with its newest reading by the primary key, filtered by `late_reading_due`. It also gains storing counts as readings without touching the payload. Verify with tests on a `_test` database: "What the refresh does not keep is not read", "Every format is read alike", and "The stored data is not replaced" (payload and `fetched_at` unchanged; absent counts stored as absent).
- [ ] 3.4 `yt/refresh.py` gains the late step, last, after past uploads' data. Videos go fifty to a call through `by_ids(asked=COUNTS)`; a missing video is removed by `remove_videos`; each call is committed before the next; a spent pool or a failure is `stopped`. `RefreshSummary` and its line gain `late_due` and `late_read`. Each run writes its `yt_refresh_runs` row at its end, in a transaction of its own. Verify with tests in `tests/test_yt_refresh.py` with a fake YouTube:
  - the scenarios "A video read once in a run is not read twice", "Videos share requests", "Nothing is read while the walk is disabled", "A past upload YouTube no longer returns" and "Exhaustion stops the readings part way";
  - the three scenarios of "Each Refresh Is Recorded";
  - the ledger books the late step under the general pool and the `refresh` command.
- [ ] 3.5 Nothing reads counts from a payload. Check every reader of `statistics` in `src/` (scoring, alerts, the weekly list, the bot's evidence) and confirm each reads `yt_raw_video_stats`, or reads the payload only to write a reading. Verify: list the readers found in the task's note. Add a test where a payload's `statistics` disagree with its newest reading, and an alert's evidence and a weekly entry show the reading.
- [ ] 3.6 `scoring/yt_refresh._settled` takes each video's earliest reading inside the window, and its docstring says why (design.md, Decision 8). Verify with tests: "One reading per settled video" (40 days, not 90) and "Daily readings do not age the reference"; a replay with `--late-settled` takes the earliest reading up to its bound; the YouTube scoring suite is otherwise unedited.
- [ ] 3.7 Docs:
  - `src/itgraph/README.md`: the refresh's late step, its cadence, its report line and its record.
  - `src/itgraph/CLAUDE.md`: the rows of `yt_schedule.py`, `yt/videos.py`, `yt/refresh.py`, `db/yt_videos.py` and `scoring/yt_refresh.py`.
  - The `yt refresh` command's help text in `cli.py`.
  
  Verify: `uv run itgraph yt refresh --help` shows the new help text, and no production value appears.

## 4. Series

- [ ] 4.1 `metrics/series.py` gains `itgraph_youtube_last_complete_refresh_timestamp_seconds`: the `finished_at` of the newest `complete` run, 0 when none. Verify with tests of the modified "The Series Come From What Is Already Recorded" ("A refresh that stopped for quota", "No refresh yet") and "Metrics Carry Nothing YouTube Said" ("The role cannot read a refresh's counts"), under the exporter's role in `tests/test_metrics_role.py`. Add the row to the README's table of series.

## 5. Plan and integration

- [ ] 5.1 Update `docs/PLAN.md` as listed in design.md, Decision 12:
  - the measurements of 2026-11-16, as aggregates;
  - the correction of Telegram's "then never again";
  - the YouTube cadence past 28 days;
  - changes A, B and C in the order table.
  
  Verify: no value beyond aggregates, and `docs/*.html` untouched.
- [ ] 5.2 Integration: `make validate` and `make test-e2e` are green on the whole change. `uv run alembic upgrade head` on a `_test` database matches the models. Verify: both commands pass, and the role's rendered CronJobs are unchanged against `master` (`tests/test_deploy_role.py`).

## 6. Rollout

- [ ] 6.1 **(operator)** Cut a release, deploy the role to the test environment, and restore from a fresh full dump. Verify: the revision applies, and the new series is served (0, or the copy's moment).
- [ ] 6.2 **(operator)** Settle the site's wording in `docs/privacy.html` (design.md, Decision 12), and decide on the retention sentences (design.md, Open Questions). Verify: the page states how often counts are read before production reads them so.
- [ ] 6.3 **(operator)** Pin the release in production, and ask the infrastructure repository for the alert and panel of design.md, Decision 9. Verify:
  - the first refresh's line shows about 52 thousand late readings due and taken, for about 1,050 units of the general pool;
  - the watch loop's line counts late readings;
  - the series shows the refresh complete.
- [ ] 6.4 Two days after the pin, read the production database in aggregates. Verify:
  - Telegram late readings per day are under 5 thousand after the first day;
  - YouTube late readings per day are about 20 thousand, and the general pool spends about 400 units;
  - no Telegram poll's request size or count changed (`flood_events` and polls a day against the week before).

## Workflow follow-up

- Archive the change once 6.4 is read.
- Start `add-telegram-weekly-list` (B) at any time. Start `add-second-wind` (C) after two to four weeks of late readings, with an explore on them.
