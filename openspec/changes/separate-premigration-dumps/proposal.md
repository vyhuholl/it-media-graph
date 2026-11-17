# Proposal

## Why

On 2026-11-17 the off-site store's account reached its daily storage cap. Its caps are $0, so that means the free tier is full. Half of it was itgraph's full copies under `full/`: 16 copies of about 0.3 GB in 17 days, where the design keeps about four (one a week, 28 days of lifecycle). Fifteen of the sixteen were the dump `alembic upgrade` takes before migrating. The deploy runs it whenever the schema is behind (`deploy/ansible/itgraph/tasks/migrate.yml`), and in this period that was more than once a day on some days.

The dump lands in `weekly/` as `full-<UTC>.dump`, beside the scheduled ones. The off-site pass copies every full dump younger than its seven-day window that the store lacks, so each deploy that migrated added 0.3 GB to the store. The same dump also stops the schedule. `is_due` reads the newest file in `weekly/`, so while migrations come more often than once a week, the scheduled weekly dump is never taken.

The design of `move-to-k3s-host` copied the pre-migration dump off-site on purpose ("The dump taken before a migration is in `weekly/` like any other full dump, so the next scheduled run copies it"). That was sized for occasional migrations. At today's pace it is the store's largest consumer, with no bound.

## What Changes

- **The dump taken before a migration becomes a tier of its own.** It goes to `premigration/premigration-<UTC>.dump`, with its own `premigration-LATEST.dump` pointer. The five newest are kept, by count alone (`backup_keep_premigration`). It is read back with `pg_restore --list` like every dump, and the migration still refuses to run if it fails. `ITGRAPH_SKIP_BACKUP` and the empty-database exception are unchanged.
- **A pre-migration dump stays on the host.** The off-site pass copies the scheduled tiers alone: inventory dumps and full dumps, a full dump taken by hand included. The store's lifecycle and the host's key are unchanged.
- **The full tier sees only full dumps.** Its schedule (weekly), its retention (28 days, never fewer than four), `full-LATEST.dump` and its off-site copies no longer see a pre-migration dump. A migration no longer holds back the weekly dump. The 28-day bound stays: a full dump taken by hand (`itgraph backup --full`) still shares the tier.
- **The deploy's report names the new path.** No task of the role changes. `migrate.yml` already reports whatever path the migration logs.
- **Restoring is unchanged.** The role's restore entry point takes a path. The infrastructure repository's restore takes the newest `full-…` dump in `weekly/`, or the newest copy under `full/` in the store, and both are still exactly where it looks. Nothing changes there.
- **Restoring over production by hand says which dump to take.** The newest dump may now be a pre-migration one, up to a week newer than `full-LATEST.dump`. The README says to take the newer of the two pointers, and after a failed migration the pre-migration one.
- **No code for the transition.** Pre-migration dumps already in `weekly/` stay there as `full-…`. They are whole-database dumps, and the full tier's 28 days age them out. For a week after the deploy, the operator deletes from the store only copies outside the seven-day window, as now.

**Out of scope:**

- Bringing metric snapshots off the host more often than weekly. The operator accepts losing up to a week of them with the host (Decision record).
- Affiliation decisions. They are hand-made, but they are in the full dump only, not in the inventory tier. A host loss loses up to a week of them as well. None has been made since 2026-08-11.
- The order of a restore after losing the host. The inventory dump's `tg_backfill_state` is newer than the full dump's raw layer, and the poll loop reads `newest_fetched_id` in place, so posts in the gap may not be fetched again. Not verified here.
- The store's lifecycle rules and the host's key.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backups`: a pre-migration dump is a tier of its own, kept on the host by count and never copied off-site. Full dumps' retention no longer counts it, and "Every Verified Dump Is Copied Off-Site" is replaced by "Inventory And Full Dumps Are Copied Off-Site".
- `collection-deploy`: "A Migration Dump Only When Migrating" names pre-migration dumps where it said full dumps.

## Impact

- **Code:** `db/backup.py` (the third tier, `scheduled_kinds()`, an optional interval), `db/offsite.py` (copies `scheduled_kinds()`), `db/migrations/env.py` (takes the pre-migration tier), `config.py` (`backup_keep_premigration`, the comment on the full tier's bound). No schema change, no revision, no new dependency.
- **Role:** no task changes. The comment in `migrate.yml` and the e2e check of the takeover's report (`dump at /backups/premigration/premigration-`) do.
- **Tests:** `tests/test_backup.py`, `tests/test_offsite.py`, `tests/test_config.py`, a test of which tier `alembic upgrade` takes, and the fake log paths in `tests/test_deploy_role_run.py`.
- **Docs:** `CLAUDE.md` (the backup invariant), `src/itgraph/CLAUDE.md` (the rows of `db/backup.py` and `db/offsite.py`), `src/itgraph/README.md` (the tiers, the pointers, the dump before a migration, restoring over production, the off-site pass), `deploy/runbook.md` (Восстановление).
- **Infrastructure repository:** nothing. Its restore catalog reads `weekly/` for `^full-[0-9]{8}T[0-9]{6}Z[.]dump$` and the store under `full/`, and both still hold the full tier alone.
- **Volume:** the backups volume is 20 GiB. At about 0.3 GB a dump: full ≈ 1.5 GB, inventory under 0.03 GB, pre-migration ≈ 1.5 GB, and 0.3 GB for a ciphertext while it uploads. Without this change, 28 days of near-daily migrations come to 8–9 GB.
- **Off-site:** `full/` returns to four or five copies, about 1.5 GB, inside the 3 GB ceiling set for itgraph's copies.
- **Release:** a release of its own, test environment first. It carries no revision, so its own deploy takes no pre-migration dump. The e2e takeover is what exercises the new tier before production, and the first release with a revision after it is the first real one.

## Decision record

Brought in the explore session of 2026-11-17, after the store reached its cap. The operator's words, then:

> Решение принято (вариант A): дамп перед миграцией — отдельный уровень (свой `BackupKind`, свой подкаталог и имя, своя локальная очистка), хранится только на хосте. В B2 уходят только плановый полный дамп раз в 7 дней и inventory, как сейчас. Плановый график считает только плановые полные дампы.

Measured on the production database the same day, aggregates only, for the cost of the decision. A host lost loses what the newest off-site full copy lacks: up to seven days and four hours, plus however long uploads were failing.

| table | rows | as text | on disk | since | last 7 days | last 24 h |
|---|---|---|---|---|---|---|
| `tg_message_metrics` | 3.38 M | 337 MB | 435 MB + 166 MB indexes | 2026-08-03 | 269 k, 27 MB | 31 k |
| `yt_raw_video_stats` | 120 k | ~9 MB | 9 MB + 7 MB indexes | 2026-11-01 | 90 k, 4.8 MB | 17.8 k |

Each snapshot table has a foreign key onto its raw parent (`tg_raw_messages`, `yt_raw_videos`). Readings newer than a full dump therefore cannot be loaded on top of it until their parents are collected again. Adding a table to the inventory tier would not have been enough on its own. The whole of `tg_message_metrics` there would also take an estimated 85–110 MB a copy, about 3 GB across thirty copies, the whole ceiling.

On 2026-11-17 the operator answered the questions of the session:

1. Pre-migration dumps are kept by count alone, as many as recommended.
2. The full tier's 28-day bound stays for good.
3. A full dump taken by hand stays in the full tier: it is copied off-site and resets the schedule.
4. The pre-migration tier has its own `LATEST` pointer.
5. No code and no hand moves for the transition. The seven-day rule for deleting from the store holds for one more week.
6. A host lost loses up to a week of snapshots, accepted (option a).
7. The tier is named `premigration`.

**Not covered by the requirements.** These were chosen here, not by the operator:

- Five pre-migration dumps. The operator asked for the recommendation, which was three to five (design.md, Decision 4).
- `interval` becomes optional on `BackupKind`, and a tier without one is never due (design.md, Decision 3).
- One list of scheduled tiers, shared by the schedule and the off-site pass (design.md, Decision 3).
- `backup_keep_premigration` refuses values under 1 (design.md, Decision 4).
- The tier's directory is `premigration/`, named like the tier (design.md, Decision 2).
- The infrastructure repository's restore catalog needs no change (design.md, Decision 6).
