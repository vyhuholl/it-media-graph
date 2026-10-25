## Why

On 2026-10-25 a full `ansible-playbook deploy/playbook.yml` against the live collection host stopped at `Refuse to restore over existing data`: 3,818 channels, a coherent schema, and an assert that passes only when a restore is needed. By then `repo` and `config` had already run, so the host held new code on an old schema. The migration carries the same `data` tag as that guard, so no tag migrates without reaching it, and the deploy of `add-youtube-inventory` was finished by hand: `--tags repo`, `alembic upgrade head` over ssh, `--tags services`, `--tags verify`.

The playbook was written for a fresh host, and looking at it as something re-run on a live one turned up more than the one assert:

- **A failed channel count reads as an empty database.** The probe ignores its own failure and an empty answer parses as 0, which is the answer that triggers a restore.
- **An incoherent database is restored over without a flag** — with the dump from the operator's machine, which is older than what the host has collected since. Metric snapshots lost that way cannot be taken again.
- **Every `alembic upgrade head` takes a full dump and prunes the weekly tier**, whether or not there is anything to migrate, and that tier keeps four. Four re-runs in an evening leave no dump older than the evening.
- **`Schema is at head` asserts nothing.** It prints `alembic current` and carries on.
- **No deploy restarts `itgraph-watch` or `itgraph-bot`.** `state: started` leaves a running unit alone, so after a deploy both keep running the code from before it.

## What Changes

- **A re-run on a coherent, non-empty database skips the restore**, says so with the channel count, and continues to the migration, the services and the checks. `-e force_restore=true` restores exactly as before.
- **BREAKING (operator procedure):** a non-empty database whose columns reference types that no longer exist now **stops** the deploy unless `force_restore` is given. It used to be restored over automatically. A fresh host after an interrupted restore needs the flag; a live host is pointed at its own backups instead.
- **A probe that fails stops the deploy.** Only a missing `channels` table counts as an empty database; any other failure of either probe is reported as unknown state, not read as zero.
- **The migration gets its own tag, `migrate`**, together with what it needs: starting Postgres, both probes, and the decision. All of them keep `data`, so `--tags data` still restores and migrates.
- **The migration runs only when the database is behind head.** One pre-migration dump per migration, not one per deploy.
- **The schema is asserted to be at head before any unit is touched**, and that check also runs under `--tags services`, so starting services on an old schema is refused rather than ordered around.
- **`itgraph-watch` and `itgraph-bot` are restarted after the schema is at head** on every run that fetched the code, and otherwise when `.env` or their unit file changed. Not only on a run that changed the code: a deploy that failed between the checkout and the services would otherwise leave them on the old code, with nothing left to notice.
- **The host's Telegram session is kept, and not required on the operator's machine.** Found on the first live re-run: the session step demanded a copy on the controller even though the host had its own. An empty or non-file session on the host, or none anywhere, stops the deploy.
- **The final report says what the deploy did:** restore skipped or done, migration from and to which revision or none, where the pre-migration dump went, and which services were restarted.
- `deploy/README.md` and the playbook's comments describe the new behaviour; the "deliberately not done" section says "skips" where it said "stops".

Out of scope:

- **Restoring from the host's own backups.** The restore source stays `itgraph_dump_src` on the operator's machine. This change only stops that source being used over live data without a flag.
- **`--check` as a preview against the host.** It fails early today (`command` tasks are skipped in check mode and later conditions read their missing results), and making the whole playbook check-mode safe is its own change.
- **Closing the window in which already-running timers fire new code on the old schema** between the checkout and the migration. It existed in the manual path too; stopping the timers would leave the backup timer stopped whenever a deploy fails midway.
- **A test harness for the playbook** (molecule, a scratch VM). Verification is static locally and real on the operator's run.

## Capabilities

### New Capabilities

- `collection-deploy`: what deploying to the collection host guarantees about the data already there, the schema, and the services left running. It covers re-run safety, stopping on a database in an unknown or broken state, the schema at head before units, a migration dump only when migrating, services running the deployed code, and a report of what was done.

### Modified Capabilities

None. `youtube-data-retention` ("Unattended Refresh") and `youtube-inventory` ("Remote Operation") touch the deploy, but neither requirement changes.

## Impact

- `deploy/playbook.yml` — the `data` section's probes, decision and restore; the migration and its check; the `session` section; a restart step in `services`; the report in `verify`; `changed_when` of `Install dependencies`, which today is true on every run.
- `deploy/README.md` — "Чего плейбук не делает намеренно", "Отдельные части", and a note on the timer window.
- No change in `src/`, no migration, no new dependency, no template change.
- The operator: on a fresh host after an interrupted restore, the re-run now needs `-e force_restore=true`.
