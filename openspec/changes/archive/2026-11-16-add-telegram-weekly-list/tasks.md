# Tasks

Tasks marked **(operator)** reach a server, Telegram or the test environment, and are the operator's. The agent sends no request to Telegram, starts no bot, runs no CronJob, and points nothing at the production database.

Rules for every task:

- Every task that changes code ends on a green `make validate` (hooks through `prek`). Every task that changes the role also ends on a green `make test-e2e`.
- Migrations are verified on a scratch database whose name ends in `_test`.
- Fixtures are synthetic: no real channel id, username, title, text or count, and fake ids spelled as obviously fake. Logs name ids only.
- No value from the production database enters a file, an artifact or a commit. Aggregates only.
- Telegram's scoring and alerts and YouTube's weekly list do not change: `tests/test_telegram_scoring_record.py` and YouTube's weekly list tests stay green and unedited.

## 1. Schema

- [x] 1.1 One revision adds `weekly_lists` and `weekly_entries` as described in design.md, Decision 5. It reuses the `metric` type, references the entry's reading in `message_metrics` with `ON DELETE CASCADE`, and grants the bot's role `SELECT` on both tables and `UPDATE` on the delivery columns, and the exporter's role `SELECT` on `computed_at` and `delivered_at` of the lists. The downgrade revokes and drops. Verify on a `_test` database:
  - upgrade, downgrade, upgrade; `alembic check` reports nothing;
  - under `SET LOCAL ROLE` the bot marks a list and an entry delivered and counts an attempt, and is refused an insert and any change of rank, post, reading, signal, count or score;
  - the exporter reads its two columns and is refused the week, the counts and every entry column;
  - a second list for one week is refused, and removing a reading removes its entry and not its list.

## 2. The week, shared

- [x] 2.1 `scoring/weeks.py` gets `WEEK`, `WeekRefused`, `week_bounds`, `last_ended_week`, `check_week`, `merge_best` and a `rank_week` generic over the post key, moved out of `scoring/yt_weekly.py`. `scoring/yt_weekly.py` imports them and keeps exporting the same names. Verify:
  - YouTube's weekly list tests pass unedited;
  - a test of `scoring/weeks.py` ranks integer keys (a Telegram post) with the tie order of design.md, Decision 1.

## 3. Scoring a week

- [x] 3.1 `db/baselines.py` gains `runs_in_force(session, since, until)` and `load_baselines(session, run_id=None)`; `current_run` and today's callers are unchanged. Verify with tests on a `_test` database:
  - a run in progress is never returned;
  - the run before a period is included;
  - `load_baselines` with a run reads that run's medians, rulers and curves, and without one reads the newest as before.
- [x] 3.2 `scoring/run._load` becomes `load_readings`, its behaviour unchanged. Verify: the scoring suite and `tests/test_telegram_scoring_record.py` pass unedited.
- [x] 3.3 `db/weekly.py` stores a week's list whole, or nothing when the week has one (told so by the unique key in a savepoint). It also gives the undelivered lists, oldest week first, with their undelivered entries in rank order, and marks parts and lists delivered or a failed attempt with its error. Verify with tests on a `_test` database:
  - a second store writes nothing;
  - an entry whose reading was removed is not returned;
  - the order of lists and entries.
- [x] 3.4 `scoring/weekly.py` computes a week's list:
  - the week of a moment in `watch_timezone`, a `--week` refused unless it is the Monday of an ended week;
  - the week's readings through `load_readings`, cut at each run's completion and each cut scored by `best_per_post` against its run, with the alerts' signals, threshold and comment minimum;
  - the best across cuts by `merge_best`, then `rank_week`, the size, and the summary of design.md, Decision 4.
  
  Verify with tests of "A List For Each Week That Has Ended", "A Week's List Is Computed Once", "A Post Is Scored As The Live Pass Scored It" and "One Entry Per Channel, At Or Above The Alerts' Threshold". Also verify that a post scored here and by `run_scoring` on the same reading and run gets the same score.
- [x] 3.5 `itgraph weekly [--week] [--preview] [--now]` and the setting `weekly_list_size` (10), validated. Verify with tests:
  - "An Empty Week Is Said" (stored);
  - "Computing A List Reads Only Collected Data": no client built, no lease taken, only the list tables changed, `--preview` changing nothing;
  - a refused size.

## 4. The message and its delivery

- [x] 4.1 `bot/render.py` gets Telegram's list header, an entry and the empty list (design.md, Decisions 6 and 7), through the existing `message_parts`. Verify with tests:
  - "Each Entry Shows What An Alert Shows": a link only with a username, the first line cut at 120 with «…», a comment entry's count and usual count, no count of views, reactions or forwards;
  - "A List States Its Week And Whose Score It Shows" and "An Empty Week Is Said" (the message);
  - ten long entries split with every entry whole and every part within 4,096;
  - YouTube's render tests unedited.
- [x] 4.2 `bot/app.py`: the weekly lists of both platforms behind one interface (design.md, Decision 8); YouTube's source unchanged, Telegram's for the Telegram destination with evidence from `db/alerts.alert_evidence`. Verify with tests with a fake sender:
  - "A List Is Delivered Once, Whatever Its Length";
  - "A List Is Neither Capped Nor Sent In Quiet Hours";
  - "Not to YouTube's chat";
  - the modified "The Bot Cannot Write Collection State" under the bot's role;
  - YouTube's weekly delivery tests and the alert delivery's tests unedited.

## 5. Series

- [x] 5.1 `metrics/series.py` gets the two series of design.md, Decision 9, 0 without a list. Verify with tests of the modified "The Series Come From What Is Already Recorded" and "Metrics Carry Nothing YouTube Said", under the exporter's role. Add both to the README's table of series.

## 6. Role

- [x] 6.1 `itgraph-weekly` goes in `__itgraph_cronjobs_always`, labelled for the database alone, with the configuration and the database's URL only; `itgraph_weekly_schedule` is `0 10 * * 1` in `defaults/main.yml`; starting deadline 518,400, active 1,800. Verify:
  - `tests/test_deploy_role.py` holds the schedule and deadlines;
  - tests of "Telegram's Weekly List Runs In Every Environment";
  - `make test-e2e`.
- [x] 6.2 `neutralise.sql` marks undelivered Telegram lists and entries delivered, and `readback.sql` fails while one remains. Verify: the suite's test that removes each statement in turn fails the read-back for each new one; tests of the modified "A Restored Copy Acts On Nobody".
- [x] 6.3 The restore's trial runs `itgraph weekly --preview`, checks that no list was written, and reports it. Verify:
  - `make test-e2e` asserts the report line;
  - a stale copy's pass exits zero with an empty list ("A Stale Copy Does Not Fail The Passes").
- [x] 6.4 `e2e.sh`: the CronJob rendered in an environment that does not collect; a weekly pass with `--week` and `--now` on synthetic readings writes the expected entry; the bot delivers the list to the stub chat without a preview. Verify: `make test-e2e`.

## 7. Docs

- [x] 7.1 Update the docs as listed in design.md, Decision 12: `docs/PLAN.md`, `src/itgraph/README.md`, `src/itgraph/CLAUDE.md`, `deploy/runbook.md`. Verify: no value from the production database beyond aggregates, and `docs/*.html` untouched.

## 8. Rollout

- [x] 8.1 **(operator)** Cut a release, deploy the role to the test environment, and restore from a fresh full dump. Verify: the restore's report names Telegram's weekly trial, and both series are served.
- [x] 8.2 **(operator)** In the test environment, run `itgraph weekly --preview` for the last two ended weeks, then make a Job from `itgraph-weekly` and read the list in the test chat. Verify: the agent reads the previews against the five weeks measured (29–41 channels at the threshold, ten listed) and the message against design.md, Decision 6.
- [x] 8.3 **(operator)** Pin the release in production, and ask the infrastructure repository for the alert and panel of design.md, Decision 9. Verify: the first Monday's list is delivered to the alert chat, and the series show it.

## Workflow follow-up

- Archive the change once 8.3 is read.
- Then change C, `add-second-wind`, once two to four weeks of late readings exist, starting with an explore.
