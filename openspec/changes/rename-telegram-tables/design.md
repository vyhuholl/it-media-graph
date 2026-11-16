# Design

## Context

See proposal.md for why. What shapes the approach:

- **Two schemas are built from two sources.** Production's comes from the Alembic revisions. The test suite builds its schema once per run from the models (`Base.metadata.create_all` in `tests/conftest.py`). Only the role tests (`test_*_role*.py`) migrate a database, and they check grants, not names. Nothing compares the two. A rename is where they could drift without anyone seeing it: production would run one set of names and the tests another.
- **The naming convention puts table names inside other names.** `ix_%(table_name)s_%(column_0_name)s`, `uq_…`, `ck_%(table_name)s_%(constraint_name)s`, `pk_%(table_name)s`, and `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, which holds two table names. Postgres renames none of them when a table is renamed. A `serial` column's sequence (`edges_id_seq`) keeps its name too.
- **The revisions import no application code.** They hold their SQL frozen, the view's definition included, so editing `db/views.py` or the models cannot change what an old revision does.
- **The role reads the database before it migrates.** `tasks/database_state.yml` looks for `public.channels`, counts its rows, and uses that count to decide whether "columns referencing a type that is gone" is fatal. Everything else the role queries (`trials.yml`, `polls.yml`, `restore.yml`) runs after the migration.
- **Dumps carry their own schema.** A full dump restores at its own revision, and the migration to head brings it forward. That is what the restore rehearsal does after `dropdb`/`createdb`. The manual restore over the working database in `src/itgraph/README.md` uses `pg_restore --clean --if-exists` instead. It drops only what the dump names. An inventory dump is the hand-reviewed tables alone (`--table`), with no `alembic_version`.
- **Full dumps are weekly.** `backup_full_interval_days` is 7. The dump `alembic upgrade` takes first is a full dump of the schema *before* the revision. So for up to a week after the deploy, `full-LATEST.dump` predates the rename.

## Goals / Non-Goals

**Goals:**
- A database migrated to head and a database built from the models agree on every name: tables, views, constraints, indexes, enum types and sequences. A test says so.
- The deploy that renames is checked as carefully as any other. Its pre-migration probe sees the channels under either name.
- No window in which a documented restore silently produces a database holding both sets of names.

**Non-Goals:**
- Compatibility views under the old names (Decision 1).
- Redesigning the manual restore over the working database. Only the part this rename breaks is addressed (Decision 8).

## Decisions

### 1. Rename in place, one revision, no aliases

`ALTER TABLE … RENAME`, `ALTER VIEW … RENAME`, `ALTER TYPE … RENAME`, `ALTER SEQUENCE … RENAME`, and `ALTER TABLE … RENAME CONSTRAINT` and `ALTER INDEX … RENAME` for every name built on a table name. All of it is metadata. No row is rewritten, and grants, default privileges, column defaults and the view's stored definition follow by object identity.

Rejected:
- **Views or synonyms under the old names.** Two names for one table is the confusion the change removes. An updatable view would need grants of its own for each role. Nothing in the repository would need the old names once its text is changed.
- **Several revisions, one per area.** The rename is one decision. Splitting it would leave an intermediate schema that no image ever runs.

### 2. The new name is `tg_` + the old one, and an explicit list holds them

Every table, the view, and the 13 Telegram-only enum types take `tg_` before their current name. Every constraint or index name takes the name the convention gives for the new table names. For a foreign key into a Telegram table, both table names change: `fk_edges_dst_channel_id_channels` becomes `fk_tg_edges_dst_channel_id_tg_channels`. The names given explicitly rather than by the convention, such as `ix_edges_dst_channel_id_dst_msg_id`, follow the same rule. Sequences become `tg_<table>_id_seq`, which is what `create_all` names them for the new table.

The revision holds the pairs as a literal list, upgrade walking it one way and downgrade the other. It does not compute the names from the catalog when it runs. A revision is frozen code, and one that renamed "whatever matches" would one day rename something added after it.

Lengths: the longest new name is 58 characters, against Postgres's 63, so SQLAlchemy never truncates and hashes one.

Shared and kept: `channel_status` (`channels`, `yt_channels`), `baseline_reference` (`channel_baselines`, `yt_channel_baselines`) and `alembic_version`.

### 3. A test that the migrated database and the models agree on names

A new test builds one database with `alembic upgrade head` and reads the names of its tables, views, constraints, indexes, enum types and sequences in `public`. It compares them with the same read of the suite's `create_all` database. The comparison is on names, not on the full DDL. Names are what this change can get wrong, and a full schema comparison would be a change of its own.

If it finds differences that predate this change, they are reported to the operator, not silently fixed or excluded.

Rejected: alembic's `compare_metadata`. It does not compare enum type or sequence names, which are two of the six kinds renamed here.

### 4. The downgrade renames everything back, and the guard still applies

The downgrade is lossless and is exercised already: role tests such as `test_metrics_role.py` downgrade past head and upgrade again. `db/guard.py` still refuses a downgrade on a database whose name does not end in `_test`. The guard cannot tell a lossless downgrade from a destructive one. In the same release, changes 1–3 add tables that a downgrade would drop.

### 5. The role's probe finds the channels under either name

`database_state.yml` asks for `coalesce(to_regclass('public.tg_channels'), to_regclass('public.channels'))` and counts the rows of whichever exists. The old name is still there on the first deploy of this release, and in any database left at an older revision, such as a restore interrupted before its migration. Without the fallback, the probe would read "no channels table" there. The count would be 0, and the assert that the database is whole would pass without checking anything, on exactly the deploy that renames every table. A comment beside the fallback says why it stays.

`deploy/ansible/e2e.sh` follows the same rule. A check made while the database is at an older revision uses the old name, and a check made after the migration uses the new one. `tests/test_deploy_role_run.py`'s rules match both probes.

### 6. Text is changed by context, and a final sweep proves it

`channels` is also the name of a YouTube API method, an `itgraph` command and an English word. `alerts`, `edges` and `metric` are words too. So no blind replace: every raw SQL string, every `__tablename__` and enum `name=`, `db/views.py`, `INVENTORY_TABLES`, `files/neutralise.sql`, `files/readback.sql`, the role's tasks, `e2e.sh`, the tests and the tracked notebooks are edited where they name a table.

The suite then runs on the new names, so any SQL a test reaches and that was missed fails loudly. For what no test reaches, a closing sweep over `src/`, `tests/`, `deploy/`, `scripts/` and `notebooks/` looks for the old names in SQL position: after `FROM`, `JOIN`, `INTO`, `UPDATE`, `TABLE`, `ON`, `REFERENCES`, in `to_regclass('…')`, and as `::type` casts. It must come back empty. This is a step of the change, not a permanent test. Once the change is done, a test about names that no longer exist is noise.

### 7. Python names stay; docs follow the schema

The model classes, the module names and the CLI do not change. The docs that describe the current schema change to the new names: the root `CLAUDE.md`, `src/itgraph/CLAUDE.md`, `src/itgraph/README.md`, `deploy/runbook.md`, and `docs/PLAN.md`, whose "Separate tables, not a platform column" paragraph gains that Telegram's tables carry `tg_` since this change. Archived OpenSpec changes are history and are not edited.

### 8. Restores across the rename

- **Full dump, the rehearsal's way** (`dropdb`, `createdb`, load, migrate to head): works unchanged. A dump from before the rename comes back with the old names, and the migration renames them.
- **Full dump over the working database with `--clean --if-exists`:** a dump from before the rename must not go this way. It would drop nothing that exists, restore the old tables beside the new ones, set `alembic_version` back, and the next deploy's migration would then fail on names that already exist. Two measures, both small:
  - After production's deploy, the operator takes a full dump at once (`scripts/on-host.sh --backups backup --full`), so `full-LATEST.dump` stops predating the rename the same day rather than within a week.
  - The README's manual-restore section says that a full dump older than the rename is restored the rehearsal's way.
- **Inventory dump from before the rename:** `pg_restore --data-only --table=channels` no longer matches anything. The README says to restore such a dump into a scratch database, rename its tables there, and pour the rows from it. Inventory dumps are kept 30 days (`backup_keep_inventory`), so the last of them ages out a month after the deploy.

## Risks / Trade-offs

- **[A raw SQL string no test reaches still names an old table]** → the commands most exposed are the operator's own (`resolve`, `backfill`, `add`, `metadata`, `floods`, `affiliation`), and the test environment cannot run the networked ones. Mitigation: their SQL is covered by the offline suite, plus the sweep in Decision 6. In production, such a string fails loudly with "relation does not exist" and writes nothing.
- **[A reader's open transaction holds a lock the rename waits for]** → the writers are stopped for the migration, but the exporter and any tunnelled session still read. An `ALTER … RENAME` queued behind an open transaction makes every later query on that table queue too. Mitigation: close reader sessions (DBeaver, an agent's tunnel) before the production deploy. The reader's own limits end an idle transaction after 5 minutes and a statement after 10, so the worst case is a wait, not a hang. Once its locks are held, the rename itself takes milliseconds.
- **[The pre-migration dump predates the rename]** → this is intended: it is the rollback point. Decision 8 covers what it means for `full-LATEST.dump`.
- **[The operator's saved queries break]** → accepted and announced in the release notes. No alias (Decision 1).
- **[The names test finds old drift]** → it then fails for a reason this change did not cause. Mitigation: report it to the operator and decide (Decision 3). Never exclude it to make the test pass.

## Migration Plan

1. The test environment first: deploy the pinned release. The role probes under the old name, takes the pre-migration dump, stops the writers, migrates, starts the workloads and runs its trials (alerts, the weekly list, polls), all on the new names.
2. Run the restore rehearsal there with the newest full dump, which predates the rename. It loads under the old names and migrates to the new ones. Neutralisation and read-back run on the new names.
3. Before production: close reader sessions.
4. Production: the same deploy.
5. After production: `scripts/on-host.sh --backups backup --full`, and check that `full-LATEST.dump` is the new one.

Rollback: the release also carries changes 1–3, so rolling back is decided for the release, not for this change. This change's part is lossless: its downgrade renames everything back. Changes 1–3 add tables a downgrade would drop, which is why rolling back the release means restoring the pre-migration dump and pinning the previous release, as for any release whose revisions add tables.
