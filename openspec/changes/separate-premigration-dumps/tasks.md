# Tasks

Tasks marked **(operator)** reach a server, the off-site store or the test environment, and are the operator's. The agent runs no CronJob, sends nothing to the store, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate`, with hooks through `prek`. Every group that changes the role or `e2e.sh` also ends on a green `make test-e2e`.
- No test makes a request. The store is the suite's `FakeB2`, and the PostgreSQL clients are the suite's fakes.
- No bucket name, key, address or host appears in code, tests, docs or commit messages.

## 1. The pre-migration tier

- [ ] 1.1 `config.py` gains `backup_keep_premigration: int = Field(default=5, ge=1)` (design.md, Decision 4). The comment above the full tier's bounds gives their reason as full dumps taken by hand, no longer migrations (Decision 5). The comment above the off-site settings says what is copied: inventory and full dumps. Verify in `tests/test_config.py`: the default is 5, and 0 is refused.
- [ ] 1.2 `db/backup.py` (design.md, Decisions 1–3):
  - `BackupKind.interval` becomes `timedelta | None`, and `is_due` answers `False` for a tier without one;
  - `premigration_kind()`: name and directory `premigration`, the whole database, `backup_keep_premigration`, no interval, no `keep_for`;
  - `scheduled_kinds()` returns the inventory and full tiers, and `due_kinds` reads it;
  - the docstrings of the module, `prune` and `_point_latest_at` describe three tiers, and the passage on a deploy playbook shipping `LATEST` with `copy_links` goes, since no task does that any more.

  Verify in `tests/test_backup.py`, with the scenarios of "Full Dumps Stay Four Weeks", "A Migration's Dump Is A Tier Of Its Own" and "Pre-Migration Dumps Are Kept By Count":
  - a dump in `premigration/` is listed with its stamp, so the name splits where `_timestamp_of` splits it;
  - `due_kinds` never names the pre-migration tier, and `is_due` is `False` for it;
  - a full dump seven days old with a pre-migration dump on each day since: the full tier is due;
  - `prune` of the full tier leaves `premigration/` alone, and pruning the pre-migration tier leaves `weekly/` and `daily/` alone;
  - five pre-migration dumps with the newest 90 days old all remain, and a sixth removes the oldest;
  - a pre-migration dump points `premigration-LATEST.dump` at itself and leaves `full-LATEST.dump` as it was, and a full dump leaves `premigration-LATEST.dump` as it was;
  - a pre-migration dump that fails its read-back raises, and the five before it remain;
  - three pre-migration dumps in a day leave `weekly/` the same files;
  - `test_full_dumps_stay_four_weeks_however_many` rests on full dumps taken by hand: its premise is no longer migrations.
- [ ] 1.3 Docs for the tier:
  - `CLAUDE.md`: the backup invariant says an `alembic upgrade` on the working database first takes a dump of the whole database into a tier of its own, which stays on the host.
  - `src/itgraph/CLAUDE.md`: the row of `db/backup.py`.
  - `src/itgraph/README.md`, «Резервные копии»: a third row in the table of tiers (`premigration/`, the whole database, before each `alembic upgrade` on a database with tables, the five newest); the paragraph on why full dumps are kept by age, its reason now the dumps taken by hand, with a sentence on the `full-…` pre-migration dumps from before the release ageing out of `weekly/`; three pointers instead of two; and where the dump before a migration goes.

  Verify: none of them still says the pre-migration dump shares the full tier or is copied off-site, and `grep -rn "weekly/" src/itgraph/README.md CLAUDE.md src/itgraph/CLAUDE.md` shows no line that says so.

## 2. The migration takes it

- [ ] 2.1 `db/migrations/env.py`: `backup_before_upgrade` takes `premigration_kind()`, and its docstring says where the dump goes and that it stays on the host. `ITGRAPH_SKIP_BACKUP`, the empty-database exception and the `backed up to <path>` line are unchanged. Verify with a test (design.md, Decision 8). It runs `alembic upgrade head` in-process against a URL that is not a scratch database and does not exist, with `run_backup` and `is_empty_database` replaced, and checks:
  - the tiers asked for are exactly the pre-migration tier;
  - a `BackupError` from it refuses the migration with the message that names `ITGRAPH_SKIP_BACKUP`;
  - with `ITGRAPH_SKIP_BACKUP=1`, nothing is asked for.

  If Alembic cannot be driven in-process cleanly, use the subprocess form of `tests/test_guard.py` with the same three checks, and record why.
- [ ] 2.2 The role and its checks:
  - the comment at the top of `migrate.yml` says the dump goes to the pre-migration tier;
  - `deploy/ansible/e2e.sh` checks the takeover's report for `dump at /backups/premigration/premigration-`;
  - the fake logs in `tests/test_deploy_role_run.py` name a path under `/backups/premigration/`.

  Verify: the tests that hold "A Migration Dump Only When Migrating" are green, as are `make ansible-lint` and `make test-e2e`, which shows the takeover's dump under `premigration/`.

## 3. Off-site copies the scheduled tiers

- [ ] 3.1 `db/offsite.py`: `copy_new_dumps` copies `scheduled_kinds()` and lists the store under their prefixes alone (design.md, Decision 3). The module's docstring and `copy_new_dumps`'s say inventory and full dumps, and why a dump with no schedule stays home. Verify in `tests/test_offsite.py`, with the scenarios of "Inventory And Full Dumps Are Copied Off-Site":
  - a pre-migration dump inside the window is not uploaded, and nothing under a `premigration/` prefix is listed or written;
  - in the same run, an inventory dump and a full dump inside the window are uploaded;
  - a full dump at a moment no schedule would pick, one taken by hand, is uploaded;
  - every existing test stays green unedited.
- [ ] 3.2 Docs:
  - `src/itgraph/README.md`, «Копии вне хоста»: each verified inventory and full dump leaves the host, and the dump before a migration does not.
  - `src/itgraph/CLAUDE.md`: the row of `db/offsite.py`.

  Verify: «включая дамп перед миграцией» appears nowhere in the README.

## 4. Restoring by hand

- [ ] 4.1 `src/itgraph/README.md`, «На хосте сбора», and the local restore above it: by hand over production, take the newer of `weekly/full-LATEST.dump` and `premigration/premigration-LATEST.dump`, and after a failed migration the pre-migration one, since `full-LATEST.dump` may be up to a week older (design.md, Decision 6). The streaming command takes the chosen path, and the note on `full-LATEST.dump` being the newest verified full dump says the same of each pointer. Verify: the commands name both paths, and no host, address or production value appears.
- [ ] 4.2 `deploy/runbook.md`:
  - «Восстановление»: the rehearsal still takes the newest full dump from `weekly/` or its copy in the store, and pre-migration dumps stay on the host and are not rehearsed;
  - «Поверх продакшена, руками»: which dump to take, pointing at the README.

  Verify: the runbook and the README say the same about which dump.

## 5. Integration

- [ ] 5.1 The Purpose of `openspec/specs/database-backups/spec.md` says that inventory and full dumps leave the host, where it said every verified dump. A delta cannot change it. Verify: `openspec validate --specs --strict` is green.
- [ ] 5.2 `openspec validate separate-premigration-dumps --strict`, `make validate` and `make test-e2e`. Verify: all green, and the number of collected tests is not lower than before group 1.

## 6. Rollout

A release of its own. It carries no revision.

- [ ] 6.1 **(operator)** Tag, pin and deploy to the test environment, then rehearse a restore from the claim through the infrastructure repository. Verify: the deploy reports no migration, the restore brings up the newest `full-…` dump from `weekly/`, and its trial dump lands in `weekly/`.
- [ ] 6.2 **(operator)** Pin and deploy to production. Verify: the deploy reports no migration, and no file appears in `premigration/`.
## Workflow follow-up

- Archive once 6.2 is done. The two checks below come after it, and the archive does not wait for them.
- **(operator)** For seven days after 6.2, delete from the store only copies outside the seven-day window. No backup run in that week should upload a copy that was deleted.
- **(operator)** At the first release with a revision after this one:
  - its deploy report names `/backups/premigration/premigration-…`;
  - the next backup run's log has no off-site line for it;
  - `full/` in the store gains a copy only when a full dump falls due.
