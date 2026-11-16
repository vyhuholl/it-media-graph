# Tasks

Tasks marked **(operator)** reach a server, Telegram, YouTube or the test environment, and are the operator's. The agent sends no request to Telegram or YouTube, starts no bot, runs no CronJob, and points nothing at the production database.

Rules for every task:

- Every task that changes code ends on a green `make validate` (hooks through `prek`). Every task that changes the role also ends on a green `make test-e2e`.
- Migrations are verified on a scratch database whose name ends in `_test`.
- Fixtures are synthetic: fake ids are spelled as obviously fake, and no real channel, title, text or count is used. Logs name ids only.
- No value from the production database enters a file, an artifact or a commit. Aggregates only.
- Neither list's main part changes: the existing tests of both weekly lists and of their delivery stay green and unedited, except where a list now carries a section.

## 1. Schema

- [ ] 1.1 One revision adds `weekly_revivals` and `yt_weekly_revivals`, and the lists' `second_wind_shown`, `second_wind_acceleration`, `second_wind_norm_share` and `second_wind_size`, as described in design.md, Decision 5. It grants the bot's role `SELECT` and `UPDATE (delivered_at)` on both new tables; the exporter's nothing. The downgrade drops. Verify on a `_test` database:
  - upgrade, downgrade, upgrade; `alembic check` reports nothing;
  - under `SET LOCAL ROLE` the bot marks a revival delivered and is refused an insert and any change of rank, item, readings, pace, acceleration or share;
  - the exporter is refused every column of both tables;
  - removing the reading that ends a revival's week removes the revival and not its list, and on YouTube so does removing its start reading.

## 2. The pure section

- [ ] 2.1 `scoring/weeks.py` gains `Revival`, `pace_of`, `qualifies` and `rank_revivals` (design.md, Decisions 2 and 7). Verify with tests:
  - the scenarios "A post that sped up", "Ordinary decay", "Speeding up by a few views", "A post that did not grow the week before", "Too few readings", "Views taken away" and "A slow-burn channel";
  - one entry per channel with its others, the size, and the tie order.

## 3. Computing and storing it

- [ ] 3.1 `config.py` gains both platforms' four settings (design.md, Decision 4), validated. Verify with a test per refused value and the defaults.
- [ ] 3.2 `db/weekly.py` and `db/yt_weekly.py` gain the items of a week with their three readings by `LATERAL` probes (design.md, Decisions 1 and 2), and storing and claiming revivals with the list. Verify with tests on a `_test` database:
  - Telegram: an album by its first part, a chat and a post younger than the previous week left out;
  - YouTube: a Short, a broadcast and a channel out of scope left out, the language from the label;
  - a revival whose reading is gone is not claimed;
  - a second store of the week writes nothing.
- [ ] 3.3 `scoring/weekly.py` and `scoring/yt_weekly.py` compute the section after the list, with the norm from the newest run (design.md, Decision 3), store it in the list's transaction with the switch's state and thresholds, and print it in `--preview` and the summary. Verify with tests:
  - "A Second Wind Section Lists Older Posts That Sped Up" and its YouTube counterpart;
  - "One Second Wind Entry Per Channel, Five At Most" on both platforms;
  - "Computed in the shadow" and "A preview";
  - "A channel without a norm";
  - the main list's tests unedited.

## 4. The message and its delivery

- [ ] 4.1 `bot/render.py` gets each platform's section heading, entry and empty line (design.md, Decision 8). Verify with tests:
  - "A Second Wind Entry Shows What Sped Up";
  - "A Second Wind Entry Names Its Source And Dates What It Shows";
  - "Nothing sped up";
  - the heading kept whole with the first revival in `message_parts`;
  - the existing render tests unedited.
- [ ] 4.2 `bot/app.py`: `WeeklySource` gains the section (design.md, Decision 9). Verify with tests with a fake sender:
  - "Switched on", "Switched on after computing" and "Computed in the shadow";
  - "A second wind entry gone before sending" on both platforms;
  - a list whose main part is empty and whose section is not;
  - parts marked per section, and a failed part resumed;
  - the modified "The Bot Cannot Write Collection State" under the bot's role.

## 5. Role and restore

- [ ] 5.1 `neutralise.sql` marks undelivered revivals of both platforms delivered, and `readback.sql` fails while one remains. Verify: the suite's test that removes each statement in turn fails the read-back for each new one; tests of the modified "A Restored Copy Acts On Nobody".
- [ ] 5.2 `e2e.sh`: a list with a revival from synthetic readings, delivered with the switch on; the restore's previews print both sections. Verify: `make test-e2e`.

## 6. Docs

- [ ] 6.1 Update the docs as listed in design.md, Decision 12. Verify: no value from the production database beyond aggregates, and `docs/*.html` untouched.

## 7. Rollout

- [ ] 7.1 **(operator)** Cut a release, deploy it to the test environment, and restore. Verify: the restore's previews print both sections, and nothing of a section is sent.
- [ ] 7.2 **(operator)** Pin the release in production. Verify: the list of 23 November is sent without a section, and its revivals are stored with `second_wind_shown` false.

## Workflow follow-up

- Archive the change once 7.2 is read.
- Calibration (design.md, Decision 11): from the list of 30 November, two or three weeks of reading, then a release that sets the thresholds and switches each platform on.
