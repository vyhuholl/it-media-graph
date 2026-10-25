## Context

See [proposal.md](proposal.md), "Why". The requirements are in [specs/collection-deploy/spec.md](specs/collection-deploy/spec.md). What shapes the approach:

- **The playbook is one play, one host, top to bottom.** Tags select tasks and never reorder them, and a failed task removes the host from the rest of the run. Ordering is therefore the file order, and "stop" means a failing task.
- **The restore source is the operator's machine.** `itgraph_dump_src` points at `weekly/full-LATEST.dump` there. Since collection moved to the host, that machine holds only older dumps, and the live data, the snapshots included, exist only on the host and in the host's own `~/itgraph-backups`.
- **`env.py` dumps before every `upgrade` on a working database** ([env.py](../../../src/itgraph/db/migrations/env.py), `backup_before_upgrade`), and `alembic.command.upgrade` always runs `env.py`, so the dump does not depend on anything being pending. `run_backup` prunes the tier right after ([backup.py](../../../src/itgraph/db/backup.py)); `backup_keep_full` is 4.
- **Timers are already running on a live host.** `alerts` and `score` every 15 minutes, `baselines` daily, `backup` every 4 hours. The playbook does not start them on a re-run; it finds them running.
- **Nothing local evaluates a `when`.** `--list-tasks` shows order and tag selection, not what a condition will decide on the host (see "Verification").

## Goals / Non-Goals

**Goals:**

- The full playbook is the one command for a live host, as it already is for a fresh one.
- Every path that changes the database either has a reason the playbook can state, or stops.
- Tag subsets cannot reach a state the full run would refuse: services on a schema that is behind, a migration on a broken database.

**Non-Goals:**

- Restoring from the host's own backups. The broken-database stop names them; using them stays a human procedure (`src/itgraph/README.md`, "Восстановление").
- Making the playbook check-mode safe.
- Changing the base, repo, session or verify sections beyond the report line and `changed_when` of `Install dependencies`.

## Decisions

### 1. A coherent, non-empty database skips the restore instead of stopping the play

The six restore tasks (ship, arrived, is a file, restore, coherent after, nothing dangling) move into one `block` with `when: restore_needed | bool`. A block's `when` is applied to each task inside and evaluated before each one. That is safe here because `restore_needed` is set by `set_fact` before the block and nothing inside changes it. When it is false, one `debug` says the restore was skipped, with the channel count and the flag that would force it.

`Remove the dump from the host` stays outside the block, unconditional as now: it is `state: absent`, and it also clears a dump left by an earlier run that failed before reaching it.

Rejected:

- **Keeping the assert and adding a tag to skip it.** The goal is one command, and a guard the operator has to remember to switch off on every live deploy turns into a guard that gets switched off.
- **`meta: end_host` / `end_play`.** Both end the play without failing it, which would skip the migration and the services: exactly the half-deploy this change removes.
- **`include_tasks` with a `when`.** It works, but tags on an include do not reach the tasks in it without `apply`, and `--list-tasks` cannot show what is inside a dynamic include. One more file, and the preview stops showing what will run.

### 2. A non-empty, incoherent database stops; it is no longer restored over automatically

The dangling-type branch was built for a fresh host after an interrupted `pg_restore --clean`, where restoring again is the fix and costs nothing. On a live host the same branch restores the operator's dump, which is older than the host's own data, so it silently drops everything since that dump, including snapshots that cannot be taken again. And a database in that state should not be migrated or have services started on it either. So the branch becomes an assert after the decision:

| channels | dangling | `force_restore` | outcome |
|---|---|---|---|
| no table, or 0 | — | — | restore |
| N > 0 | 0 | no | skip restore, say so, continue |
| N > 0 | > 0 | no | **stop** |
| any | any | yes | restore (as before) |
| probe failed | — | — | **stop** (decision 3) |

The stop message gives the number of affected columns. It also says the restore source is the operator's dump and older than what this host collected. For a host that has been collecting, it points at restoring from `~/itgraph-backups` on the host per `src/itgraph/README.md`. It names `-e force_restore=true` only for a host that has never collected, where a restore is being re-tried.

`Say so when a broken database is being replaced` goes. With no flag it is no longer what happens, and with the flag the operator already knows.

Rejected: auto-restoring only on a host that has never completed a deploy, recognised by a marker file. It is more precise than a flag only while the marker is right, and it adds a piece of host state that a restore or a reinstall can leave wrong.

### 3. Probes tell "empty" from "unknown"

Today both probes have `failed_when: false`, and `'' | int` is 0. A failed channel count therefore reads as an empty database, the one answer that leads to a restore. A failed coherence check reads as coherent.

- **Channel count, in two questions.** First `SELECT to_regclass('public.channels') IS NULL`: true means no table, which is a fresh database and counts as zero. Only when the table exists is it counted. Two statements because SQL resolves a table name when it parses the statement, so a missing table fails the whole statement, a `CASE` around it included. Both tasks fail on a non-zero exit.
- **Coherence check** fails on a non-zero exit. Its query reads only `pg_catalog`, so it answers on a fresh database too (zero).

Both keep `changed_when: false`. Their failures carry psql's own error, which is the message the operator needs.

### 4. The migration runs only when the database is behind head

Before migrating, `uv run alembic current --check-heads` (alembic 1.18.5; the flag exists since 1.17.1). `current` runs `env.py` with a function named `display_version`, so `env.py`'s direction check matches neither `upgrade` nor `downgrade`, and the probe takes no dump and trips no guard. Its result is read in three ways:

- exit 0 → at head; no migration;
- the database is behind: alembic raises `DatabaseNotAtHead`, a `CommandError`, which its CLI prints as `FAILED: Database is not on all head revisions` before exiting `-1`. That text means behind;
- anything else (no `.env`, Postgres unreachable, a traceback) → stop with that output.

When behind: plain `alembic current` records the starting revision, then `alembic upgrade head` runs, and `env.py` takes its dump as before. The playbook never sets `ITGRAPH_SKIP_BACKUP`. The dump's path is read from the upgrade's stderr, from the line `env.py` logs as `backed up to <path> (<n> bytes)`. `alembic.ini` puts the `alembic` logger at INFO onto a stderr handler, and `alembic.env` is under it.

Then the same `--check-heads` again, this time as the assertion: exit 0, or the deploy stops. When it passes, it prints the current revision, which is the "to" of the report. This replaces `Schema is at head`, which printed and asserted nothing.

Rejected:

- **`upgrade head` every run.** A full dump and a prune per deploy; four re-runs in an evening leave no dump older than the evening.
- **Reading "behind" from any non-zero exit.** It would turn "cannot reach the database" into a migration attempt. The upgrade would fail too, because its dump would, but that is the wrong error, reported one step late.
- **Comparing `alembic current` with `alembic heads` in Jinja.** It parses two formatted outputs to rebuild a comparison alembic already makes.

### 5. The final schema check is also the services gate

The final `--check-heads` and its assertion are tagged `[data, migrate, services]`. They sit in the data section, before `session` and `services` in the file, so under `--tags services` they run first. A schema that is behind then stops the run before `Unit files`. The manual sequence that skips the migration (`--tags repo`, then `--tags services`) now fails at the gate instead of enabling `itgraph-yt-refresh` on a schema without its tables.

As a result `--tags services` reaches the database. It does not start Postgres (that is `data`/`migrate`), so with Postgres down it stops at the gate, which is the right answer for a run that is about to start services needing it.

### 6. The tag map

| task | tags | when |
|---|---|---|
| Start Postgres · Wait for Postgres | `data, migrate` | |
| Bot role exists | `data` | |
| Is there a channels table · How many channels | `data, migrate` | count: table exists |
| Is it coherent | `data, migrate` | |
| Decide (`restore_needed`, broken) | `data, migrate` | |
| Stop: broken and not forced | `data, migrate` | |
| Say: restore skipped | `data` | not `restore_needed` |
| block: restore and its checks | `data` | `restore_needed` |
| Remove the dump | `data` | |
| Bot role password | `data` | |
| Is the schema at head (probe) | `data, migrate` | |
| Where the schema starts · Bring it to head · Where the dump went | `data, migrate` | behind |
| Schema is at head · Nothing starts on a schema that is behind (the gate) | `data, migrate, services` | |
| Say what the migration did | `data, migrate` | the upgrade ran |
| Baselines probe · First baseline refresh | `data` | no completed run |

`--tags data` keeps meaning everything about the database, so a fresh host restored with `--tags data` still ends at head. `--tags migrate` runs the probes and the broken-database stop but never the restore: with `force_restore` under `--tags migrate`, a broken database still stops, because the restore that would fix it is not part of that selection. The stop's condition says so: `force_restore` lifts it only when the restore block is selected too. That is read from the magic variables `ansible_run_tags` (`["all"]` when `--tags` is not given) and `ansible_skip_tags`. The block counts as selected when `all`, `tagged` or `data` is among the run tags and `data` is not among the skipped ones.

`--tags migrate` against an empty database finds no tables, and `env.py` then migrates without a dump, creating an empty schema at head. The next full run counts zero channels and restores, so nothing is lost. This is documented, not prevented.

`First baseline refresh` keeps its own condition (no completed run) and its `data` tag only. It is a state check, not a path check: a live host with no completed refresh needs one whether or not anything was restored.

### 7. `watch` and `bot` are restarted by a task, not a handler

After `Long-running services`, a task with `state: restarted` loops over `itgraph-watch` and `itgraph-bot`, and runs for an item when any of these holds:

- this run fetched the code: `Clone the repository` ran and registered a result, changed or not;
- `.env` changed: `Configuration` registered and `changed`;
- that unit's own file changed: the item's result in `Unit files`.

**Fetched, not changed.** The first version restarted only when the checkout or the dependencies changed in the same run. The operator's first run on the live host (2026-10-25) showed why that is not enough. The run pulled new code and stopped at the session, before the services. The next run's checkout then reports nothing new, so a per-run "changed" loses exactly the restart the failed run still owes, and the report would say `restarted: none` over services running the old code. Rejected alternatives:

- a marker on the host recording what the services were started on: precise, but one more piece of host state to keep right;
- keeping per-run detection with a `restart_services` flag: the trap stays silent.

The cost is a restart of both services on every full run, a few seconds of polling; the design already accepted it for docs-only commits.

It skips a unit that `Long-running services` started in this same run, so a fresh host does not start the bot and then bounce it a second later. Every registered value is read through `default` or `is defined`. A run that neither fetched code nor wrote anything (`--tags services`, `--tags verify`) restarts nothing, and neither does `--tags repo`, because the restart task is in `services`.

`Install dependencies` also gets its `changed_when` fixed, though the restart no longer reads it. `uv sync` prints `Resolved N packages` on every run (observed locally on 2026-10-25: `Resolved 123 packages`, `Checked 83 packages`, nothing installed), so the task reported a change every time. It becomes `Installed` or `Uninstalled` in stderr, the two lines uv prints only when the environment changed.

Not a handler: handlers ignore tags and run at the next flush or at the end of the play. Under `--tags repo`, a restart notified by the checkout would then run at the end of that play, with the schema still behind, which is the exact state decision 5 forbids. Not unconditional either: `--tags services` is how a unit tweak is applied, and bouncing `watch` for it costs a poll for nothing.

Timers are not restarted. Their services are oneshots and start on the deployed code at their next firing.

### 8. The report says what this run did

`Report` gains:

- the restore: `restored`, `skipped (N channels kept)`, or `not run`;
- the migration: `A → B`, `at head`, or `not run`;
- the dump path, when one was taken;
- the restarted services, or `none`.

Every field reads its registered source through `default`, so `--tags verify` reports "not run" rather than raising on an undefined variable or implying a restore that did not happen.

The migration is also announced where it happens. `Say what the migration did`, right after the gate, gives the revision before and after and the dump path, tagged `[data, migrate]`, when the upgrade ran. `--tags migrate` never reaches the report, and the requirement to say where the pre-migration dump went holds for it too. Found by `/opsx:verify`.

### 9. The session is looked for on the host first

Added after the operator's first run on the live host, 2026-10-25. That run passed the data tasks and stopped at `Telegram session`: `Could not find or access '~/Documents/it-media-graph/itgraph.session' on the Ansible Controller`. Once collection moved, the session lives on the host and nowhere else, which is as it should be: a second copy on the operator's machine is a second full key to the account. `copy` with `force: false` transfers only when the destination is missing, but its action still resolves `src` on the controller before it looks at the host. So a task written to leave the host's session alone failed for want of a file it had no use for.

The section becomes three tasks, all tagged `session`:

1. `stat` of the session path on the host;
2. when it exists, an assert that it is a regular file with something in it. An empty or non-file session would be "kept" and collection would stop on it, so the deploy stops instead and replaces nothing;
3. when it does not exist: an assert that `itgraph_session_src | expanduser is file`, which is a test evaluated on the controller's filesystem, then the copy as before.

The failure message for the third case keeps the original warning: copy the session from the machine that has been collecting, never create one with `itgraph login`.

Rejected:

- **Copying the session back to the operator's machine** to satisfy the task. It is an account key; one copy is better than two.
- **`ignore_errors` on the copy.** It would hide the one case that matters, a fresh host with no session anywhere.
- **A flag that enables the session step.** Easy to forget on exactly the fresh host that needs it.

### 10. The window for running timers is accepted

```
 Clone ─ python ─ uv sync ─ .env ─ Postgres ─ probes ─ [dump ─ upgrade] ─ gate ─ services
   │                                                                      │
   └──────── new code on disk, old schema in the database ────────────────┘
             alerts / score / baselines / backup may fire here
```

A timer firing inside the window runs the new code against the old schema and may fail once. The next firing runs after the migration. The manual path had the same window. Stopping the timers around it was rejected: a deploy that fails midway would leave `itgraph-backup.timer` stopped, and no backups looks the same as a quiet backup log. The README says the window exists.

## Risks / Trade-offs

- [The operator expects a restore and gets a skip] → The skip is a `debug` at the decision and a line in the report, with the count and the flag. A restore that did not happen is printed, not inferred.
- [A fresh host after an interrupted restore now needs a flag] → The stop message says so in that case's own words.
- [`--check-heads` output changes in a future alembic] → The "behind" text is matched exactly, and anything unrecognised stops the deploy. A changed message fails safe (stop) instead of migrating or skipping.
- [Every full run restarts `watch` and `bot`, a re-run that fetched nothing new included] → A restart costs a few seconds of polling: the process exits on SIGTERM between iterations, the lease goes with it, and `TimeoutStopSec` bounds a stuck stop. Restarting only on a change loses the restart a failed run owes (decision 7).
- [`--tags services` now needs Postgres up] → Intended: a run that starts services needing the database finds out first.
- [The timer window (decision 10)] → Accepted and documented.
- [No local check evaluates a condition] → The operator's first run is the verification, and its expected output is written down in tasks.md.

## Verification

| check | what it establishes | what it does not |
|---|---|---|
| `make ansible-lint` | syntax, FQCN, `no-changed-when`, `risky-shell-pipe` and the rest of the profile | anything about conditions or order |
| `ansible-playbook --syntax-check` | the playbook parses; keywords are valid where used | `when`, Jinja, variable values |
| `--list-tags` | `migrate` exists | which tasks carry it |
| `--list-tasks --tags X` | the static order and tag selection: under `migrate`, Postgres → probes → decision → migration → gate and nothing from `services`; under `services`, the gate before `Unit files` and no restore task | what a `when` decides: the restore block is listed although on a live host it is skipped |

`--list-tasks` runs locally against `deploy/inventory.ini.example` with the fake `deploy/.ansible-lint-vault-pass`, reaching no host (checked 2026-10-25). What only the operator's run settles: how the probes' output parses, the `--check-heads` text and exit on the host, the dump line, and the restart conditions.

## Facts checked against the documentation, 2026-10-25

- **Tags** (docs.ansible.com, playbooks_tags): a task runs when any of its tags is selected; tags on a block are inherited by its tasks; handlers ignore tags; `--list-tasks` with `--tags` previews selection without running; `always` runs unless skipped.
- **Blocks** (playbooks_blocks): a `when` on a block is applied to the tasks and evaluated before each.
- **Magic variables** (special_variables): `ansible_run_tags` holds `--tags`, `["all"]` when it is not passed; `ansible_skip_tags` holds `--skip-tags`.
- **Execution** (playbooks_intro): tasks run top to bottom; a failed task removes the host from the rest of the playbook.
- **`meta`** (module doc): `end_play` ends the play for all hosts without failing; `end_host` does the same for one host.
- **`systemd_service`** (module doc): `started`/`stopped` are idempotent; `restarted` always bounces the unit.
- **`command`** (module doc): check mode is partial; without `creates`/`removes` the task is skipped in check mode.
- **`git`** (module doc): returns `before` and `after`.
- **`copy`** (module doc): with `force: false` the file is transferred only when the destination does not exist. The operator's run on 2026-10-25 showed the action still requires `src` on the controller in that case.
- **The `file` test** (`ansible-doc -t test ansible.builtin.file`): checks a path on the controller's filesystem.
- **alembic 1.18.5** (source in `.venv`): `current --check-heads` raises `DatabaseNotAtHead`, a `CommandError`, printed as `FAILED: …` with `sys.exit(-1)`; `current`'s environment function is `display_version`; `upgrade` always calls `run_env`.
- **uv** (observed locally): a no-op `uv sync` prints `Resolved` and `Checked` and nothing else.

## Migration Plan

1. One commit on `master`: playbook, README, the comments.
2. `make validate`, then the local checks above.
3. The operator runs `ansible-playbook deploy/playbook.yml` against the live host. The first run, on 2026-10-25, passed the data tasks and stopped at the session (decision 9), before any unit was touched. Expected from the next one, the schema being at head already from the manual upgrade, and the host's session kept with nothing copied: restore skipped with 3,818 channels; no migration; no new file in `~/itgraph-backups/weekly/`; `watch` and `bot` restarted, because the run fetched the code, which also settles the restart the first run owed; verify passes; the report says all of it.

Rollback: revert the commit. The four-step manual path works with either version of the playbook.

## Assumptions

Confirmed by the operator on 2026-10-25:

- a live host is one whose database holds channels and is coherent, with no marker of its own;
- one host in `collector`;
- the playbook never sets `ITGRAPH_SKIP_BACKUP` or `ITGRAPH_ALLOW_DESTRUCTIVE`;
- the restore source stays `itgraph_dump_src`;
- the host has disk for one more full dump (pruning follows it, so five exist briefly);
- the timer window is accepted;
- revisions are linear;
- `--tags migrate` alone migrates whatever checkout is already there.

Confirmed by the operator on 2026-10-25, after the first run on the live host:

- the host's session is kept and not required on the controller; an empty or non-file session on the host stops the deploy, and so does no session anywhere (decision 9);
- `watch` and `bot` are restarted on every run that fetched the code, not only on one that changed it (decision 7);
- both are fixed in this change, not a separate one.

Decided in this design, open to veto:

- `.env` and a unit's own file also trigger the restart, beside fetching the code;
- a unit started in the same run is not restarted again;
- the starting revision is read with a plain `alembic current`, only when behind.

## Open Questions

None that change the specs or the tasks.
