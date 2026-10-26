# Spec Delta

## MODIFIED Requirements

### Requirement: An Incoherent Database Stops The Deploy

A deploy SHALL stop when the database holds channels but some column references a type that no longer exists, unless the operator explicitly asks for a restore. It SHALL stop before any migration and before the collector, the bot or any scheduled job is created, changed or restarted. The message SHALL say that the restore source is the operator's dump and is older than the host's own data.

#### Scenario: A broken live database

- **GIVEN** a host whose database holds channels and has columns referencing a missing type
- **WHEN** the full deploy runs without a restore being asked for
- **THEN** the deploy fails with the number of affected columns
- **AND** nothing is restored and nothing is migrated
- **AND** the collector, the bot and the scheduled jobs are not created, changed or restarted
- **AND** the message points at restoring from the host's own backups, and at the restore flag only for a host that never collected

#### Scenario: A fresh host after an interrupted restore

- **GIVEN** a host where a previous restore stopped part way
- **WHEN** the operator re-runs the deploy asking for a restore
- **THEN** the dump is restored and checked for coherence

### Requirement: Unknown Database State Stops The Deploy

A deploy SHALL NOT treat a database whose state it could not read as empty. Only a missing channel table SHALL count as an empty database; any other failure to count channels or to check coherence SHALL stop the deploy before a restore, a migration, or any change to the collector, the bot or a scheduled job.

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

A deploy SHALL bring the schema to the newest revision of the deployed image before it creates, changes or restarts the collector, the bot or any scheduled job, and SHALL stop if the schema is not at head afterwards. A deploy restricted to the services SHALL check the schema too, and refuse to touch any of them while it is behind.

#### Scenario: A full deploy with a pending migration

- **GIVEN** a host whose schema is one revision behind the deployed image
- **WHEN** the full deploy runs
- **THEN** the schema is migrated to head before the collector, the bot or any scheduled job is created or given the new image

#### Scenario: Scheduled jobs wait for the schema

- **GIVEN** a deploy whose image carries a migration
- **WHEN** a scheduled job fires after the deploy started and before the migration finished
- **THEN** it runs the image it had before the deploy

#### Scenario: The migration fails

- **WHEN** the migration fails during a deploy
- **THEN** the deploy stops
- **AND** the collector, the bot and the scheduled jobs are not created, changed or restarted

#### Scenario: Services alone on a schema that is behind

- **GIVEN** a host whose schema is behind the image being deployed
- **WHEN** the operator runs only the services part of the deploy
- **THEN** the deploy fails before the collector, the bot or any scheduled job is touched, saying the schema is not at head

### Requirement: Migration Runnable On Its Own

The deploy SHALL offer a part that migrates without restoring and without touching the collector, the bot or any scheduled job, and the data part of the deploy SHALL continue to include the migration. The migration part SHALL itself start the database, check the database's state as the full deploy does, and stop on the same conditions.

#### Scenario: Migration alone

- **WHEN** the operator runs only the migration part on a host whose schema is behind
- **THEN** the database is started if it was stopped
- **AND** the database's state is checked, and the schema is migrated to head
- **AND** the output names the revision before and after, and the path of the pre-migration dump
- **AND** nothing is restored, and the collector, the bot and the scheduled jobs are not created, changed or restarted

#### Scenario: Migration alone on a broken database

- **GIVEN** a host whose database holds channels and has columns referencing a missing type
- **WHEN** the operator runs only the migration part
- **THEN** the deploy fails before migrating

#### Scenario: The data part still migrates

- **GIVEN** a fresh host
- **WHEN** the operator runs only the data part
- **THEN** the dump is restored and the schema is then migrated to head

### Requirement: Re-run Keeps The Host's Session

A deploy SHALL NOT replace a Telegram session that already exists in the host's session volume, and SHALL NOT need a copy of it on the operator's machine when the volume has one. It SHALL copy the operator's session only into a volume that has none. It SHALL stop when the session in the volume is empty or not a regular file, and when neither the volume nor the operator's machine has a session.

#### Scenario: A live host, and no session on the operator's machine

- **GIVEN** a session volume holding a non-empty session file
- **AND** no session file at the configured path on the operator's machine
- **WHEN** the full deploy runs
- **THEN** nothing is copied, and the deploy continues to the services

#### Scenario: Both have one

- **GIVEN** a session volume holding a non-empty session file, and a session file on the operator's machine
- **WHEN** the full deploy runs
- **THEN** the session in the volume is left unchanged

#### Scenario: A fresh host

- **GIVEN** a session volume with no session file, and a session file on the operator's machine
- **WHEN** the full deploy runs
- **THEN** the operator's session is copied into the volume, readable by the collection workloads' user only

#### Scenario: No session anywhere

- **GIVEN** a session volume with no session file, and none at the configured path on the operator's machine
- **WHEN** the full deploy runs
- **THEN** the deploy fails before the collector is started
- **AND** the message names the path it looked at, and says to copy the session from the machine that has been collecting, not to create a new one

#### Scenario: A broken session on the host

- **GIVEN** a session volume whose session path is an empty file, or not a regular file
- **WHEN** the full deploy runs
- **THEN** the deploy fails before the collector is started, and the file in the volume is not replaced

### Requirement: Services Run The Deployed Code

A deploy SHALL leave the collector and the bot running the deployed image and configuration once the schema is at head. It SHALL restart either only when its image or its configuration differs from what it is running; a deploy that changed neither SHALL NOT restart them. A restart SHALL stop the old instance before the new one starts.

#### Scenario: New code is deployed

- **GIVEN** a running collector and bot
- **WHEN** a full deploy deploys an image they are not running
- **THEN** both are restarted after the schema is at head

#### Scenario: A re-run after a deploy that failed midway

- **GIVEN** an earlier deploy that migrated to a new image and failed before the services
- **WHEN** the full deploy runs again with the same image
- **THEN** both are restarted after the schema is at head, because they still run the old image

#### Scenario: Configuration and services

- **WHEN** the operator runs only the configuration and services parts, and the configuration changed
- **THEN** both are restarted

#### Scenario: Services alone

- **WHEN** the operator runs only the services part, and neither the image nor the configuration changed
- **THEN** neither is restarted

#### Scenario: Fetching code alone

- **WHEN** the operator runs only the part that resolves the image
- **THEN** nothing is restarted

#### Scenario: Never two at once

- **WHEN** the collector or the bot is restarted by a deploy
- **THEN** at no moment do two instances of it run

### Requirement: The Deploy Reports What It Did

At the end of a deploy, the report SHALL state the commit and image digest deployed, whether a restore was done or skipped, whether a migration ran and between which revisions, where the pre-migration dump was written, and which services were restarted. A part of the deploy that did not run in this invocation SHALL be reported as not run, not as done.

#### Scenario: A re-run with nothing to migrate

- **WHEN** a full deploy on a live host at head finishes
- **THEN** the report names the commit and digest, says the restore was skipped with the channel count, no migration ran, and which services were restarted, if any

#### Scenario: Checks alone

- **WHEN** the operator runs only the checks
- **THEN** the report marks the restore, the migration and the restarts as not run in this invocation

## ADDED Requirements

### Requirement: The Deploy Touches Nothing Outside Its Namespace

A deploy SHALL create and change objects only inside the collection namespace. It SHALL NOT install packages on the host, change its firewall, swap, power settings or time zone, or create or change anything in another namespace.

#### Scenario: A full deploy on a fresh host

- **WHEN** the full deploy runs on a host for the first time
- **THEN** the host's packages, firewall rules, swap, power settings and time zone are as they were before
- **AND** every object the deploy created is in the collection namespace

#### Scenario: Removing itgraph

- **WHEN** the collection namespace's workloads are deleted
- **THEN** nothing itgraph created remains running on the host

### Requirement: The Deployed Code Is An Image Pinned By Digest

Every workload that runs itgraph's code SHALL run one image, built by CI from a commit pushed to the repository and referenced by digest. The image SHALL contain nothing the repository does not track. A deploy SHALL NOT proceed without an image for the commit it deploys.

#### Scenario: One image everywhere

- **WHEN** a full deploy finishes
- **THEN** the collector, the bot, the console and every scheduled job reference the same image digest

#### Scenario: No image yet

- **GIVEN** a commit whose image CI has not finished or failed to build
- **WHEN** the deploy is run for that commit
- **THEN** it waits a bounded time and then fails, before anything in the namespace is changed

#### Scenario: A moved tag changes nothing running

- **WHEN** an image tag in the registry is moved to a different image
- **THEN** no running workload or scheduled job changes image

#### Scenario: Nothing ignored is in the image

- **WHEN** the image's files are listed
- **THEN** none of `.env`, a session file, a vault password, an inventory or a dump is among them

### Requirement: Telegram Leaves Through The Shared Exit

On the collection host every MTProto connection SHALL go through a proxy in the namespace whose only way out is the operator's VPN exit. When that exit cannot be reached, a connection SHALL fail rather than leave from the host's own address.

#### Scenario: The collector is proxied

- **WHEN** the collector connects to Telegram
- **THEN** it logs a proxied route naming the namespace's proxy

#### Scenario: The VPN exit is down

- **GIVEN** a VPN server that cannot be reached
- **WHEN** a Telegram command connects
- **THEN** the connection fails and the command exits non-zero
- **AND** nothing reaches Telegram from the host's own address

#### Scenario: Other traffic stays direct

- **WHEN** the bot calls the Bot API, or a YouTube command makes a request
- **THEN** it does not go through the proxy

### Requirement: The Deploy Verifies The Exit

Before reporting success, a deploy SHALL check that the collector logged a proxied route since its last start, and that the address the internet sees through the proxy differs from the one it sees from the host directly. Either check failing SHALL fail the deploy.

#### Scenario: The collector connected directly

- **GIVEN** a collector whose configuration lost the proxy settings
- **WHEN** the deploy verifies
- **THEN** it fails, saying the collector connected directly

#### Scenario: A proxy that goes out directly

- **GIVEN** a proxy that accepts connections but sends them out from the host's own address
- **WHEN** the deploy verifies
- **THEN** it fails, naming the address seen through the proxy as the host's own

#### Scenario: The collector has not connected

- **GIVEN** a collector that logged no route since its last start
- **WHEN** the deploy verifies and the wait for that line runs out
- **THEN** it fails, saying the collector is not getting as far as connecting

### Requirement: Nothing Outside The Namespace Reaches Its Services

The database and the proxy SHALL accept connections only from the namespace's own workloads, and the proxy only from those that speak to Telegram. Nothing in the namespace SHALL be exposed outside the cluster.

#### Scenario: A pod in another namespace

- **WHEN** a pod in another namespace connects to the database's or the proxy's address
- **THEN** the connection is refused or dropped

#### Scenario: The bot and the proxy

- **WHEN** the bot's pod connects to the proxy
- **THEN** the connection is refused or dropped

### Requirement: Schedules And Logs Keep Moscow Time

Scheduled jobs SHALL fire at their configured times in Europe/Moscow, and timestamps in itgraph's own logs and messages SHALL be Moscow time, whatever the host's time zone. The deploy SHALL NOT change the host's time zone.

#### Scenario: A daily job on a host in UTC

- **GIVEN** a host whose time zone is UTC
- **WHEN** the baseline refresh is scheduled for 04:30
- **THEN** it fires at 04:30 Moscow time

#### Scenario: A log line

- **WHEN** a workload writes a log line
- **THEN** its timestamp is Moscow time

#### Scenario: A time in a bot message

- **WHEN** the bot states a time in a message
- **THEN** it is Moscow time

### Requirement: The Session Moves Only From A Stopped Host

A deploy that copies the Telegram session SHALL first confirm, over SSH on the host the session came from, that its collector and bot are stopped and will not start on boot, and SHALL stop without copying otherwise. The operator MAY state that no such host exists, and the report SHALL then say the check was skipped on request.

#### Scenario: The previous host still collects

- **GIVEN** a session volume with no session, and a previous host whose collector is running or enabled
- **WHEN** the full deploy runs
- **THEN** it fails before copying the session or starting the collector, naming the previous host and its running unit

#### Scenario: The previous host is stopped

- **GIVEN** a previous host whose collector and bot are stopped and disabled
- **WHEN** the full deploy runs
- **THEN** the session is copied and the collector is started

#### Scenario: No previous host named

- **GIVEN** a session volume with no session, and no previous host named
- **WHEN** the full deploy runs
- **THEN** it fails before copying, asking for the previous host or an explicit statement that there is none

#### Scenario: Nothing to copy

- **GIVEN** a session volume that already holds a session
- **WHEN** the full deploy runs
- **THEN** the previous host is not consulted

### Requirement: The Namespace Can Be Prepared Without Collecting

The deploy SHALL offer a part that sets up the namespace, the image, the configuration and the proxy, and runs the exit check, without restoring or migrating, without copying the session, and without starting the collector, the bot or any scheduled job.

#### Scenario: Preparing a fresh host

- **GIVEN** a host with no collection namespace
- **WHEN** the operator runs only the preparation part
- **THEN** the proxy runs and its exit is checked against the host's own address
- **AND** the database holds no restored data, no session is in the volume, and nothing speaks to Telegram

### Requirement: The Deploy Fails On A Workload That Keeps Restarting

After the collector and the bot are rolled out, the deploy SHALL watch them for a settling period and fail if either restarted in that time, naming it and how it last exited. A rollout reported as complete SHALL NOT be taken as proof that the workload runs.

#### Scenario: The collector exits at start

- **GIVEN** a collector that exits shortly after it starts
- **WHEN** the deploy rolls it out
- **THEN** the deploy fails, naming the collector and its last exit

### Requirement: A Retired Host Keeps No Collection State

Retiring a collection host SHALL leave it with no running or enabled itgraph service, no Telegram session, no configuration holding secrets and no database, while keeping its dumps. It SHALL refuse to run unless the operator names that host explicitly.

#### Scenario: Retiring the previous host

- **WHEN** the operator retires a host, naming it
- **THEN** no itgraph unit on it is running or enabled
- **AND** no session file, no `.env` and no database volume remain
- **AND** its dumps are where they were

#### Scenario: YouTube data goes with the database

- **WHEN** a host is retired
- **THEN** no YouTube API data remains on it

#### Scenario: Not named

- **WHEN** the retirement is run without the operator naming the host
- **THEN** it changes nothing and says what is missing
