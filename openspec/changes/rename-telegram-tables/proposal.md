# Proposal

## Why

YouTube's tables have carried a `yt_` prefix since the first YouTube change ("Separate tables, not a platform column" in `docs/PLAN.md`). Telegram's carry none, so in any database tool they sit unmarked among YouTube's: `alerts` beside `yt_alerts`, `weekly_lists` beside `yt_weekly_lists`, `channels` beside `yt_channels`. The operator asked for every Telegram table to carry `tg_`, as YouTube's carry `yt_`, and for this to go first. The changes after it (new seeds' history walked by the poll loop, Telegram's rising candidates, review in the bot) add Telegram tables of their own. Renamed first, those tables are created with the prefix and nothing has to be renamed twice.

Measured on the production database on 2026-11-16 (schema only):

- **20 Telegram tables and one view** (`channel_families`), with 20 primary keys, 25 foreign keys, 5 unique and 4 check constraints, 12 further indexes and 6 sequences named after them.
- **15 enum types without `yt_`.** Two are shared by both platforms: `channel_status` (`channels`, `yt_channels`) and `baseline_reference` (`channel_baselines`, `yt_channel_baselines`). The other 13 are Telegram's alone.
- **Grants survive a rename.** A grant attaches to the object, not to its name, so the bot's, the exporter's and the reader's privileges carry over unchanged.

## What Changes

- **Every Telegram table and the view are renamed to `tg_` + their current name:** `channels` → `tg_channels`, `raw_messages` → `tg_raw_messages`, `alerts` → `tg_alerts`, `channel_families` → `tg_channel_families`, and so on for all 21. Column names do not change.
- **What is named after them follows them.** Primary keys, foreign keys, unique and check constraints, indexes and sequences are renamed to what the naming convention gives for the new names, so that autogenerate finds no difference afterwards.
- **The 13 Telegram-only enum types are renamed to `tg_` + their current name** (`alert_kind` → `tg_alert_kind`, `metric` → `tg_metric`, and so on), as YouTube's are `yt_*`.
- **What both platforms share keeps no prefix:** `channel_status`, `baseline_reference`, `alembic_version`.
- **One Alembic revision, metadata only.** No row is rewritten and nothing is copied. Its downgrade renames everything back.
- **Everything that names them by text:** the models' table and enum names, raw SQL across the code, the view's definition, the inventory tier of the backups, the role's tasks and the restore's `files/neutralise.sql` and `files/readback.sql`, `e2e.sh`, the tests, the tracked notebooks, the specs that name a table, and the docs.
- **The role's check before migrating finds the channels under either name.** On the first deploy of this release the database still has the old one. A probe that knew only the new name would read zero channels and skip the check that the database is whole.
- **The role's checks of the bot and the collector give up when quiet hours would overtake their wait,** rather than fail on a service that works. Found by e2e while verifying this change: a trial started at 01:58 Moscow time had its alerts held from 02:00. They also read a quiet window as the code does now: past midnight when it starts later than it ends, and none when its bounds are equal.
- **A test that a migrated database and the models agree on every name.** Today nothing compares them: the suite builds its schema from the models, production from the revisions.
- **BREAKING for anything outside the repository that queries by name:** the operator's saved queries, and an inventory dump taken before the rename, which restores under the old names. A full dump taken before the rename still restores with one command, the rehearsal's way: it loads at its own revision, and the migration to head renames it. It must not be restored with `--clean` over a renamed database, so production takes a fresh full dump right after the deploy.

**Out of scope:**

- Python class names (`Channel`, `Alert`, `RawMessage`, …). The prefix is for the database. `Yt*` and the unprefixed Telegram classes stay as they are.
- Column names, `tg_id` included.
- The names of metric series and their label values. The exporter's SQL changes, but the shared monitoring sees the same series.
- Converting inventory dumps taken before the rename.
- Any change in behaviour but the role's checks near quiet hours. The features this rename prepares for are changes of their own.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `collection-monitoring`: the requirement that a series needs no sequential scan names the snapshot and raw tables by their new names.
- `youtube-telegram-links`: the requirement that nothing joined is stored names the Telegram raw layer by its new name.
- `database-backups`: the restore's alert trials are not started when quiet hours would begin before their wait ends, and read quiet hours past midnight and with equal bounds as the bot does.
- `collection-deploy`: the deploy does not wait for a poll when quiet hours would begin before the wait ends, and its report says so.

## Impact

- **Code:** `db/models.py` (table and enum names), `db/views.py`, every module with raw SQL over a Telegram table (`db/`, `derive/`, `scoring/`, `alerts/`, `affiliation/`, `metrics/series.py`, `bot/`), and `db/backup.py` (`INVENTORY_TABLES`). No new dependency.
- **Schema:** one Alembic revision renaming 20 tables, 1 view, 13 enum types, 6 sequences, 54 constraints and 12 indexes. Metadata only. It holds a brief exclusive lock on each object it renames.
- **Role:** the probe in `tasks/database_state.yml`, the queries in `trials.yml`, `polls.yml` and `restore.yml`, `files/neutralise.sql`, `files/readback.sql` and the tests that remove each of their statements in turn, `e2e.sh`, and `tests/test_deploy_role_run.py`. No new object, CronJob or input. The deploy stops the writers for the migration, as for any revision, and the pre-migration full dump is taken as always.
- **Role, quiet hours:** a new `tasks/quiet.yml`, which `trials.yml` and `polls.yml` include before they wait.
- **Tests:** a new test comparing the names in a migrated database with the models'. Role tests for the checks near and across quiet hours.
- **Docs:** the root `CLAUDE.md`, `src/itgraph/CLAUDE.md`, `src/itgraph/README.md` (restoring a full or inventory dump taken before the rename), `deploy/runbook.md`, `docs/PLAN.md`.
- **Production, after the deploy:** the operator takes a full dump at once (`scripts/on-host.sh --backups backup --full`). Otherwise `full-LATEST.dump` predates the rename for up to a week.
- **Infrastructure repository:** nothing. Series names do not change.
- **Release:** ships in one release with the three changes that follow. The test environment first, as always.

## Decision record

Brought in the explore session of 2026-11-16, while scoping rising candidates for both platforms. The operator's words:

> А, и ещё кое-что. Для удобства хочу переименовать таблицы в базе, чтобы у всех таблиц, связанных с Telegram, был префикс tg_, как сейчас у ютуб-таблиц — префикс yt_. Это, наверное, проще всего будет сделать самым первым.

> 1. Да. Всё, что общее у telegram и YouTube, оставляем без префикса.

> Да, change 0, 1, 2 и 3 я выкачу одним релизом. А следующим релизом — change 4.

Asked whether the prefix also covers the 13 Telegram-only enum types and the Python classes, the operator chose "База целиком": tables, view and Telegram-only enums take `tg_`, and Python classes stay as they are.

After verification, which reported the role's trial failing across 02:00 as a defect outside this change:

> Правь пробу роли прям здесь. И suggestion тоже поправь. Как всё поправишь — прогони полный make validate. e2e снова гонять не надо.

Confirmed as of 2026-11-16:

1. Every Telegram table and the view take `tg_`, with their keys, constraints, indexes and sequences.
2. The Telegram-only enum types take `tg_`. What both platforms share keeps no prefix.
3. Python class names do not change.
4. This change goes first, and ships in one release with the next three.
5. The role's trial that fails across the start of quiet hours is fixed in this change.

**Not covered by the requirements.** These were chosen here, not by the operator:

- The new name is `tg_` + the old one, with nothing else renamed. For example, `alerts` becomes `tg_alerts`, not `tg_spike_alerts`.
- `baseline_reference` counts as shared, because a YouTube table uses it too.
- Metric series keep their names.
- Rename in place, with no aliases under the old names (design.md, Decision 1).
- The revision holds the names as a literal list rather than reading them from the catalog (design.md, Decision 2).
- The names test compares names, not full DDL. Drift it finds that predates this change is reported to the operator (design.md, Decision 3).
- The role's probe keeps the old name as a fallback for good, with the reason beside it (design.md, Decision 5).
- The old names are swept for once, as a step of this change rather than a permanent test (design.md, Decision 6).
- Restores across the rename: a full dump right after production's deploy, and README notes. Inventory dumps from before the rename are documented, not converted (design.md, Decision 8).
- The poll check gets the same fix as the trial, since it fails the same way. One answer covers both trials. The checks also take on the code's reading of a quiet window past midnight and with equal bounds. A check that cannot be made is not started, rather than judged after its wait (design.md, Decision 9).
- e2e is not run again for this fix, at the operator's word. The fix is covered by the role tests.
