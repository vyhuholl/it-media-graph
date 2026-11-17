# Tasks

Tasks marked **(operator)** reach a server, Telegram or the test environment, and are the operator's. The agent sends no request to Telegram or YouTube, runs no collector or CronJob, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`). Every group that changes the role also ends on a green `make test-e2e`.
- Migrations are verified on a scratch database whose name ends in `_test`.
- The Telegram client is the suite's fake. No test makes a request, and fake ids are spelled as obviously fake.
- `itgraph backfill`'s tests stay green and unedited: they are the check that splitting the walk changed nothing for the command.

## 1. One walk, one page at a time

- [x] 1.1 `config.py` gains `history_walk_since`, default `2025-07-24T00:00:00Z`, validated as timezone-aware and in the past (design.md, Decision 2). Verify in `tests/test_config.py`: the default, a naive datetime refused, a future one refused.
- [x] 1.2 `tg/backfill.py`: one iteration of `backfill_channel` becomes `walk_page`. It reads the cursor and the row count, asks for one window paced by `backfill_request_delay`, keeps what is above the cutoff, stores rows and cursor in one transaction, and says whether the channel is now done — complete or capped, with `complete` recorded either way as today (design.md, Decision 3). `backfill_channel` loops over it. Verify:
  - every test of `itgraph backfill` passes unedited;
  - new tests of `walk_page`: one request per call; a short window ends the walk; a window crossing the cutoff ends it with the cutoff recorded; the ceiling ends it with the oldest stored date recorded; a poll's newest mark is left as it was; a second call resumes from the oldest message stored.

## 2. Which seeds await their walk

- [x] 2.1 `db/backfill.py` gains `seeds_awaiting_walk`: the seeds of `in_scope()` whose `tg_backfill_state.status` is not `complete` — no row, `pending`, `running`, `skipped` or a transient `failed` — ordered by `reviewed_at`, then id (design.md, Decision 2). Verify with tests on a `_test` database:
  - each status;
  - a rejected channel, a candidate, a discussion chat and a permanent failure are left out;
  - a seed complete to a later cutoff is left out;
  - the order.

## 3. The poll loop walks

- [x] 3.1 `tg/watch.py`: when no poll is due and it is not quiet hours, the loop asks for one page of the earliest waiting seed, then goes round again (design.md, Decision 3):
  - scope is read anew before each page;
  - a different seed's first page waits out `backfill_channel_pause_*` since the last page, without sleeping for it;
  - a page Telegram answered is progress for the stall check, as a poll is;
  - the cycle's line counts pages walked and walks completed.

  Verify in `tests/test_watch.py` against the fake client, with the scenarios of "New Seeds Are Walked Without An Operator" and "A Walk Yields To Due Polls":
  - due polls before any page;
  - one page, then the polls again;
  - no page in quiet hours;
  - a completed walk not repeated;
  - the ceiling;
  - a restart resuming from the cursor;
  - a seed rejected mid-walk getting no further page;
  - the modified "An empty queue costs no requests" and the new "An empty queue and a seed to walk".
- [x] 3.2 A walk that could not run (design.md, Decision 4). No cached peer: `record_skip` with the reason, no resolution. A transient failure: `record_failure`. Either way the seed is not tried for an hour, kept in the loop's memory, while another seed is walked. A permanent failure: recorded so, and the channel is neither walked nor polled again. Verify with tests driving the loop's clock through the scenarios of "A Walk That Could Not Run Is Tried Again An Hour Later". A test also asserts that no username resolution appears among the fake client's calls.
- [x] 3.3 A page answered with `FloodWaitTooLong` postpones every poll past the wait, holds every walk until the same moment, and is recorded in `tg_flood_events` under `watch` (design.md, Decision 5). Verify: the scenario "A long rate limit stops the walk with the polls", with no page asked for before the wait ends.
- [x] 3.4 Docs:
  - `src/itgraph/README.md`: the loop walks new seeds — how deep, when, what it skips and retries; `itgraph backfill` stays for deeper walks and still needs the loop stopped; the cycle's line.
  - `src/itgraph/CLAUDE.md`: the rows of `tg/watch.py`, `tg/backfill.py` and `db/backfill.py`.

  Verify: the README names `history_walk_since` and its default, and no production value appears.

## 4. The series

- [x] 4.1 One revision grants the exporter `SELECT (reviewed_at)` on `tg_channels` and `SELECT (status)` on `tg_backfill_state`; its downgrade revokes them. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; `tests/test_schema_names.py` stays green. Under the exporter's role, the scenarios of "The Exporter Reads No Channel's Name Or Notes": it reads the review time and the walk's status, and is refused username, title, kind, rejection reason, both notes, the cursors, the cutoff and the failure detail.
- [x] 4.2 `metrics/series.py` serves `itgraph_telegram_seeds_awaiting_history` and `itgraph_telegram_oldest_awaiting_history_seconds`, from the same predicate as `seeds_awaiting_walk` (design.md, Decision 6). Verify: the scenarios "Seeds awaiting their walk" and "What is out of scope does not wait", through the exporter's role; the series table of the modified requirement matches what a scrape declares.
- [x] 4.3 Docs: `deploy/runbook.md` says what the two series mean, what a seed waiting for a day means, and what to do about it — resolve it, or look at the loop. `src/itgraph/CLAUDE.md`, the row of `metrics/series.py`.

## 5. YouTube's walk every hour

- [x] 5.1 `itgraph_yt_backfill_schedule` becomes `30 * * * *`, and the CronJob's starting deadline becomes 3,300 s; the active deadline and `Forbid` stay (design.md, Decision 7). The comment above the schedule says why the hour no longer matters. Verify: `tests/test_deploy_role.py`'s table of schedules, `make ansible-lint`, and `make test-e2e`.
- [x] 5.2 The scenario "Nothing due spends nothing": a walk with every seed walked makes no request and books no unit. Verify with a test against the fake YouTube and the ledger, added if none covers it yet.
- [x] 5.3 Docs: `src/itgraph/README.md`'s YouTube walk (hourly, not daily), `deploy/runbook.md` where it names the walk's time, `docs/PLAN.md` where it says the walk runs daily.

## 6. Integration

- [x] 6.1 `openspec validate walk-new-seeds --strict`, `make validate` and `make test-e2e`. Verify: all green, and the number of collected tests is not lower than before group 1.
  - Note, run on 2026-11-17: `make validate` green at 2,816 tests, against 2,775 before group 1; `make test-e2e` green at 71 checks, inside quiet hours. The full `make validate` ran after groups 2, 4 and 6 rather than after each group, with each group's own tests run as it ended.
  - Note: splitting the walk exposed that sessions keep objects across commits, so `load_state` answered a page-by-page walk from its identity map and asked for the same page for ever. `load_state` now always reads the database.
  - Note: `tests/test_watch.py`'s `seed()` now records a seed's walk complete unless asked not to, so the polling tests keep testing polling. The walk's own tests pass `walked=False`.

## 7. Rollout

A release of its own.

- [ ] 7.1 **(operator)** Deploy the release to the test environment. Verify: the revision applies; both new series read 0; the YouTube CronJob, where a key is configured, is scheduled hourly.
- [ ] 7.2 **(operator)** Deploy to production. Verify: both series read 0, and the loop's line shows no page walked.
- [ ] 7.3 **(operator)** Mark a seed (`itgraph mark … --seed`) outside quiet hours. Verify:
  - within minutes, `itgraph channels --backfill` shows it complete to 2025-07-24, or capped;
  - the series return to 0;
  - its edges appear after the next `derive`;
  - `tg_flood_events` shows nothing new under `watch`.
- [ ] 7.4 **(operator)** Ask the infrastructure repository for the alert on `itgraph_telegram_oldest_awaiting_history_seconds > 86400` and a panel of both series.

## Workflow follow-up

- `/opsx:apply`, then `/opsx:verify`. Archive once 7.3 is read.
- Next: change 2, Telegram's rising candidates and the loop's capped resolution, with an explore.
