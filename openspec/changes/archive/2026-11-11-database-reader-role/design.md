# Design

## Context

See `proposal.md`, Why. The requirements are in `specs/`. What shapes the approach:

- **Two roles already follow this pattern.** `c5a30b71e8d4_bot_database_role` and `b7d2e4f19a30_metrics_database_role` create a role `NOLOGIN` in a `DO` block, grant `CONNECT` through `format('%I', current_database())`, and leave the password to the deploy. The metrics revision's downgrade keeps a role another database still uses; the bot's does not.
- **Passwords.** `__itgraph_passwords` in `vars/main.yml` lists each role with its input. `tasks/password.yml` sets one over the database pod's local socket, the password on psql's stdin, and marks its hash on the `itgraph-postgres` Secret's annotations; a run whose hash matches does nothing. The owner's comes before the migration, every other role's after it, so the role exists by then. Only the owner's mark decides whether writers stop.
- **The owner is a superuser.** The Postgres image creates `POSTGRES_USER` as one, and `itgraph_db_user` is `itgraph` in every environment (`defaults/main.yml`), not an input.
- **The restore** runs `pg_restore --exit-on-error --no-owner`, without `--no-privileges`, as the owner: a dump's grants are replayed, and fail if they name a role the cluster lacks. Then `migrate.yml` brings the schema to head, and `neutralise.sql` and `readback.sql` run. Neither reads privileges.
- **What a dump carries.** pg_dump writes each table's grants, the schema's, and the default privileges, the last as `ALTER DEFAULT PRIVILEGES FOR ROLE itgraph …`. Without `--create` it writes no privilege on the database itself, and a database `createdb` makes gives `CONNECT` and `TEMP` to `PUBLIC`. Role attributes and role settings are the cluster's and never in a dump.
- **Postgres 16, documented behaviour** (checked against the PG16 docs, 2026-11-11):
  - `default_transaction_read_only` and the other session settings can be changed by any session (`SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE`, `BEGIN READ WRITE`, `SET …`). The docs call it "a high-level notion of read-only that does not prevent all writes to disk".
  - A read-only transaction refuses every `CREATE`, including of a temporary table.
  - `ALTER DEFAULT PRIVILEGES` without `FOR ROLE` applies to objects the current role creates, and to no other role's.
  - Settings given by `ALTER ROLE … SET` apply when a session logs in as the role, not on `SET ROLE`.
- **Precedent for the guards.** On 2026-11-06 a read-only query on production, a recursive walk of `raw_messages` payloads, ran the database's 2 GiB cgroup out of memory. The kernel killed another backend, Postgres ran crash recovery, and the collector lost its session lease and restarted.
- **The suite.** `tests/test_metrics_role.py` builds a database of its own with Alembic and tests grants through `SET LOCAL ROLE`, because logging in needs a password, and that would change the cluster outside any `_test` database. It serialises building that database on a cluster-wide advisory lock, `MIGRATION_LOCK`, because two workers creating one role at once collide.
- **The end-to-end suite** already reaches the fixed address from a `hostNetwork` pod (`tunnel` in `e2e.sh`), as a tunnel's far end does, and restores twice: a dump made at the revision before `fb31bd0b6656`, then a dump of the neutralised copy at head.

## Goals / Non-Goals

**Goals:**

- The role and its guards in one revision, idempotent like the two before it.
- The login follows the input exactly, in both directions, with no password in any rendered object, argument or log.
- Every behaviour in `specs/` checked: grants and catalog state in pytest, login and session state in the end-to-end suite.

**Non-Goals:**

- Ending sessions of any role but the reader when its password changes (Decision 5).
- `REVOKE TEMP ON DATABASE … FROM PUBLIC`, or revoking advisory-lock functions from `PUBLIC`. Both would change what every role can do, not only the reader.
- A `lock_timeout` on the deploy's migrations (`proposal.md`, Out of scope).

## Decisions

### 1. Everything in `public`, by default privileges

`GRANT SELECT ON ALL TABLES IN SCHEMA public` (views included), then `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO itgraph_reader`, so a table a later migration creates is readable without a migration of its own. This is the opposite of the bot's revision, which enumerates on purpose so that a new table has to be named to be read. That reasoning is about a workload whose credential could leak. The reader's rights are a subset of the owner's, and the runbook today sends operators through the tunnel as the owner, so the reader narrows what a tunnel session can do; it widens nothing.

*Alternatives.* Enumerating the tables: a migration per new table, and the reader falls behind the schema the first time one is forgotten. Excluding `yt_raw_*` by column grants: the operator reads API data as the owner today, and the rule that matters is what leaves the database, which a grant cannot see. That rule, aggregates only for an agent, goes in the runbook.

### 2. Default privileges with no `FOR ROLE`

They are written for the current role, which is whoever runs the migrations: `itgraph` in both environments and in the suite. That names no owner in a revision, and is correct in a scratch database whose owner differs. pg_dump writes `FOR ROLE itgraph` explicitly all the same, and every restore runs as `itgraph`.

*Alternative.* `FOR ROLE itgraph`, as first asked. It is the same in production, and wrong anywhere migrations run as another role. No migration does today, but a revision should not depend on that.

### 3. Guards as role settings, not as a boundary

```sql
ALTER ROLE itgraph_reader CONNECTION LIMIT 4;
ALTER ROLE itgraph_reader SET default_transaction_read_only = on;
ALTER ROLE itgraph_reader SET work_mem = '16MB';
ALTER ROLE itgraph_reader SET statement_timeout = '10min';
ALTER ROLE itgraph_reader SET idle_in_transaction_session_timeout = '5min';
```

Each covers an accident that has happened or plausibly will:

- **Read-only** catches a stray write, and refuses the one write that grants leave open, `CREATE TEMP TABLE`, because `PUBLIC` holds `TEMP`.
- **`work_mem`** bounds a sort or hash per node: the 2026-11-06 crash came from memory, not time.
- **`statement_timeout`** ends a query left running from a laptop.
- **`idle_in_transaction_session_timeout`** ends a transaction left open, for example by DBeaver in manual-commit mode, before its `AccessShareLock` makes a deploy's `alembic upgrade` wait, with every other query queued behind that.
- **The connection limit** keeps a tool that opens a pool from taking the database's connections.

A session can change every one of the settings; the connection limit it cannot. So none is a boundary, and the specs say so. The boundary is Decision 1's grants, which is why the test after lifting read-only expects permission denied.

The settings are the cluster's, so a test environment gets them from its own migration, not from a dump. A re-run sets the same values.

### 4. The revision

`upgrade`:

1. The `DO` block creates the role `NOLOGIN` if it is missing.
2. `CONNECT` on the current database, as the bot's revision grants it.
3. `USAGE` on `public`, `SELECT` on all tables, then the default privileges.
4. The guards of Decision 3.

There is no grant on a sequence or a function. `down_revision` is the head at implementation time.

`downgrade`, in reverse:

1. Revoke the default privileges. Forgetting this is the classic reason `DROP ROLE` fails.
2. Revoke `SELECT` on all tables, `USAGE`, and `CONNECT`.
3. Drop the role in a block that keeps it on `dependent_objects_still_exist`, as the metrics revision does.

The role is the cluster's. A scratch database is verified beside the working one, and every worker's database migrated to head names the reader too. The settings go with the role, or stay with it when it is kept.

### 5. The input and `password.yml`

`itgraph_reader_postgres_password`: `type: str`, `required: true`, `no_log: true`, and empty allowed. That is the pattern `itgraph_youtube_api_key` set: the repository that runs the role says "none" explicitly, and the role never assumes it. `inputs.yml` checks it apart from the other passwords, against `^([A-Za-z0-9]{20,})?$`.

`__itgraph_passwords` gains `{key: reader, role: itgraph_reader, password: …}`, so it runs in the loop after the migration. `password.yml` branches on whether the password is empty:

- **Set:** the existing path. `ALTER ROLE … LOGIN PASSWORD` on stdin, then the `itgraph/password-reader` mark, skipped when the mark already matches.
- **Empty:** read `rolcanlogin` from `pg_roles`, and the mark. If either says the role can log in, run `ALTER ROLE itgraph_reader NOLOGIN PASSWORD NULL` on stdin and remove the mark. Otherwise do nothing, so a second run changes nothing. Reading `rolcanlogin`, rather than trusting the mark alone, also undoes a login someone gave the role by hand (requirement 1: what is not in Ansible is removed).

**Ending the reader's sessions.** A changed or removed login does not end the sessions that logged in with the old one, so revoking would wait for them to disconnect. Whenever either branch actually runs its `ALTER ROLE`, the same stdin then runs `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'itgraph_reader'`, in that order, so that a session reconnecting at once meets the new state. The owner is a superuser, so it may end them. A run that skips the `ALTER` skips this too, and an unchanged run ends nothing. This is a flag on the reader's entry in `__itgraph_passwords` (`end_sessions: true`) and on no other entry:
- The bot's and the exporter's sessions belong to pods the same run restarts, through their templates' checksums.
- The owner's sessions are the run's own psql and the writers it has stopped.

The password is in no Secret's data, no pod and no URL; only its hash is in the annotation, as the other roles' are. `__itgraph_must_stop` keeps reading the owner's mark alone, so the reader's password stops and restarts nothing.

*Alternative.* `required: false` with an empty default. An inventory that forgot the input would then deploy silently with no reader, and the role's rule is that where it runs is always said.

### 6. The restore stays as it is

A production dump that names the reader is restored only into a test environment already at a release that has the revision. Pins are moved in test first, and a deploy at that release ran the migration and created the role in the cluster. Its grants and default privileges then replay under `--exit-on-error`. `CONNECT` comes from `PUBLIC` on the new database, and the guards from the cluster. Restoring a production dump into a test environment pinned behind production already fails today, since that image does not know the dump's revision, and the restore's rescue empties the database. A dump older than the revision gains the reader's grants when `migrate.yml` brings it to head.

So the restore changes nothing, and the end-to-end suite makes this explicit (Decision 7) rather than leaving it to the absence of a failure.

*Alternative.* `--no-privileges` on the restore, with the grants re-run after. It would drop every grant in the dump, the bot's and the exporter's included, and the restore would no longer test what production holds.

### 7. Where each behaviour is tested

| Behaviour | Where | How |
|---|---|---|
| `SELECT` on every table and view at head | pytest, own database | `SET LOCAL ROLE`, tables enumerated from `information_schema` at run time |
| A table created after the migration | pytest, own database | created as the owner with no grant, then read under `SET LOCAL ROLE` |
| Every write refused for privilege | pytest | `SET LOCAL ROLE`. The role's settings do not apply there, so the error is 42501 directly |
| `nextval`, `CREATE TABLE` refused | pytest | same |
| Guards and `NOLOGIN` as created | pytest | `pg_roles.rolconfig`, `rolconnlimit`, `rolcanlogin` |
| Downgrade leaves nothing, keeps a used role | pytest, two own databases | after downgrading one: no privilege or default ACL in it, the role exists and reads the other |
| `password.yml`, both branches | pytest, fake kubectl | as the existing password tests; the empty branch with `rolcanlogin` true, false, and a stale mark |
| The password in no rendered object | pytest, render | a set of inputs with a reader password; the value in no stream, the Secrets included |
| Login, read-only start, lifting then 42501, the settings | e2e, through `tunnel` | after a run with a password |
| No password: refused for login | e2e | the fresh install, with an empty input |
| A new password, then none | e2e | the old one refused, then every one refused; `restarted: none` each time |
| Open sessions ended on a change, kept on none | e2e, and pytest with fake kubectl | a `hostNetwork` pod holding a reader session across each run; the statement on stdin after the `ALTER`, for the reader's entry alone |
| Restore of a dump naming the reader | e2e | `pg_restore --list` of the neutralised dump shows the reader's ACL and `DEFAULT ACL`; after each restore, the reader's privileges checked over the local socket |

`MIGRATION_LOCK` moves to `tests/conftest.py`, and both role modules take it, around every upgrade and downgrade. `ALTER ROLE … SET` from two workers at once fails with `tuple concurrently updated`.

## Risks / Trade-offs

- [A session lifts its guards: read-only, `work_mem`, the timeouts] → The grants hold regardless. The guards are for accidents, and the runbook says what each is for, so lifting one is a decision and not a slip.
- [A heavy query within the guards still runs the database out of memory, from several nodes or parallel workers] → `work_mem` narrows it and does not close it. The runbook keeps the rules from 2026-11-06: sampling for shape, streaming counts, nothing heavy while the collector is busy.
- [A reader takes an advisory lock the collector or a YouTube job uses] → Execute on those functions belongs to `PUBLIC`, and revoking it is out of scope. It takes a deliberate call with the right key, not an accident, and the runbook says so.
- [A transaction open for less than 5 minutes, or a long statement, delays a deploy's migration] → Both are bounded: 5 minutes idle, 10 running. A deploy stops writers before migrating, and the operator running it is the same person who would hold the session.
- [Rotating the reader's password cuts an operator's open session, a query in progress included] → Intended: a rotation is a revocation. The runbook says that a deploy which changes the reader's password ends its sessions.
- [An agent's transcript keeps API data past 30 days] → The runbook rule: an agent prints only aggregates of `yt_raw_*`, never a title, description, handle or link.
- [The public repository names the infrastructure repository or its host] → Neither appears in code, docs, artifacts or commits. The infrastructure repository is called just that.

## Migration Plan

1. Release: the revision, the role's input and the tests, with `make validate` and `make test-e2e` green.
2. The infrastructure repository: the input, derived from its seed, in the test environment's inventory, and the pin moved there. Its operator runs the role in test.
3. In test: tunnel as the reader, read, see a write refused. Restore a production dump, made after step 5 of an earlier cycle or the next weekly one, to see grants that name the reader replayed. Until production has the revision, the existing dump checks the older-dump path.
4. Production: the input and the pin, as a commit of its own; the role run. The migration's pre-upgrade dump is taken as usual.
5. The runbook's tunnel section is already part of the release.

**Rollback.** Reverting the pin alone stops at "the schema is ahead of the image" (`database_state.yml`), as after any revision, so the way back is a release that fixes forward. The revision drops no data. If it ever has to go, its downgrade on production needs the guard's override after a dump, by the documented path.
