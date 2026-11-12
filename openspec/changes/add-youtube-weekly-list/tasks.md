# Tasks

Tasks marked **(operator)** reach a server, YouTube, a chat or the test environment, and are the operator's: the agent sends no request to YouTube or the Bot API, starts no bot and runs no CronJob. Every task that changes code ends on a green `make validate` (hooks through `prek`); every task that changes the role also on `make test-e2e`. Migrations are verified on a scratch database whose name ends in `_test`, never on the working one. Fixtures are synthetic: no real channel or video id, title or count, and fake ids spelled as obviously fake. Logs name ids only. No value from the production database enters a file, an artifact or a commit — aggregates only. Telegram's scoring and alerts do not change: `tests/test_telegram_scoring_record.py` and Telegram's suite stay green and unedited.

## 1. Schema

- [x] 1.1 One revision: `yt_weekly_lists` and `yt_weekly_entries` as design.md, Decision 5, reusing `yt_alert_route` and `yt_metric`; the entry's reading a foreign key into `yt_raw_video_stats` with `ON DELETE CASCADE`, as `yt_alerts`'; the bot's role `SELECT` on both and `UPDATE` on `delivered_at`, `attempts`, `last_error` of the lists and `delivered_at` of the entries; the exporter's role `SELECT` on `route`, `computed_at`, `delivered_at` of the lists. The downgrade revokes and drops. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; under `SET LOCAL ROLE` the bot marks a list and an entry delivered and is refused an insert and a change of rank, video or score, and the exporter reads its three columns and is refused an entry and a list's counts; a second list for one week and route refused; removing a reading removes its entry and not its list.

## 2. Reading what the list needs

- [x] 2.1 `db/yt_baselines.py`: a completed run's baselines by id, and the runs completed within a period with the newest before it. Verify: tests — a run in progress never returned; the run before a period included; the newest-run loader unchanged.
- [x] 2.2 `db/yt_weekly.py`: storing a week's lists whole, or nothing if the week has them; the undelivered lists of a route, oldest week first, with their undelivered entries in rank order; marking entries and a list delivered, a failed attempt with its error; for rendering, each entry's title, channel title, publication, format, and newest stored reading with its moment, read from the payloads at sending. Verify: tests on a `_test` database — a second store writes nothing; an entry whose reading was removed is not returned; the format worked out from the payload; a counter absent stays absent.

## 3. Computing the list

- [x] 3.1 `scoring/yt_weekly.py`: the week of a moment in `youtube_alert_timezone`, a `--week` refused unless the Monday of an ended week; the week's videos of channels in scope, their readings up to the moment cut at each run's completion through `yt_run._prepare`, each cut scored by `yt_run._best` against its run with the alerts' signals and comment minimum, a video's best across cuts; the floor, one entry per channel with its others, the ranking and its ties, the size; the language from the label at computing; the summary of design.md, Decision 10. Verify: tests of "A List Per Language For Each Week That Has Ended", "A Week's Lists Are Computed Once", "A Video Is Scored As The Live Pass Scored It" and "One Entry Per Channel, The Best Above A Floor"; a video scored here and by `run_yt_scoring` on the same reading and run gets the same score.
- [x] 3.2 `itgraph yt weekly [--week] [--preview] [--now]` and the settings `youtube_weekly_list_z` (2.0) and `youtube_weekly_list_size` (10), validated. Verify: tests of "An Empty Week Is Said" (stored), "Computing A List Reads Only Collected Data" — no request, no quota row, no key, only the list tables changed, `--preview` changes nothing — and a test per refused setting.

## 4. The message and its delivery

- [x] 4.1 `bot/render.py`: the list's header, an entry, the empty list, design.md, Decisions 6 and 7; `digest_parts` with its headers as arguments, the digest's own unchanged. Verify: tests of "Each Entry Names Its Source And Dates What It Shows", "A List States Its Week And Whose Score It Shows" and "An Empty Week Is Said" (the message); a list of ten long titles split with every entry whole and every part within 4,096; the digest's tests unedited.
- [x] 4.2 `bot/app.py`: per YouTube destination and pass, the undelivered lists of its route — outside quiet hours, outside the cap and the digest, in parts, claim → send → mark per part, a failed part an attempt on the list; `Sender.send` with the link preview off for a list, mapped in `bot/handlers.py` to aiogram's options (checked against aiogram's documentation). Verify: tests of "A List Is Delivered Once, Whatever Its Length" and "A List Is Neither Capped Nor Sent In Quiet Hours" with a fake sender, and of the modified "The Bot Cannot Write Collection State" under the bot's role; the alert delivery's tests unedited.

## 5. Series

- [x] 5.1 `metrics/series.py`: the two series of design.md, Decision 9, per route, 0 without a list. Verify: tests of the modified "The Series Come From What Is Already Recorded" and "Metrics Carry Nothing YouTube Said", under the exporter's role.

## 6. Role

- [x] 6.1 `itgraph-yt-weekly` in `__itgraph_cronjobs_always`, labelled for the database alone, config and database URL only; `itgraph_yt_weekly_schedule` `0 10 * * 1` in `defaults/main.yml`; starting deadline 518,400, active 1,800. Verify: `tests/test_deploy_role.py` holds the schedule and deadlines; tests of the modified "YouTube Scoring Runs In Every Environment"; `make test-e2e`.
- [x] 6.2 `neutralise.sql` marks undelivered lists and entries delivered; `readback.sql` fails while one remains. Verify: the suite's test removing each statement in turn fails the read-back for each new one; tests of the modified "A Restored Copy Acts On Nobody".
- [x] 6.3 The restore's trial runs `itgraph yt weekly --preview` and reports it; the fake `kubectl` of the role's run tests answers its pod. Verify: `make test-e2e` asserts the report line and that no list was written; a stale copy's weekly pass exits zero with empty lists.
- [x] 6.4 `e2e.sh`: the CronJob rendered without a YouTube key; a weekly pass with `--week` and `--now` on synthetic readings writes a list per route with the expected entry. Verify: `make test-e2e`.

## 7. Docs

- [x] 7.1 `docs/PLAN.md`, YouTube: the weekly list, the replayed week's table, the change 8 row. `src/itgraph/README.md`: `yt weekly`, its summary and preview. `src/itgraph/CLAUDE.md`: rows for `scoring/yt_weekly.py` and `db/yt_weekly.py`, the changed rows of `db/yt_baselines.py`, `bot/render.py` and `bot/app.py`. `deploy/runbook.md`: the CronJob, the series, a list that did not go out. Verify: no value from the production database beyond aggregates; `docs/*.html` untouched.

## 8. Rollout

- [x] 8.1 **(operator)** A release; the role in the test environment; a restore from a fresh full dump. Verify: the restore's report names the weekly list's trial; both weekly series served, 0.
- [x] 8.2 **(operator)** In the test environment: `itgraph yt weekly --preview` for the newest ended week, then a Job made from `itgraph-yt-weekly`; the two lists read in the test chat's topics. Verify: the agent reads the preview's output against the replay's volume and the message against design.md, Decision 6; both series show the lists delivered.
- [x] 8.3 **(operator)** The site's sentence on the weekly list and its status line (design.md, Decision 12). Verify: the site names the weekly list before production gets it.
- [ ] 8.4 **(operator)** The release pinned in production; the infrastructure repository asked for the alert and the panel of design.md, Decision 9. Verify: the first Monday's lists delivered in both topics, and the series show them.
