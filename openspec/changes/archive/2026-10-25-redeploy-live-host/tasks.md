Local listing commands below use the example inventory and the fake vault password, and reach no host:

```bash
L() { ANSIBLE_VAULT_PASSWORD_FILE=deploy/.ansible-lint-vault-pass uv run ansible-playbook -i deploy/inventory.ini.example deploy/playbook.yml "$@"; }
```

`L --tags X --list-tasks` below means that function. No task here runs the playbook against a host.

## 1. Probes tell empty from unknown

- [x] 1.1 Split `How much is already in this database` in two: `Is there a channels table` (`SELECT to_regclass('public.channels') IS NULL`), then the count, run only when the table exists. Both fail on a non-zero exit, so `failed_when: false` goes; both keep `changed_when: false`. A comment says why there are two statements: a missing table fails the whole statement at parse time, a guarding `CASE` included. Verify `make ansible-lint` passes and `L --tags data --list-tasks` lists both in that order.
- [x] 1.2 `Is what is already here internally consistent` fails on a non-zero exit (drop `failed_when: false`); the comment adds that its query reads only `pg_catalog`, so it answers on an empty database too. Verify `make ansible-lint`.
- [x] 1.3 Add `migrate` to `Start Postgres`, `Wait for Postgres`, both channel tasks, the coherence check and `Decide whether to restore` (all keep `data`). Verify `L --tags migrate --list-tasks` lists them in file order, and `Bot role exists before anything references it` is not among them.

## 2. The restore decision

- [x] 2.1 `Decide whether to restore` sets three facts: `restore_needed` (no table or zero channels, or `force_restore`), `database_broken` (channels > 0 and dangling > 0), and `restore_selected` (`all`, `tagged` or `data` in `ansible_run_tags`, and `data` not in `ansible_skip_tags`). Comment: `restore_selected` exists so that `force_restore` cannot lift the broken-database stop under a tag selection that will not run the restore (design decision 6). Verify `make ansible-lint`.
- [x] 2.2 Replace `Refuse to restore over existing data` with `A broken database stops the deploy`: an assert, tags `[data, migrate]`, failing when `database_broken` unless `force_restore` and `restore_selected`. Its message, per design decision 2: the number of affected columns; that the restore source is the operator's dump and older than what this host collected; for a host that has been collecting, restoring from `~/itgraph-backups` on the host per `src/itgraph/README.md` ("Восстановление"); `-e force_restore=true` only for a host that never collected. Remove `Say so when a broken database is being replaced`. Verify `make ansible-lint`, and read the message against the spec scenario "A broken live database".
- [x] 2.3 Move `Ship the dump`, `The dump arrived`, `The dump is a real file with something in it`, `Restore`, `The restored database is coherent` and `Nothing points at a type that is gone` into one block, `when: restore_needed | bool`, tag `data`, dropping their own `when`s; the block's comment carries the "one destructive step" paragraph. `Remove the dump from the host` stays outside and unconditional. Verify `L --tags data --list-tasks` lists the six inside the block in the same order, and `L --tags migrate --list-tasks` lists none of them.
- [x] 2.4 Add `Say so when the restore is skipped`: a `debug`, tag `data`, `when: not restore_needed | bool`, naming the channel count, saying the schema is coherent, and that `-e force_restore=true` replaces them. Verify `make ansible-lint`, and read the message against the spec scenario "A full re-run on a live host".
- [x] 2.5 Docs: in `deploy/README.md`, "Чего плейбук не делает намеренно": the restore over a non-empty, coherent database is skipped with a message and the deploy goes on; a non-empty database with dangling types stops the deploy, and on a host that has been collecting the fix is the host's own backups; `-e force_restore=true` as before. In the playbook header, the "restore over a database that already has rows" bullet says the same in one sentence. Verify by reading both against the spec requirements "Re-run Keeps A Coherent Inventory" and "An Incoherent Database Stops The Deploy".

## 3. The migration runs only when behind, and gates the services

- [x] 3.1 Add `Is the schema at head`: `{{ uv }} run alembic current --check-heads`, tags `[data, migrate]`, `changed_when: false`, `failed_when` true unless the exit is 0 or the output contains `Database is not on all head revisions`. Registered as `schema_check`; because of that `failed_when`, a non-zero `schema_check.rc` that gets past it can only mean "behind", so the later tasks test `schema_check.rc != 0` and no separate fact is set. Comment: `current` takes no dump (`env.py` dumps only on `upgrade`); the matched text is alembic's own `DatabaseNotAtHead`; anything else stops with its own output (design decision 4). Verify `make ansible-lint` and `L --tags migrate --list-tasks` lists it after the broken-database stop.
- [x] 3.2 When behind (`schema_check.rc != 0`): `Where the schema starts` (`alembic current`, registered), then `Bring the schema to head`, registered, `changed_when: true`, both `[data, migrate]`. Then `Where the pre-migration dump went`: a `set_fact` taking the path from the upgrade's stderr with `regex_search('backed up to [^ ]+')` and stripping the prefix with `regex_replace`. No backslashes, so no escaping between YAML and a Jinja string literal, where `\1` would not survive. Replace the migration's comment: it no longer says the host is fresh; it says a re-run at head migrates nothing and therefore dumps and prunes nothing, and why that matters (four kept). Verify `make ansible-lint` and `L --tags migrate --list-tasks` order: probe → starts → upgrade → dump path.
- [x] 3.3 Replace `Schema is at head` with the gate: `{{ uv }} run alembic current --check-heads`, registered as `alembic_current`, tags `[data, migrate, services]`, failing on a non-zero exit with a message saying the schema is behind the deployed code and to run the migration (`--tags migrate`) before the services. Comment: why it carries `services` (design decision 5). Verify `L --tags services --list-tasks` lists it before `Unit files`, and `L --tags migrate --list-tasks` lists it after `Bring the schema to head`.
- [x] 3.4 Docs: `deploy/README.md`, "Отдельные части": `--tags migrate` (starts Postgres, checks the database, migrates when behind; never restores, never touches units; against an empty database it creates an empty schema, and the next full run still restores); `--tags services` now refuses on a schema behind head; `--tags data` still restores and migrates. Verify by reading against the spec requirements "Migration Runnable On Its Own" and "Schema At Head Before Services".

## 4. The services run the deployed code

- [x] 4.1 `Install dependencies`: `changed_when` becomes `'Installed' in uv_sync.stderr or 'Uninstalled' in uv_sync.stderr`, with a comment that `Resolved` is printed on every run. Verify `uv sync` locally prints neither word when nothing changes (it printed `Resolved 123 packages`, `Checked 83 packages` on 2026-10-25), and `make ansible-lint`.
- [x] 4.2 Register `Clone the repository` (`repo_checkout`), `Configuration` (`env_file`), `Unit files` (`unit_files`) and `Long-running services` (`long_running`). Verify `make ansible-lint`.
- [x] 4.3 Add `Restart what is running old code` after `Long-running services`, tag `services`, `become: true`, `state: restarted`, looping over `itgraph-watch` and `itgraph-bot`. Runs for an item when the checkout changed, `uv_sync` changed, `env_file` changed, or that item's `.service` changed in `unit_files`, and not when `long_running` started it in this run. Every registered value read through `default`. Register it for the report. Comment: why a task and not a handler (handlers ignore tags; under `--tags repo` a notified restart would run at the end of the play on the old schema) and why not unconditional (design decision 7). Verify `L --tags services --list-tasks` lists it after `Long-running services`, and `L --tags repo --list-tasks` does not list it.
- [x] 4.4 Docs: `deploy/README.md` says when `watch` and `bot` are restarted (code, dependencies, `.env`, their unit file), that timers are not and why, and that `--tags services` or `--tags config` alone restarts nothing. The playbook header gains one line under "Usage" for `--tags migrate`. Verify by reading against the spec requirement "Services Run The Deployed Code".

## 5. The report

- [x] 5.1 `Report` gains the restore (`restored` / `skipped (N channels kept)` / `not run`), the migration (`A → B` / `at head` / `not run`), the dump path when one was taken, and the restarted services (`none` when none). Each field reads its source through `default`, so `--tags verify` alone says `not run`. Verify `make ansible-lint`, and read each expression's `default` chain against the spec requirement "The Deploy Reports What It Did".
- [x] 5.2 Docs: `deploy/README.md`, "Что проверяется в конце": what the report line says and that "skipped" is the normal answer on a live host. Verify by reading.

## 6. The window, and the whole

- [x] 6.1 Docs: `deploy/README.md` gains a short note on the window between the checkout and the migration in which already-running timers may fire new code on the old schema once, and why the playbook does not stop them (design decision 10). Verify by reading.
- [x] 6.2 `make validate` green: lint, typecheck, test, ansible-lint.
- [x] 6.3 `L --syntax-check` passes; `L --list-tags` includes `migrate`; `L --list-tasks` for no tags and for `--tags data`, `--tags migrate`, `--tags services`, `--tags repo` and `--tags verify` each match the tag map in design.md decision 6. Verify by comparing each listing with that table line by line.
- [x] 6.4 `openspec validate redeploy-live-host --strict` green.

## 7. The host's session is kept, and not required on the controller

Added after the operator's first run on the live host (design decision 9).

- [x] 7.1 `session_path` as a play variable; `Is there a session on the host` (`stat`, tag `session`); `The session on the host is a real file with something in it` (assert `isreg` and `size > 0`, when it exists), whose message says nothing replaces it on its own. Verify `make ansible-lint` and `L --tags session --list-tasks` lists both first.
- [x] 7.2 `There is a session to copy` (assert `itgraph_session_src | expanduser is file`, when the host has none), with the message from the spec scenario "No session anywhere", keeping the `itgraph login` warning; `Telegram session` copies only when the host has none. Verify `make ansible-lint`, `L --tags session --list-tasks` order (stat → host assert → controller assert → copy), and that `is file` with `expanduser` is true for an existing `~/…` file and false for a missing one, evaluated locally.
- [x] 7.3 Docs: `deploy/README.md`, "Не запускает `itgraph login`": the session is copied only to a host that has none, the operator's machine needs no copy once the host has one, and the two stops. Verify by reading against the spec requirement "Re-run Keeps The Host's Session".
- [x] 7.4 `make validate` green, and `openspec validate redeploy-live-host --strict` green.

## 8. A restart a failed run owes is not lost

Added after the operator's first run on the live host (design decision 7, "Fetched, not changed").

- [x] 8.1 `Restart what is running old code`: `runs_old_code` becomes `repo_checkout is defined`, or `.env` changed, or the unit's own file changed; the comment says why a fetch counts and not only a change. The `Install dependencies` comment no longer claims its result decides the restart. Verify `make ansible-lint`, and the copied expression evaluated locally: an unchanged fetch restarts, nothing registered restarts nothing, a changed `.env` alone restarts, a unit just started is not restarted again.
- [x] 8.2 Docs: `deploy/README.md`, "Когда перезапускаются `watch` и `bot`": every run that fetched code restarts them, why, and the cost. Verify by reading against the spec requirement "Services Run The Deployed Code".
- [x] 8.3 `make validate` green, `L --tags services --list-tasks` and `L --tags repo --list-tasks` unchanged from 6.3, and `openspec validate redeploy-live-host --strict` green.

## 9. Findings of `/opsx:verify`

- [x] 9.1 `Say what the migration did`: a `debug` right after the gate, tags `[data, migrate]`, when the upgrade ran, naming the revision before and after and the dump path, or that no dump was taken because the database had no tables. Verify `make ansible-lint`, `L --tags migrate --list-tasks` lists it after `Nothing starts on a schema that is behind`, and its message expression evaluated locally on a copy, both with a dump and without one.
- [x] 9.2 The playbook header says the full run is the command for a fresh host and for one already collecting, and that the session is copied only to a host that has none. The first line of `deploy/README.md` says the same. Verify by reading.
- [x] 9.3 The comment above `Restart what is running old code` reflowed, naming `is defined` beside `default`. Verify by reading.
- [x] 9.4 `make validate` green and `openspec validate redeploy-live-host --strict` green.

## Workflow follow-up

- Commit to `master` when the operator asks.
- The operator runs `ansible-playbook deploy/playbook.yml` against the live host. Expected, the schema being at head already from the manual upgrade:
  - `Say so when the restore is skipped` reports 3,818 channels (or the current count);
  - `Is the schema at head` passes, so `Bring the schema to head` is skipped;
  - `~/itgraph-backups/weekly/` holds the same files as before the run;
  - the session tasks find the host's session and copy nothing;
  - `itgraph-watch` and `itgraph-bot` are restarted, because the run fetched the code — which also settles the restart the first, failed run owed;
  - verify passes, and the report says all of the above.
- If any of it differs, stop and read why before running again. In particular, never re-run with `-e force_restore=true` against the live host to get past a failure.
- `/opsx:verify`, then `/opsx:archive`.
