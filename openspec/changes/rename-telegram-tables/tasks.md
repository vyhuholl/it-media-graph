# Tasks

Tasks marked **(operator)** reach a server or the test environment, and are the operator's. The agent runs no collector and no CronJob, sends no request to Telegram or YouTube, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`). Group 2 is one rename in four tasks: the models and the SQL that reads them change together, so the suite is green at its end, not between its tasks. Every group that changes the role also ends on a green `make test-e2e`.
- Migrations are verified on a scratch database whose name ends in `_test`.
- Text is changed where it names a Telegram table, view or enum type, never by a blind replace. `channels` is also a YouTube API method, an `itgraph` command and an English word (design.md, Decision 6).
- Fixtures stay synthetic. No value from the production database enters a file, an artifact or a commit.
- No test is removed, skipped or loosened to make the rename pass. The number of collected tests does not fall.

## 1. Names agree, before anything is renamed

- [ ] 1.1 A test builds a database of its own with `alembic upgrade head` through `conftest.alembic`, as the role tests do. It compares the names in `public` with the suite's `create_all` database: tables, views, constraints, indexes, enum types and sequences (design.md, Decision 3). On a difference, it names each missing or extra name and its kind. Verify: the test passes on today's code, with the old names. If it fails on a difference that predates this change, stop and report the difference to the operator: never exclude it.

## 2. The rename

- [ ] 2.1 One revision renames, from a literal list of `(old, new)` pairs, the 20 tables, the view `channel_families`, the 13 Telegram-only enum types, the 6 sequences, and every constraint and index whose name holds a Telegram table's name: 20 primary keys, 25 foreign keys, 5 unique and 4 check constraints, 12 further indexes (design.md, Decision 2). Upgrade walks the list one way, and downgrade the other. `channel_status`, `baseline_reference` and `alembic_version` are not in it. Verify on a `_test` database seeded with synthetic rows:
  - upgrade, downgrade, upgrade;
  - after upgrade, no name in `public` is an old one, and every table's row count is unchanged;
  - the longest new name fits in 63 characters, with nothing truncated;
  - the role tests that downgrade past head and upgrade again (`test_metrics_role.py` and the others) stay green.
- [ ] 2.2 The models: every Telegram `__tablename__` and every Telegram-only enum's `name=` take `tg_`, and `db/views.py` creates and drops `tg_channel_families` over `tg_affiliation_candidates`. Python class names do not change. Verify: test 1.1 passes against the new revision, and autogenerate on a `_test` database at head reports nothing.
- [ ] 2.3 The SQL in `src/` where it names a Telegram table, the view or a Telegram-only enum: `db/` (`INVENTORY_TABLES` in `db/backup.py` included), `derive/`, `scoring/`, `alerts/`, `affiliation/`, `metrics/series.py`, `bot/`, `tg/`, `yt/`, and the `derive/tg_youtube.py` and `db/yt_telegram_links.py` readers of the Telegram raw layer. Verify: `make validate` green.
- [ ] 2.4 The tests' and the tracked notebooks' SQL, and the docs that describe the current schema:
  - `tests/` (fixtures, role tests, `to_regclass` checks, `tests/people_cases.py` where it names a table) and `notebooks/`;
  - the root `CLAUDE.md`, `src/itgraph/CLAUDE.md` (the rows naming `raw_messages`, `message_metrics`, `edges`, `pending_mentions`, `channel_families` and the others), the SQL examples in `src/itgraph/README.md`, and `deploy/runbook.md`'s reader section;
  - `docs/PLAN.md`: the "Storage" sketch and the present-tense mentions take the new names, and "Separate tables, not a platform column" says that Telegram's tables carry `tg_` since this change.

  Archived changes are not edited. Verify: `make validate` green; the notebooks compile under `uv run python -m py_compile`; the docs' examples name only tables that exist after 2.1.

## 3. The role and the restore

- [ ] 3.1 `tasks/database_state.yml` probes `coalesce(to_regclass('public.tg_channels'), to_regclass('public.channels'))` and counts the rows of whichever exists, with a comment saying why the old name stays (design.md, Decision 5). Verify in `tests/test_deploy_role_run.py`: a database with only `channels`, one with only `tg_channels`, and one with neither each give the count and the assert they gave before. A probe that fails is still a failure, never an empty answer.
- [ ] 3.2 The role's queries that run after the migration take the new names: `trials.yml` (the synthetic alert, the weekly list's count), `polls.yml`, `restore.yml` (the channels left). So do `files/neutralise.sql` and `files/readback.sql`, whose tests remove each statement in turn. Verify: those tests green, and `make ansible-lint` green.
- [ ] 3.3 `deploy/ansible/e2e.sh`: a check made while the database is at an older revision, or before anything is loaded, keeps the old name, and a check made after the migration takes the new one. Verify: `make test-e2e` green. e2e builds the current image only and never deploys a database at the previous revision, so the first deploy of the rename is exercised by 5.1, not here.
- [ ] 3.4 `src/itgraph/README.md`, "Восстановление" (design.md, Decision 8):
  - a full dump older than the rename is restored the rehearsal's way, into an emptied database, and never with `--clean --if-exists` over a renamed one, with the reason;
  - an inventory dump older than the rename is restored into a scratch database, its tables renamed there, and its rows poured from it;
  - such dumps age out within `backup_keep_inventory` days.

  Verify: the commands as written name only tables that exist on each side.

## 4. Integration

- [ ] 4.1 The sweep of design.md, Decision 6 over `src/`, `tests/`, `deploy/`, `scripts/`, `notebooks/`, `docs/`, `CLAUDE.md` and `openspec/specs/`. It looks for every old table, view and Telegram-only enum name in SQL position: after `FROM`, `JOIN`, `INTO`, `UPDATE`, `TABLE`, `ON`, `REFERENCES`, inside `to_regclass('…')`, and as a `::type` cast. Verify: it comes back empty but for the probe's fallback (3.1), e2e's checks at an older revision (3.3), the README's notes on old dumps (3.4) and the revisions before this one. List what it found in the task's note.
- [ ] 4.2 `openspec validate rename-telegram-tables --strict`, `make validate` and `make test-e2e`. Verify: all green, and the number of collected tests is not lower than before group 1.

## 5. Rollout

This change ships in one release with the three changes after it, so these are done at that release's deploy.

- [ ] 5.1 **(operator)** Deploy the release to the test environment. Verify:
  - the role's report names the channels counted under the old name before the migration;
  - the migration applies, the trials pass, and the CronJobs' next runs succeed;
  - in the test database, no old name is left (`\dt`, `\dv`, `\dT`).
- [ ] 5.2 **(operator)** Run the restore rehearsal in the test environment from the newest full dump, which predates the rename. Verify: it loads under the old names, migrates to the new ones, and neutralisation and the read-back pass.
- [ ] 5.3 **(operator)** Before production, close every reader session: DBeaver, and any agent's tunnel. Then deploy the release to production. Verify: the migration applies without waiting on a lock, and the collector and the bot are back.
- [ ] 5.4 **(operator)** Right after 5.3: `scripts/on-host.sh --backups backup --full`. Verify: `full-LATEST.dump` is the new dump, and it lists `tg_channels` under `pg_restore --list`.
- [ ] 5.5 **(operator)** Update saved queries outside the repository, such as DBeaver's, to the new names.

## Workflow follow-up

- `/opsx:verify`, then archive once group 4 is green. Group 5 waits for the release that carries changes 1–3 too.
- Next: change 1, the new seeds' history walked by the poll loop, with an explore.
