# Tasks

Tasks marked **(operator)** reach a server or the test environment, and are the operator's. The agent runs no collector and no CronJob, sends no request to Telegram or YouTube, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`). Group 2 is one rename in four tasks: the models and the SQL that reads them change together, so the suite is green at its end, not between its tasks. Every group that changes the role also ends on a green `make test-e2e`.
- Migrations are verified on a scratch database whose name ends in `_test`.
- Text is changed where it names a Telegram table, view or enum type, never by a blind replace. `channels` is also a YouTube API method, an `itgraph` command and an English word (design.md, Decision 6).
- Fixtures stay synthetic. No value from the production database enters a file, an artifact or a commit.
- No test is removed, skipped or loosened to make the rename pass. The number of collected tests does not fall.

## 1. Names agree, before anything is renamed

- [x] 1.1 A test builds a database of its own with `alembic upgrade head` through `conftest.alembic`, as the role tests do. It compares the names in `public` with the suite's `create_all` database: tables, views, constraints, indexes, enum types and sequences (design.md, Decision 3). On a difference, it names each missing or extra name and its kind. Verify: the test passes on today's code, with the old names. If it fails on a difference that predates this change, stop and report the difference to the operator: never exclude it.

## 2. The rename

- [x] 2.1 One revision renames, from a literal list of `(old, new)` pairs, the 20 tables, the view `channel_families`, the 13 Telegram-only enum types, the 6 sequences, and every constraint and index whose name holds a Telegram table's name: 20 primary keys, 25 foreign keys, 5 unique and 4 check constraints, 12 further indexes (design.md, Decision 2). Upgrade walks the list one way, and downgrade the other. `channel_status`, `baseline_reference` and `alembic_version` are not in it. Verify on a `_test` database seeded with synthetic rows:
  - upgrade, downgrade, upgrade;
  - after upgrade, no name in `public` is an old one, and every table's row count is unchanged;
  - the longest new name fits in 63 characters, with nothing truncated;
  - the role tests that downgrade past head and upgrade again (`test_metrics_role.py` and the others) stay green.
- [x] 2.2 The models: every Telegram `__tablename__` and every Telegram-only enum's `name=` take `tg_`, and `db/views.py` creates and drops `tg_channel_families` over `tg_affiliation_candidates`. Python class names do not change. Verify: test 1.1 passes against the new revision, and autogenerate on a `_test` database at head reports nothing.
- [x] 2.3 The SQL in `src/` where it names a Telegram table, the view or a Telegram-only enum: `db/` (`INVENTORY_TABLES` in `db/backup.py` included), `derive/`, `scoring/`, `alerts/`, `affiliation/`, `metrics/series.py`, `bot/`, `tg/`, `yt/`, and the `derive/tg_youtube.py` and `db/yt_telegram_links.py` readers of the Telegram raw layer. Verify: `make validate` green.
- [x] 2.4 The tests' and the tracked notebooks' SQL, and the docs that describe the current schema:
  - `tests/` (fixtures, role tests, `to_regclass` checks, `tests/people_cases.py` where it names a table) and `notebooks/`;
  - the root `CLAUDE.md`, `src/itgraph/CLAUDE.md` (the rows naming `raw_messages`, `message_metrics`, `edges`, `pending_mentions`, `channel_families` and the others), the SQL examples in `src/itgraph/README.md`, and `deploy/runbook.md`'s reader section;
  - `docs/PLAN.md`: the "Storage" sketch and the present-tense mentions take the new names, and "Separate tables, not a platform column" says that Telegram's tables carry `tg_` since this change.

  Archived changes are not edited. Verify: `make validate` green; the notebooks compile under `uv run python -m py_compile`; the docs' examples name only tables that exist after 2.1.

## 3. The role and the restore

- [x] 3.1 `tasks/database_state.yml` probes `coalesce(to_regclass('public.tg_channels'), to_regclass('public.channels'))` and counts the rows of whichever exists, with a comment saying why the old name stays (design.md, Decision 5). Verify in `tests/test_deploy_role_run.py`: a database with only `channels`, one with only `tg_channels`, and one with neither each give the count and the assert they gave before. A probe that fails is still a failure, never an empty answer.
- [x] 3.2 The role's queries that run after the migration take the new names: `trials.yml` (the synthetic alert, the weekly list's count), `polls.yml`, `restore.yml` (the channels left). So do `files/neutralise.sql` and `files/readback.sql`, whose tests remove each statement in turn. Verify: those tests green, and `make ansible-lint` green.
- [x] 3.3 `deploy/ansible/e2e.sh`: a check made while the database is at an older revision, or before anything is loaded, keeps the old name, and a check made after the migration takes the new one. `e2e_seed.sql` keeps the old names: every database it seeds is at an older revision. Verify: `make test-e2e` green. It crosses the rename twice: the restore loads a dump made at an older revision, and the takeover runs the role on a database at `a89cb273f885`, so both see the probe's old name before the migration.
  - Note, found on 2026-11-16: this task first said e2e never deploys a database at an older revision. It does — the takeover.
  - Note, run on 2026-11-17: green on the fourth run, 71 checks, inside quiet hours. The second run proved delivery after the rename outside them: the first restore's Telegram alert and both YouTube topics. What failed on the way:
    - `public.channels` in the check of the reader's grants in a dump at head — this change's, fixed (see 4.1);
    - the role's YouTube trial on the second restore, started at 01:59 Moscow time: its alerts were raised before 02:00 and held by quiet hours during its five-minute wait. The trial reads the hour once, at its start. Not this change's; reported to the operator, not fixed here;
    - the first run's Telegram trial at 00:45, the bot delivering nothing in five minutes, with no log kept. Not reproduced by the three runs after it with the same code; reported to the operator.
- [x] 3.4 `src/itgraph/README.md`, "Восстановление" (design.md, Decision 8):
  - a full dump older than the rename is restored the rehearsal's way, into an emptied database, and never with `--clean --if-exists` over a renamed one, with the reason;
  - an inventory dump older than the rename is restored into a scratch database, its tables renamed there, and its rows poured from it;
  - such dumps age out within `backup_keep_inventory` days.

  Verify: the commands as written name only tables that exist on each side.

## 4. Integration

- [x] 4.1 The sweep of design.md, Decision 6 over `src/`, `tests/`, `deploy/`, `scripts/`, `notebooks/`, `docs/`, `CLAUDE.md` and `openspec/specs/`. It looks for every old table, view and Telegram-only enum name in SQL position: after `FROM`, `JOIN`, `INTO`, `UPDATE`, `TABLE`, `ON`, `REFERENCES`, inside `to_regclass('…')`, and as a `::type` cast. Verify: it comes back empty but for the probe's fallback (3.1), e2e's checks at an older revision (3.3), the README's notes on old dumps (3.4) and the revisions before this one. List what it found in the task's note.
  - Note, swept on 2026-11-17 (backticked names in comments and docstrings included). Old names left, each on purpose:
    - the probe's fallback in `database_state.yml`, and the test naming it;
    - e2e's checks before a migration or after a failed load (`e2e.sh` 616, 771, 1130), and `e2e_seed.sql`, which seeds databases at older revisions;
    - SQL run at an older revision: `test_people_migration.py`, the downgrade checks in `test_alert_roles.py`, `test_metrics_role.py`, `test_second_wind_roles.py` and `test_telegram_weekly_roles.py`;
    - the README's notes on dumps from before the rename;
    - the two main specs whose deltas this change carries, rewritten at archive.
  - Not table names, and left: the `itgraph channels` and `itgraph alerts` commands, the `itgraph-alerts` container, the YouTube API's `channels` method and `contentDetails.channels`, the Telegram API's `channels.*` methods, and the record's `channel_baselines`-style keys in `test_telegram_scoring_record.py`.
  - Missed by the first pass and caught by e2e: a name with its schema, `public.channels`, in the check that a dump at head carries the reader's grants. Swept again for `public.<old name>`: what is left is the probe's fallback, e2e's checks at an older revision, and the role test's mocked row count of a dump, which is the dump's own name before any migration.
  - Missed by the first pass and caught by the suite: constraint names referenced in code (`ON CONFLICT ON CONSTRAINT`, the weekly list's unique key) and the frozen people revision's table list, which `test_neutralise.py` now reaches under its new names.
- [x] 4.2 `openspec validate rename-telegram-tables --strict`, `make validate` and `make test-e2e`. Verify: all green, and the number of collected tests is not lower than before group 1.

## 5. Quiet hours in the role's checks

Added after verification, at the operator's word (design.md, Decision 9). e2e is not run again for it.

- [x] 5.1 `tasks/quiet.yml` answers whether quiet hours hold now, or begin before a wait of `__itgraph_quiet_wait` seconds (and a minute more) would end. It reads them as `schedule.in_quiet_window` does: past midnight when the window starts later than it ends, none when its bounds are equal. The clock is read once, to the minute. `trials.yml` asks it once for both trials, with their two waits where YouTube alerts are configured. `polls.yml` asks it with its own wait. A check that cannot be made says why, beginning "could not be proven" or "could not be checked" as before. Verify in `tests/test_deploy_role_run.py`:
  - trials at 01:58 raise no alert of either platform and report it;
  - trials at 01:45, with both waits to spare, are run;
  - polls at 01:57 are not waited for, and at 01:50 are;
  - a 22–06 window holds at 23:00 and 03:00 and not at 12:00, and equal bounds hold never;
  - the tests already there pass unedited.
- [x] 5.2 Delta specs for `database-backups` ("The Service Is Tried On The Restored Data") and `collection-deploy` ("The Deploy Verifies The Exit", "The Run Reports What It Did"). The runbook says what a check near quiet hours reports. Verify: `openspec validate rename-telegram-tables --strict`.
- [x] 5.3 A full `make validate`. Verify: green, and the number of collected tests not lower than at 4.2.

## 6. Rollout

This change ships in one release with the three changes after it, so these are done at that release's deploy.

- [ ] 6.1 **(operator)** Deploy the release to the test environment. Verify:
  - the role's report names the channels counted under the old name before the migration;
  - the migration applies, the trials pass, and the CronJobs' next runs succeed;
  - in the test database, no old name is left (`\dt`, `\dv`, `\dT`).
- [ ] 6.2 **(operator)** Run the restore rehearsal in the test environment from the newest full dump, which predates the rename. Verify: it loads under the old names, migrates to the new ones, and neutralisation and the read-back pass.
- [ ] 6.3 **(operator)** Before production, close every reader session: DBeaver, and any agent's tunnel. Then deploy the release to production. Verify: the migration applies without waiting on a lock, and the collector and the bot are back.
- [ ] 6.4 **(operator)** Right after 6.3: `scripts/on-host.sh --backups backup --full`. Verify: `full-LATEST.dump` is the new dump, and it lists `tg_channels` under `pg_restore --list`.
- [ ] 6.5 **(operator)** Update saved queries outside the repository, such as DBeaver's, to the new names.

## Workflow follow-up

- `/opsx:verify`, then archive once group 5 is green. Group 6 waits for the release that carries changes 1–3 too.
- Next: change 1, the new seeds' history walked by the poll loop, with an explore.
