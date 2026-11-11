# Tasks

Every task that changes code ends on a green `make validate`. Nothing here names a host, an address or the infrastructure repository, and every password in a test or an input set is synthetic, spelled `fake…`.

## 1. The revision

- [x] 1.1 Move `MIGRATION_LOCK` from `tests/test_metrics_role.py` to `tests/conftest.py`, with a helper that runs Alembic under it; `test_metrics_role.py` uses it. Verify: `make test` green, and `make test WORKERS=0` green.
- [x] 1.2 The revision of design.md, Decisions 3 and 4, after the current head (`uv run alembic revision -m "reader database role"`): the role `NOLOGIN` in a `DO` block, `CONNECT`, `USAGE`, `SELECT` on all tables, default privileges with no `FOR ROLE`, the guards; the downgrade in reverse, keeping a role still in use. The docstring says why it grants schema-wide where the bot's revision enumerates, and that the guards are not a boundary. Verify: `uv run alembic upgrade head` then `downgrade -1` then `upgrade head` on a scratch `*_test` database, each succeeding, and `alembic upgrade head --sql` reads as design.md says.
- [x] 1.3 `tests/test_reader_role.py`, on a database of its own built under the shared lock, through `SET LOCAL ROLE`: `SELECT` on every table and view `information_schema` lists at run time; a table the owner creates after the migration, read with no grant of its own; `INSERT`, `UPDATE`, `DELETE` and `TRUNCATE` on every table refused with 42501; `nextval` and `CREATE TABLE` refused; `pg_roles` shows `rolcanlogin` false, `rolconnlimit` 4 and the four settings. Verify: the module green under `make test`, and each write test failing when its revoke is made a grant by hand on that database.
- [x] 1.4 The downgrade, on two databases of its own: after downgrading one, nothing in it refers to the reader (no table, schema or database privilege, no row in `pg_default_acl`), the downgrade succeeded, the role exists and still reads the other. Verify: the test green, and failing when the downgrade's default-privilege revoke is removed.
- [x] 1.5 `src/itgraph/README.md`: a short section beside the bot's and the exporter's roles on the reader: what it reads, that it writes nothing, the guards and why they are not a boundary, that no process connects as it. Verify: it reads true against the revision.

## 2. The input and the password

- [x] 2.1 `meta/argument_specs.yml` gains `itgraph_reader_postgres_password` (`required: true`, `no_log: true`, empty meaning `NOLOGIN`); `tasks/inputs.yml` checks it against `^([A-Za-z0-9]{20,})?$` apart from the other passwords; `deploy/ansible/inputs/a.yml` gives a synthetic one, `b.yml` and `e2e.yml` an empty one. Verify: in `tests/test_deploy_role_run.py`, a short or malformed value refused by name before any call, an empty one accepted, a missing one refused by name.
- [x] 2.2 `vars/main.yml`: `{key: reader, role: itgraph_reader, password: …}` in `__itgraph_passwords`. `tasks/password.yml`: the empty branch of design.md, Decision 5, reading `rolcanlogin` and the mark, `ALTER ROLE itgraph_reader NOLOGIN PASSWORD NULL` on stdin, the mark removed. Verify, with the fake kubectl: empty with `rolcanlogin` true runs the `NOLOGIN` statement on stdin and removes the mark; empty with a stale mark does the same; empty with neither runs no psql that changes anything and touches no annotation; the set branch for `reader` behaves as the bot's tests show; no argument or output holds the password. Both branches end the reader's sessions per design.md, Decision 5: when an `ALTER ROLE` runs for an entry with `end_sessions`, `pg_terminate_backend` over the role's sessions follows it on the same stdin; it is absent when the `ALTER` is skipped, and absent for the bot's entry.
- [x] 2.3 Render tests in `tests/test_deploy_role.py`: the reader's password from `a.yml` is in no rendered stream, Secrets included, and is added to the lists `test_no_value_of_one_set_is_in_the_others_objects` and `test_no_secret_is_outside_the_secret_streams` keep, where it fits their meaning. Verify: the tests green, and failing when the value is put in a Secret's data by hand.
- [x] 2.4 `deploy/runbook.md`, «База через туннель»: connect as `itgraph_reader` by default and as `itgraph` only to change something; what each guard is for and that a session can lift it; the rules from 2026-11-06 for heavy queries; an agent prints only aggregates of `yt_raw_*`; no advisory lock is taken by hand; a deploy that changes or takes away the reader's password ends its open sessions. Root `CLAUDE.md`, "Deploying": one line that the reader is an operator's door and no workload's. Verify: the runbook's commands match the role's names, and a search for the infrastructure repository's name in the diff finds nothing.

## 3. End to end

- [x] 3.1 `deploy/ansible/e2e.sh`: `tunnel` takes the user and the SQL. After the fresh install, with `e2e.yml`'s empty input, the reader is refused for not being allowed to log in. Verify: `make test-e2e` shows it.
- [x] 3.2 A run with `-e itgraph_reader_postgres_password=fakeReaderPasswordSetEeee`: through `tunnel`, the reader logs in; `SHOW` gives read-only on and the three settings; it selects from `channels`; an `INSERT` is refused as a write in a read-only transaction; after `SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE` the same `INSERT` is refused with permission denied; the run reports `restarted: none`. Verify: `make test-e2e`, each as an `ok` line.
- [x] 3.3 A run with another fake password: the old one is refused for its password, and the new one is accepted. Then a run with the empty input: every password is refused for login; `restarted: none` both times. Across each of these runs, and across a run with an unchanged password, a `hostNetwork` pod holds a reader session (`SELECT pg_sleep(300)`): it is ended by the server on a change and a take-away, and still running after the unchanged run. Verify: `make test-e2e`, each as an `ok` line.
- [x] 3.4 Restore: after the first restore (a dump older than the reader) and after the second (a dump of the neutralised copy at head), over the local socket, the reader has `SELECT` and no write privilege on every table, and `pg_default_acl` grants it `SELECT`; `pg_restore --list` of the neutralised dump holds the reader's `ACL` entries and a `DEFAULT ACL`, so the second check is not vacuous. Verify: `make test-e2e`, and the restore's own lines unchanged in its output.

## 4. Integration

- [x] 4.1 `make validate` and `make test-e2e` green, and `openspec validate database-reader-role --strict`. Verify: their output.

## Workflow follow-up

- **(operator)** A release tag; the infrastructure repository gives the input from its seed and moves the pin in the test environment first, then production, per design.md, Migration Plan.
- **(operator)** In test, a tunnel as the reader, and a restore of a production dump once production has the revision.
- After production: the memory note on read-only SQL moves from `kubectl exec` as the owner to the tunnel as the reader.
- Archive the change, syncing `operator-database-access`, `collection-deploy` and `database-backups` into `openspec/specs/`.
