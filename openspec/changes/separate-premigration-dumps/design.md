# Design

## Context

See proposal.md for why. What shapes the approach:

- **A tier is a directory and a name.** `BackupKind` carries `subdirectory` and `name`, and `existing()` lists `<name>-*.dump` in `<root>/<subdirectory>`. `is_due`, `prune`, the `LATEST` pointer and the off-site pass all see dumps only through it. A dump with its own directory and name is invisible to every other tier, with no condition anywhere.
- **The off-site pass and the schedule each list their tiers.** `due_kinds` iterates `(inventory_kind(), full_kind())`, and so does `copy_new_dumps`. A tier in neither list is neither scheduled nor copied.
- **`_timestamp_of` splits a file name at its first hyphen.** `inventory-20261117T100000Z` reads as `inventory` and the stamp. A tier name with a hyphen in it would leave a stamp that does not parse. The file would be skipped with a warning: never pruned, never pointed at.
- **The migration's dump is one call.** `env.py`'s `backup_before_upgrade` runs `run_backup(kinds=[full_kind()])`, which takes, verifies, points and prunes. It refuses the migration if that fails, unless `ITGRAPH_SKIP_BACKUP=1`, and takes nothing on a database with no tables. It logs `backed up to <path>`. `migrate.yml` reads the path out of that line for the deploy's report, and the e2e takeover checks it.
- **Restores take a path.** The role's `restore` entry point is given `itgraph_restore_from`. The infrastructure repository chooses it: the newest `^full-[0-9]{8}T[0-9]{6}Z[.]dump$` in `weekly/` on the backups claim, or the newest such copy under `full/` in the store.
- **The full tier's 28 days were added for migrations.** Commit 8cbd0bb, after three migrations on 2026-11-01 pushed every weekly dump out of a tier kept by count.

## Goals / Non-Goals

**Goals:**
- A pre-migration dump is invisible to the full tier: its schedule, its bounds, its pointer and its copying.
- The pre-migration tier's space on the host is bounded however often migrations come.
- Nothing that reads `weekly/` or `full/` changes: the role's restore, the infrastructure repository's catalog, the e2e checks of `full-LATEST.dump`.

**Non-Goals:**
- Any change to what the store keeps or how: lifecycle rules, prefixes and the host's key stay as they are.
- A command to take a pre-migration dump by hand. `itgraph backup` keeps `--full` and `--inventory`.
- Moving or renaming dumps already on the host.

## Decisions

### 1. A third tier, not a mark inside `weekly/`

`premigration_kind()` is a `BackupKind` like the other two. `env.py` passes it where it passed `full_kind()`. `take_backup`, `prune` and `_point_latest_at` work on it unchanged.

Rejected:
- **A suffix on the full name**, such as `full-<stamp>-premigration.dump`. The glob `full-*.dump` matches it, so every consumer would need a condition to leave it out. That is the coupling this change removes.
- **A marker file beside the dump.** Every consumer would have to learn it. A dump whose marker went missing would quietly become a scheduled one again: copied, and holding back the schedule.
- **The same directory under another name.** It works through the glob, but two tiers would share one directory, and an operator listing `weekly/` would see dumps that are not weekly.

### 2. `premigration`, in `premigration/`

The name has no hyphen, because `_timestamp_of` splits at the first one. `pre-migration-<stamp>` would read `migration-<stamp>` as the stamp, find none, and be skipped with a warning: never pruned, never pointed at. A test holds the new tier's dumps to being listed with their stamps.

The directory is named for the tier. `daily/` and `weekly/` are named for their rhythm, and this tier has none.

### 3. One list of scheduled tiers, and an optional interval

`scheduled_kinds()` returns `(inventory_kind(), full_kind())`. `due_kinds` and `copy_new_dumps` both read it.

What the schedule takes is exactly what leaves the host, and that is deliberate. The store's budget is the schedule times the lifecycle. A tier with no schedule has no bound on how many copies it sends, which is how this change came about.

`BackupKind.interval` becomes `timedelta | None`. `None` means the tier is taken only when something asks for it, and `is_due` answers `False` for it. The pre-migration tier has no interval.

Rejected:
- **A placeholder interval** for the pre-migration tier. It is a number that means nothing, read by nothing, and it invites someone to schedule the tier by adding it to a list.
- **An `offsite` flag per tier.** It is a second axis that today always equals "scheduled". Keeping both invites a scheduled tier that stays home, or a copied one with no schedule, with nobody sizing the store for it.

### 4. Five pre-migration dumps, by count alone

`backup_keep_premigration: int = Field(default=5, ge=1)`, and `keep_for=None`.

- **By count**, because migrations come in bursts: 15 in the 17 days before 2026-11-17, three on 2026-11-01. A bound by age holds as many dumps as a burst brings.
- **Five** keeps the dump from before each of the last five deploys that migrated, so a day of three still leaves the two from before it. At about 0.3 GB that is 1.5 GB of the 20 GiB volume. History older than that is the full tier's four weeks.
- **At least one**, because `prune` keeps `existing(...)[keep:]`. A keep of 0 would delete the dump `run_backup` has just taken. The two existing keep settings share that gap. They are left as they are here, since this change does not touch them.

Rejected:
- **By age.** No bound under a burst.
- **By count and age together.** Age adds no bound that the count does not already give. After a quiet month it would delete the last rollback point before the next migration needed it.

### 5. The full tier keeps its 28 days

The operator kept it for good. Its reason is no longer migrations but full dumps taken by hand: `itgraph backup --full`, which the README advises after a deploy, `make backup-full` on the Mac, and the restore's trial dump in a test environment. A few of those in a day would push the weekly dumps out of a tier kept by count.

It also carries the transition. The pre-migration dumps already in `weekly/` are `full-…` files. Kept by count, the first prune after the release would keep the four newest of them and delete the weekly ones, the failure of 2026-11-01 once more. The docstring of `prune` and the comment in `config.py` give the new reason.

### 6. A pointer per tier; restores unchanged

`take_backup` points each tier's `LATEST` after verifying, so the new tier gets `premigration/premigration-LATEST.dump` with no new code.

The role's restore and the infrastructure repository's catalog are unchanged. The catalog reads the newest `full-…` in `weekly/`, and `full/` in the store, and after this change those hold the full tier alone. A restore rehearsal from the claim and one from the store now bring up the same tier.

The pre-migration tier is not added to the catalog. Requirement 4 of `docs/INFRASTRUCTURE.md` asks that a backup brings a database up in a test environment. The full tier proves that, and it is also the tier that survives a host lost.

Restoring over production by hand is the one place the choice matters. The newest dump may now be the pre-migration one, up to a week newer than `full-LATEST.dump`. The README says to take the newer of the two pointers, and after a failed migration the pre-migration one.

### 7. No code for the transition

Pre-migration dumps taken before the release stay in `weekly/` as `full-…`. They are whole-database dumps, and the full tier's bounds age them out within 28 days. Until the newest of them is a week old:

- `is_due` may read it as the last full dump, so the first scheduled one comes up to seven days after it. That dump is in the store, unless its upload failed, and then every run tries it again.
- `copy_new_dumps` copies any of them the store lacks while it is inside the window. So the operator keeps deleting from the store only copies outside the window, as now, for one more week.

Rejected:
- **Moving them into `premigration/` by hand.** Telling them apart is a guess by time of day: scheduled runs start at :40 every fourth hour, Moscow time. A wrong guess moves a weekly dump out of its tier.
- **A cutoff date in code.** Transition code that outlives its week.

### 8. Where the choice of tier is tested

The e2e takeover proves the whole path: the migration pod writes the dump, and the report names `/backups/premigration/premigration-…`. `make test-e2e` is outside `make validate`, so a unit test checks that `alembic upgrade` asks for the pre-migration tier. It drives Alembic in-process against a URL that is not a scratch database and does not exist, with `run_backup` and `is_empty_database` replaced.

## Risks / Trade-offs

- **[A host lost loses up to seven days and four hours]** of snapshots, and of everything else outside the inventory tier → accepted by the operator, with the numbers in the proposal. Before this change, frequent migrations made the window shorter by accident; by design it was always a week.
- **[A restore by hand after a failed migration takes `full-LATEST.dump`]** and loses the days since → the README and the runbook name both pointers and say which one to take.
- **[Damage from a migration noticed five migrating deploys later]** → the dump from before it is gone by then. The full tier's four weeks are the fallback, at the cost of the days in between.
- **[A host lost right after a migration]** takes its pre-migration dump with it → the off-site full copy, at most a week older, is the fallback, as for any host lost.
- **[The pin is rolled back]** → the old code lists only `daily/` and `weekly/`, so `premigration/` stays on the volume, unpruned, at most 1.5 GB. It is removed by hand if the rollback stays.
- **[The operator's Mac]** runs the same code. Its pre-migration dumps go to `~/itgraph-backups/premigration/`, and nothing there is copied off-site.

## Migration Plan

1. `make validate` and `make test-e2e` green. Tag, then pin and deploy to the test environment. The release carries no revision, so the deploy takes no pre-migration dump. A restore rehearsal from the claim works as before.
2. Pin and deploy to production. No migration runs, and nothing changes on the volume until the next dump.
3. For a week, delete from the store only copies outside the seven-day window.
4. The first release with a revision after this one is the tier's first real dump. Its deploy report names `/backups/premigration/premigration-…`. The next backup run's log has no off-site line for it, and `full/` in the store gains nothing until a full dump falls due.

Rollback: revert the pin. Nothing in the store depends on this release.
