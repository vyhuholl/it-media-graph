## Purpose

Deploying to the collection host, whether to a fresh machine or again to one that is already collecting: what a deploy may do to the data already there, the order in which the schema and the services change, and what the operator is told afterwards.

## ADDED Requirements

### Requirement: Re-run Keeps A Coherent Inventory

A deploy SHALL NOT restore over a database that holds channels and whose schema is coherent, unless the operator explicitly asks for a restore. It SHALL skip the restore, say so with the number of channels kept, and continue with the rest of the deploy.

#### Scenario: A full re-run on a live host

- **GIVEN** a host whose database holds channels and has no column referencing a missing type
- **WHEN** the operator runs the full deploy without asking for a restore
- **THEN** nothing is restored
- **AND** the output says the restore was skipped and how many channels were kept
- **AND** the migration, the services and the checks still run

#### Scenario: A restore the operator asked for

- **GIVEN** the same host
- **WHEN** the operator runs the deploy asking for a restore
- **THEN** the dump from the operator's machine is restored, as before this change
- **AND** the restored database is checked for coherence before the deploy continues

#### Scenario: A fresh host

- **GIVEN** a host whose database has no tables
- **WHEN** the full deploy runs
- **THEN** the dump is restored without being asked for

### Requirement: An Incoherent Database Stops The Deploy

A deploy SHALL stop when the database holds channels but some column references a type that no longer exists, unless the operator explicitly asks for a restore. It SHALL stop before any migration and before any unit is touched. The message SHALL say that the restore source is the operator's dump and is older than the host's own data.

#### Scenario: A broken live database

- **GIVEN** a host whose database holds channels and has columns referencing a missing type
- **WHEN** the full deploy runs without a restore being asked for
- **THEN** the deploy fails with the number of affected columns
- **AND** nothing is restored and nothing is migrated
- **AND** no unit is installed, started or restarted
- **AND** the message points at restoring from the host's own backups, and at the restore flag only for a host that never collected

#### Scenario: A fresh host after an interrupted restore

- **GIVEN** a host where a previous restore stopped part way
- **WHEN** the operator re-runs the deploy asking for a restore
- **THEN** the dump is restored and checked for coherence

### Requirement: Unknown Database State Stops The Deploy

A deploy SHALL NOT treat a database whose state it could not read as empty. Only a missing channel table SHALL count as an empty database; any other failure to count channels or to check coherence SHALL stop the deploy before a restore, a migration, or any change to a unit.

#### Scenario: The channel count fails

- **GIVEN** a host where counting channels fails for any reason other than the table not existing
- **WHEN** the deploy runs
- **THEN** the deploy fails with the error it received
- **AND** nothing is restored

#### Scenario: The channel table does not exist

- **GIVEN** a host whose database has no channel table
- **WHEN** the deploy runs
- **THEN** the database is treated as empty

#### Scenario: The coherence check fails

- **GIVEN** a host where the coherence check itself fails
- **WHEN** the deploy runs
- **THEN** the deploy fails with the error it received
- **AND** the database is not treated as coherent

### Requirement: Schema At Head Before Services

A deploy SHALL bring the schema to the newest revision before it installs, starts or restarts any unit, and SHALL stop if the schema is not at head afterwards. A deploy restricted to the services SHALL check the schema too, and refuse to touch any unit while it is behind.

#### Scenario: A full deploy with a pending migration

- **GIVEN** a host whose schema is one revision behind the deployed code
- **WHEN** the full deploy runs
- **THEN** the schema is migrated to head before any unit file is written or any unit is started

#### Scenario: The migration fails

- **WHEN** the migration fails during a deploy
- **THEN** the deploy stops
- **AND** no unit is installed, started or restarted

#### Scenario: Services alone on a schema that is behind

- **GIVEN** a host where new code was fetched and the schema is behind it
- **WHEN** the operator runs only the services part of the deploy
- **THEN** the deploy fails before any unit is touched, saying the schema is not at head

### Requirement: A Migration Dump Only When Migrating

A deploy SHALL run a migration only when the database is behind head. A deploy against a database already at head SHALL run no migration, and so take no pre-migration dump and prune no older one. When a migration runs, the deploy SHALL report the revisions it moved between and where the pre-migration dump was written.

#### Scenario: Already at head

- **GIVEN** a host whose schema is at head
- **WHEN** the full deploy runs
- **THEN** no migration runs
- **AND** no new file appears among the host's full dumps, and none is pruned

#### Scenario: Behind head

- **GIVEN** a host whose schema is behind head
- **WHEN** the full deploy runs
- **THEN** exactly one full dump is taken, before the migration
- **AND** the output names the revision before and after, and the path of that dump

#### Scenario: Repeated re-runs

- **GIVEN** a host whose schema is at head
- **AND** no scheduled full dump falls due while the deploys run
- **WHEN** the full deploy is run four times in a row
- **THEN** the host's full dumps are the same files after the fourth run as before the first

### Requirement: Migration Runnable On Its Own

The deploy SHALL offer a part that migrates without restoring and without touching any unit, and the data part of the deploy SHALL continue to include the migration. The migration part SHALL itself start the database, check the database's state as the full deploy does, and stop on the same conditions.

#### Scenario: Migration alone

- **WHEN** the operator runs only the migration part on a host whose schema is behind
- **THEN** the database is started if it was stopped
- **AND** the database's state is checked, and the schema is migrated to head
- **AND** the output names the revision before and after, and the path of the pre-migration dump
- **AND** nothing is restored, and no unit is installed, started or restarted

#### Scenario: Migration alone on a broken database

- **GIVEN** a host whose database holds channels and has columns referencing a missing type
- **WHEN** the operator runs only the migration part
- **THEN** the deploy fails before migrating

#### Scenario: The data part still migrates

- **GIVEN** a fresh host
- **WHEN** the operator runs only the data part
- **THEN** the dump is restored and the schema is then migrated to head

### Requirement: Re-run Keeps The Host's Session

A deploy SHALL NOT replace a Telegram session that already exists on the host, and SHALL NOT need a copy of it on the operator's machine when the host has one. It SHALL copy the operator's session only to a host that has none. It SHALL stop when the host's session is empty or not a regular file, and when neither the host nor the operator's machine has a session.

#### Scenario: A live host, and no session on the operator's machine

- **GIVEN** a host holding a non-empty session file
- **AND** no session file at the configured path on the operator's machine
- **WHEN** the full deploy runs
- **THEN** nothing is copied, and the deploy continues to the services

#### Scenario: Both have one

- **GIVEN** a host holding a non-empty session file, and a session file on the operator's machine
- **WHEN** the full deploy runs
- **THEN** the host's session is left unchanged

#### Scenario: A fresh host

- **GIVEN** a host with no session file, and a session file on the operator's machine
- **WHEN** the full deploy runs
- **THEN** the operator's session is copied to the host, readable by its owner only

#### Scenario: No session anywhere

- **GIVEN** a host with no session file, and none at the configured path on the operator's machine
- **WHEN** the full deploy runs
- **THEN** the deploy fails before any unit is touched
- **AND** the message names the path it looked at, and says to copy the session from the machine that has been collecting, not to create a new one

#### Scenario: A broken session on the host

- **GIVEN** a host whose session path is an empty file, or not a regular file
- **WHEN** the full deploy runs
- **THEN** the deploy fails before any unit is touched, and the host's file is not replaced

### Requirement: Services Run The Deployed Code

A deploy SHALL restart the long-running services after the schema is at head whenever it fetched the code, whether or not the fetch brought anything new, and also when it changed the configuration or that service's unit file. A deploy that did none of these SHALL NOT restart them.

#### Scenario: New code is deployed

- **GIVEN** running collection and bot services
- **WHEN** a full deploy fetches a newer revision of the code
- **THEN** both services are restarted after the schema is at head

#### Scenario: A re-run after a deploy that failed midway

- **GIVEN** an earlier deploy that fetched newer code and failed before the services
- **WHEN** the full deploy runs again and the fetch brings nothing new
- **THEN** both services are restarted after the schema is at head

#### Scenario: Configuration and services

- **WHEN** the operator runs only the configuration and services parts, and the configuration changed
- **THEN** both services are restarted

#### Scenario: Services alone

- **WHEN** the operator runs only the services part of the deploy
- **THEN** neither service is restarted

#### Scenario: Fetching code alone

- **WHEN** the operator runs only the part that fetches code
- **THEN** no service is restarted

### Requirement: The Deploy Reports What It Did

At the end of a deploy, the report SHALL state whether a restore was done or skipped, whether a migration ran and between which revisions, where the pre-migration dump was written, and which services were restarted. A part of the deploy that did not run in this invocation SHALL be reported as not run, not as done.

#### Scenario: A re-run with nothing to migrate

- **WHEN** a full deploy on a live host at head finishes
- **THEN** the report says the restore was skipped with the channel count, no migration ran, and which services were restarted, if any

#### Scenario: Checks alone

- **WHEN** the operator runs only the checks
- **THEN** the report marks the restore, the migration and the restarts as not run in this invocation
