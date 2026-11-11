# Proposal

## Why

Operators reach the database through an SSH tunnel to the fixed address (requirement 5 of `docs/INFRASTRUCTURE.md`), and today the only role there that can read everything is the owner, `itgraph`: the runbook's tunnel section connects with the owner's password, so a look at production carries the power to change it. `itgraph_bot` and `itgraph_metrics` read only what their workloads need. The infrastructure repository that owns the host asks for a role that reads everything and changes nothing, so that the operators, and an agent exploring with them, look at production without holding the owner's password.

## What Changes

- **A migration creates `itgraph_reader`,** `NOLOGIN`, on the pattern of the bot's and the exporter's roles: `CONNECT` on the database, `USAGE` on `public`, `SELECT` on every table and view there, and default privileges so that a table a later migration creates is readable with no migration of its own. No `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, and nothing on a sequence. The downgrade revokes all of it, the default privileges included, and drops the role unless the cluster still uses it elsewhere.
- **The role's sessions start guarded:** read-only by default, a connection limit, and role-level `work_mem`, `statement_timeout` and `idle_in_transaction_session_timeout`. None of these is a boundary, since a session can lift each one; the grants are the boundary. They are there against accidents, which on this database have already cost a crash recovery (a read-only query's memory, 2026-11-06).
- **A new input, `itgraph_reader_postgres_password`,** required and allowed to be empty. Set, the deploy makes the role `LOGIN` with that password; empty, the role is `NOLOGIN` with no password, including after one was set. A change or a take-away ends the reader's open sessions, so that a revoked login is revoked at once. The value reaches no workload and no Secret's data. A change to it restarts nothing.
- **Restore keeps working on dumps that carry the reader's grants.** No change to the restore is expected: the role is the cluster's, created in the test environment by the same migration before any dump of production could name it. The end-to-end suite makes that explicit.
- **The runbook's tunnel section connects as `itgraph_reader`** by default, and as `itgraph` only to change something. It states what the guards are for, and that what an agent reads of `yt_raw_*` stays aggregates.

**Out of scope:**

- **Giving the role to a workload.** It is an operator's door. No pod's configuration names it.
- **Network policies.** The tunnel ends on the node, which the policies do not stop; the end-to-end suite already proves the node reaches the fixed address.
- **Moving the infrastructure repository's pin.** Its operator does that after the release, naming the tag, and derives the password from its own seed in both environments.
- **A lock timeout for the deploy's migrations,** which would bound how long a reader's open transaction can hold an upgrade. The idle-in-transaction timeout bounds the common case; the rest is its own change if it is ever needed.

## Capabilities

### New Capabilities

- `operator-database-access`: what an operator's tunnel may do in the database under the reader role: what it reads, that it writes nothing, the guards its sessions start with, and how its login follows the deploy's input.

### Modified Capabilities

- `collection-deploy`: "Operators Reach The Database At A Fixed Address" gains the reader's login and its absence; "Each Workload Holds Only Its Own Secrets" gains that the reader's password reaches no pod and restarts nothing.
- `database-backups`: "A Full Dump Restores Into A Test Environment With One Entry Point" gains a dump that carries the reader's grants, and the reader reading the restored copy.

## Impact

- `src/itgraph/db/migrations/versions/`: one new revision after head.
- `deploy/ansible/itgraph/`: `meta/argument_specs.yml`, `tasks/inputs.yml`, `vars/main.yml`, `tasks/password.yml`; `deploy/ansible/inputs/*.yml` gain a synthetic value.
- Tests: a new module for the role's grants on a database of its own; the cluster-wide migration lock shared through `tests/conftest.py`; the deploy role's static tests; `deploy/ansible/e2e.sh` for login, the read-only start, the absent password and the restore.
- Docs: `deploy/runbook.md`, `src/itgraph/README.md` where it lists the database roles, the root `CLAUDE.md` if it names the roles.
- The infrastructure repository: a new input in both environments, given after the release.
