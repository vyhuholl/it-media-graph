# collection-deploy Specification

## Purpose
Deploying to the collection host, whether to a fresh machine or again to one that is already collecting: what a deploy may do to the data already there, the order in which the schema and the services change, and what the operator is told afterwards.

## Requirements

### Requirement: Unknown Database State Stops The Deploy

A deploy SHALL NOT treat a database whose state it could not read as empty. Only a missing channel table SHALL count as an empty database; any other failure to count channels or to check coherence SHALL stop the deploy before a migration or any change to the collector, the bot or a scheduled job.

#### Scenario: The channel count fails

- **GIVEN** a host where counting channels fails for any reason other than the table not existing
- **WHEN** the deploy runs
- **THEN** the deploy fails with the error it received

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

A deploy SHALL bring the schema to the newest revision of the deployed image before it creates, changes or restarts the collector, the bot or any scheduled job, and SHALL stop if the schema is not at head afterwards. The migration SHALL run from the deployed image, in a pod of its own.

#### Scenario: A full deploy with a pending migration

- **GIVEN** a host whose schema is one revision behind the deployed image
- **WHEN** the deploy runs
- **THEN** the schema is migrated to head before the collector, the bot or any scheduled job is created or given the new image

#### Scenario: Scheduled jobs wait for the schema

- **GIVEN** a deploy whose image carries a migration
- **WHEN** a scheduled job's image is about to change
- **THEN** the schema is already at head, since the schedules were suspended and running jobs had ended before the migration

#### Scenario: The migration fails

- **WHEN** the migration fails during a deploy
- **THEN** the deploy stops
- **AND** the collector, the bot and the scheduled jobs keep the image they had

#### Scenario: Services alone on a schema that is behind

- **GIVEN** a schema that a gate finds behind the deployed image once the migration step has passed
- **WHEN** the deploy reaches the collector, the bot and the scheduled jobs
- **THEN** it fails before touching any of them, saying the schema is not at head

#### Scenario: A schema ahead of the image

- **GIVEN** a schema at a revision the deployed image does not know
- **WHEN** the deploy runs
- **THEN** it fails before changing any workload, naming both revisions

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

### Requirement: The Deploy Touches Nothing Outside Its Namespace

A deploy SHALL create and change objects only inside the collection namespace. It SHALL NOT install packages on the host, change its firewall, swap, power settings or time zone, or create or change anything in another namespace.

#### Scenario: A full deploy on a fresh host

- **WHEN** the full deploy runs on a host for the first time
- **THEN** the host's packages, firewall rules, swap, power settings and time zone are as they were before
- **AND** every object the deploy created is in the collection namespace

#### Scenario: Removing itgraph

- **WHEN** the collection namespace's workloads are deleted
- **THEN** nothing itgraph created remains running on the host

### Requirement: Telegram Leaves Through The Shared Exit

Where an environment collects, every MTProto connection SHALL go through a proxy in the namespace whose only way out is the operator's VPN exit. When that exit cannot be reached, a connection SHALL fail rather than leave from the host's own address. The network policy SHALL hold this even if the proxy's settings are lost.

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

Before reporting success, a deploy in an environment that collects SHALL check that the collector logged a proxied route since its last start, or, for a collector this run did not change and whose log no longer holds the line, that its environment names the proxy; and that the address the internet sees through the proxy differs from the one it sees directly. In an environment that does not collect, it SHALL check that a connection through the proxy fails. Any check failing SHALL fail the deploy.

#### Scenario: The collector connected directly

- **GIVEN** a collector whose configuration lost the proxy settings
- **WHEN** the deploy verifies
- **THEN** it fails, saying the collector connected directly

#### Scenario: A proxy that goes out directly

- **GIVEN** a proxy that accepts connections but sends them out from the host's own address
- **WHEN** the deploy verifies
- **THEN** it fails, saying the address seen through the proxy is the host's own

#### Scenario: The collector has not connected

- **GIVEN** a collector that logged no route since its last start
- **WHEN** the deploy verifies and the wait for that line runs out
- **THEN** it fails, saying the collector is not getting as far as connecting

#### Scenario: A collector whose log has rotated

- **GIVEN** a collector this run did not change, whose log no longer holds the route line
- **WHEN** the deploy verifies
- **THEN** it reads the route from the collector's environment, and passes only if that names the proxy

#### Scenario: Polls advance

- **GIVEN** an environment that collects, outside quiet hours
- **WHEN** the deploy verifies
- **THEN** it waits a bounded time for a poll newer than the collector's start, and fails if none comes

#### Scenario: A closed exit in a test environment

- **GIVEN** an environment that does not collect
- **WHEN** a connection through the proxy is tried
- **THEN** it fails, and the deploy passes this check

### Requirement: Nothing Outside The Namespace Reaches Its Services

The database and the proxy SHALL accept connections only from the namespace's own workloads that need them, and the proxy only from those that speak to Telegram. The node SHALL be able to reach the database, for an operator's tunnel. Nothing in the namespace SHALL be exposed outside the cluster.

#### Scenario: A pod in another namespace

- **WHEN** a pod in another namespace connects to the database's or the proxy's address
- **THEN** the connection is refused or dropped

#### Scenario: The bot and the proxy

- **WHEN** the bot's pod connects to the proxy
- **THEN** the connection is refused or dropped

#### Scenario: The proxy and the database

- **WHEN** the proxy's pod connects to the database
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

### Requirement: The Deploy Fails On A Workload That Keeps Restarting

After a long-running workload is rolled out, the database included, the deploy SHALL watch it for a settling period and fail if it restarted in that time, naming it and how it last exited. A rollout reported as complete SHALL NOT be taken as proof that the workload runs. A Deployment this run gave a new template SHALL be put back to its previous one if it does not come up. The database SHALL NOT be put back by a run: the run SHALL fail, saying where the runbook covers doing it by hand.

#### Scenario: The collector exits at start

- **GIVEN** a collector that exits shortly after it starts
- **WHEN** the deploy rolls it out
- **THEN** the deploy fails, naming the collector and its last exit
- **AND** the collector is put back to its previous template

#### Scenario: A workload this run only stopped and started

- **GIVEN** a collector whose template this run did not change, stopped for the database's sake
- **WHEN** it does not come up again
- **THEN** the deploy fails, naming it, and its template is left as it is

#### Scenario: The database does not come up

- **GIVEN** a database template whose pod does not become ready
- **WHEN** the deploy rolls it out
- **THEN** the deploy fails, pointing at the runbook, and the database is not put back
- **AND** the schedules are as they were before it

#### Scenario: A restart from before the deploy

- **GIVEN** a collector that restarted last week and is running
- **WHEN** a deploy changes nothing about it
- **THEN** the deploy does not fail on that restart

### Requirement: A Deploy Is A Role Run With Its Inputs

A deploy SHALL be a run of the role in this repository, by an operator, at a release pinned by the repository that owns the host. Everything about where it runs SHALL be an input: the role SHALL name no host and no repository. It SHALL check its inputs before it changes anything.

#### Scenario: A missing or malformed input

- **GIVEN** an input missing, or an image not named as `repo:tag@sha256:…`
- **WHEN** the role runs
- **THEN** it fails naming the input, before any call that changes the cluster

#### Scenario: Nothing deploys by itself

- **WHEN** a commit is pushed to `master` or a tag is pushed
- **THEN** nothing in any cluster changes

#### Scenario: A second run changes nothing

- **GIVEN** a deploy that has just finished
- **WHEN** the role runs again with the same inputs
- **THEN** no workload is restarted, and every apply reports no change

#### Scenario: A check

- **WHEN** the role runs in check mode
- **THEN** it shows the changes it would make and applies none

### Requirement: A Release Publishes The Role And Tags The Image

A version tag on a commit of `master` SHALL publish a release: the role as `itgraph-ansible.tar.gz` holding one directory named `itgraph`, its `SHA256SUMS`, and the image CI built for that commit under the version's tag, by digest, with no rebuild. The tests and the role's checks SHALL pass first, or nothing is published.

#### Scenario: A tag on master

- **GIVEN** a commit on `master` whose image exists
- **WHEN** `vX.Y.Z` is pushed on it
- **THEN** the release holds the archive and its sum
- **AND** the image's new tag has the digest of the commit's image

#### Scenario: A tag off master

- **WHEN** a tag is pushed on a commit that is not on `master`
- **THEN** nothing is tagged or published

#### Scenario: A tag ahead of the image

- **GIVEN** a tag pushed before CI has built the commit's image
- **WHEN** the release runs
- **THEN** it waits a bounded time for the image, and fails without publishing when it does not appear

#### Scenario: Red tests

- **WHEN** the tests or the role's checks fail on a tag
- **THEN** nothing is tagged or published

#### Scenario: The same commit packs the same

- **WHEN** the role is packed twice from one commit
- **THEN** the two archives are byte-identical

#### Scenario: A push to master gates nothing

- **WHEN** a commit is pushed to `master`
- **THEN** its image is built, and no test runs in CI

### Requirement: Two Runs Never Interleave

The role SHALL hold a lock in the namespace for the length of a run, and a restore SHALL take the same one. A second run SHALL wait, or take the lock over once it is older than any run could last.

#### Scenario: Two runs at once

- **WHEN** two runs start together
- **THEN** one proceeds, and the other waits or fails having changed nothing

#### Scenario: A run that was killed

- **GIVEN** a lock left by a run that was killed, older than the longest run
- **WHEN** a new run starts
- **THEN** it takes the lock over

### Requirement: Pods Are Restricted And Read-Only

The namespace SHALL enforce the `restricted` Pod Security Standard, and every container, init containers included, SHALL run with a read-only root filesystem, as a non-root user. Applying the label SHALL treat any warning about a running pod as a failure.

#### Scenario: Every container

- **WHEN** the role's objects are rendered, with and without collection, with and without a YouTube key
- **THEN** every container sets `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, drops all capabilities and runs as non-root with `RuntimeDefault` seccomp

#### Scenario: A pod that would not be admitted

- **GIVEN** a namespace enforcing `restricted`
- **WHEN** the server is asked to admit each pod template of the role
- **THEN** it admits all of them

#### Scenario: The database on a volume a root-started image wrote

- **GIVEN** a data volume first written by the image starting as root
- **WHEN** the database starts under the role's manifest, as uid 999 with a read-only root
- **THEN** it comes up with its data, and its rows are unchanged

#### Scenario: The database on a fresh volume

- **GIVEN** an empty volume
- **WHEN** the database starts under the role's manifest
- **THEN** it initializes and answers

#### Scenario: A pod in breach when the label is applied

- **GIVEN** a running pod that breaches `restricted`
- **WHEN** the role applies the label
- **THEN** the run fails, naming the warning

### Requirement: Each Workload Holds Only Its Own Secrets

Each workload's pods SHALL receive only the configuration that workload reads. A changed value SHALL restart only the long-running workloads that read it.

#### Scenario: The bot's pod

- **WHEN** the bot's pod is rendered
- **THEN** it holds its own database role's URL, the bot's token and the chat id
- **AND** no superuser URL, Telegram API credential, proxy password, B2 key or YouTube key

#### Scenario: The analysis jobs

- **WHEN** the detection and baseline jobs are rendered
- **THEN** they hold the database's URL and no other secret

#### Scenario: The collector

- **WHEN** the collector's pod is rendered
- **THEN** it holds the database's URL, the Telegram API credentials and the proxy's settings
- **AND** no bot token, B2 key or YouTube key

#### Scenario: A new bot token

- **WHEN** the bot's token changes and the role runs
- **THEN** the bot restarts and the collector does not

#### Scenario: A new password for the bot's own role

- **WHEN** the password of the bot's database role changes and the role runs
- **THEN** the bot restarts and the collector does not

#### Scenario: A process that needs no Telegram credentials

- **GIVEN** a process with no Telegram API credentials configured
- **WHEN** it starts and never builds a Telegram client
- **THEN** its settings load

#### Scenario: A Telegram command without them

- **GIVEN** no Telegram API credentials configured
- **WHEN** a command builds a Telegram client
- **THEN** it exits non-zero naming what is missing, before any connection

### Requirement: Egress Is Denied Unless Named

Every pod's outbound traffic SHALL be denied except what its workload needs. The collector SHALL reach DNS, the database and the proxy, and nothing else. Passes that make no request outside the cluster SHALL reach DNS and the database alone. Of the workloads, only the proxy, the bot, the YouTube jobs and the backup SHALL reach the internet.

#### Scenario: The collector going direct

- **GIVEN** a pod with the collector's labels and no proxy settings
- **WHEN** it opens a connection to an address on the internet
- **THEN** the connection does not leave the namespace

#### Scenario: The collector through the proxy

- **WHEN** a pod with the collector's labels connects to the proxy
- **THEN** the connection is accepted

#### Scenario: An analysis job

- **WHEN** a detection or baseline pod opens a connection to an address on the internet
- **THEN** the connection does not leave the namespace

#### Scenario: Short-lived pods that may leave

- **WHEN** an operator's console Job that asked for neither the session nor the backups, or the role's pod reading the address seen directly, connects to a public address
- **THEN** the connection is accepted, for as long as that pod lasts

#### Scenario: The database

- **WHEN** the database's pod opens a connection anywhere
- **THEN** the connection does not leave the pod

### Requirement: Operators Reach The Database At A Fixed Address

The database SHALL also be served at a fixed ClusterIP, given as an input, where an operator's SSH tunnel to their own account ends. No database port SHALL be open to the internet.

#### Scenario: A tunnel from an operator's own account

- **GIVEN** an operator's account on the host
- **WHEN** they connect through an SSH tunnel to the fixed address with a database role's password
- **THEN** the connection is accepted

#### Scenario: No credential

- **WHEN** something connects to the fixed address with no valid password
- **THEN** the database refuses it

### Requirement: An Environment Says Whether It Collects

Whether an environment runs the collector SHALL be an input with no default. Where it does not, the collector SHALL stay at zero replicas, the proxy's only outbound SHALL close every connection, and the role SHALL refuse any other outbound, a YouTube key and an off-site store. Where it does, the role SHALL refuse that outbound. No task of the role SHALL write a session.

#### Scenario: A test environment

- **GIVEN** an environment that does not collect
- **WHEN** the role has run
- **THEN** the collector's Deployment exists at zero replicas
- **AND** nothing in the namespace has reached Telegram

#### Scenario: The wrong outbound for the environment

- **WHEN** the role is given a real outbound where collection is off, or the closing outbound where it is on
- **THEN** it fails before changing anything

#### Scenario: A key or a store where collection is off

- **WHEN** the role is given a YouTube key or an off-site store where collection is off
- **THEN** it fails before changing anything, naming the inputs and printing neither value

#### Scenario: The collector's spec is tried without an account

- **GIVEN** an environment that does not collect
- **WHEN** the role starts one short-lived pod with the collector's spec and the empty session volume
- **THEN** the pod takes the lease, logs a proxied route naming the namespace's proxy, and exits non-zero
- **AND** the role fails unless all three happened
- **AND** nothing reaches Telegram

#### Scenario: A hand-run command in such an environment

- **GIVEN** an environment that does not collect
- **WHEN** an operator runs a command that opens a Telegram connection
- **THEN** it fails, and nothing reaches Telegram

### Requirement: A Deploy Takes Over What Is Already There

Applied to a namespace made by the previous deploy path, the role SHALL keep every object's name, selector and claim, and SHALL leave the session, the database and the backups as they were. It SHALL delete only objects it names, one by one, and SHALL NOT delete a volume claim.

#### Scenario: The first run on a live namespace

- **GIVEN** a namespace with workloads applied by the previous path, a session, a database with channels and dumps
- **WHEN** the role runs
- **THEN** the session file's checksum is unchanged
- **AND** every table's row count is at least what it was, and the dumps are still there
- **AND** every claim is bound to the volume it had

#### Scenario: What is deleted

- **WHEN** the role has run on that namespace
- **THEN** the console's Deployment, the two CronJobs merged into one and the old shared Secret are gone, each by name

#### Scenario: A claim on a class that deletes its volume

- **GIVEN** a storage class whose `reclaimPolicy` is not `Retain`
- **WHEN** the role runs
- **THEN** it fails before making or changing any claim

### Requirement: Writers Stop Before The Database Changes

When a run changes the database's pod or its owner's password, or migrates the schema, it SHALL first stop the collector and the bot gracefully, suspend the scheduled jobs and wait for running ones to end, and SHALL afterwards put the schedules back as it found them, whatever the outcome. An operator's console Job is not waited for: it sleeps for hours, whether or not a command is running in it.

#### Scenario: The database's template changes

- **GIVEN** a run that will restart the database
- **WHEN** the database's pod is replaced
- **THEN** no collector or bot pod exists, and no Job of the namespace is running but an operator's console

#### Scenario: Nothing about the database changes

- **GIVEN** a run whose database template, schema and owner's password are unchanged
- **WHEN** the run finishes
- **THEN** the database was not restarted, and the collector and the bot stopped only if their own templates changed

#### Scenario: A run that fails midway

- **GIVEN** a run that suspended the schedules and then fails
- **WHEN** it ends
- **THEN** the schedules are as they were before it, and the failure names the step

#### Scenario: Stopped by hand

- **GIVEN** the collector scaled to zero by an operator, in an environment that collects
- **WHEN** the role runs
- **THEN** it fails before changing anything, saying so and how to start it

### Requirement: A Deploy Never Replaces Data

A deploy SHALL NOT restore or replace data. On a database with no tables it SHALL create the schema and start nothing that needs a session unless one is there. Restoring is the restore entry point's alone.

#### Scenario: A live database

- **GIVEN** a database holding channels
- **WHEN** the role's main entry point runs
- **THEN** nothing is restored, and the report says how many channels are there

#### Scenario: An empty database

- **GIVEN** a database with no tables
- **WHEN** the role's main entry point runs
- **THEN** the schema reaches head, and the database holds no channels

### Requirement: A Broken Database Stops The Deploy

A deploy SHALL stop when the database holds channels but some column references a type that no longer exists. It SHALL stop before any migration and before the collector, the bot or any scheduled job is created, changed or restarted. The message SHALL point at restoring from the host's own backups by hand, as the runbook says.

#### Scenario: A broken live database

- **GIVEN** a host whose database holds channels and has columns referencing a missing type
- **WHEN** the role's main entry point runs
- **THEN** the deploy fails with the number of affected columns
- **AND** nothing is restored and nothing is migrated
- **AND** the collector, the bot and the scheduled jobs are not created, changed or restarted
- **AND** the message points at restoring from the host's own backups

### Requirement: A Deploy Never Writes A Session

A deploy SHALL NOT create, copy, change or replace a Telegram session. Where the environment collects, it SHALL stop before starting the collector when the session volume holds no session, or an empty file, or something that is not a regular file. It SHALL NOT read the session's content.

#### Scenario: A live host

- **GIVEN** a session volume holding a non-empty session file
- **WHEN** the deploy runs
- **THEN** the session's checksum is unchanged, and the deploy continues to the services

#### Scenario: No session

- **GIVEN** an environment that collects, and a session volume with no session file
- **WHEN** the deploy runs
- **THEN** the deploy fails before the collector is started
- **AND** the message says to bring the session from where it was collecting by the runbook, and not to create a new one

#### Scenario: A broken session on the host

- **GIVEN** a session volume whose session path is an empty file, or not a regular file
- **WHEN** the deploy runs
- **THEN** the deploy fails before the collector is started, and the file in the volume is not replaced

#### Scenario: An environment that does not collect

- **GIVEN** an environment that does not collect
- **WHEN** the deploy runs
- **THEN** the session volume is not checked for a session

### Requirement: Workloads Run The Deployed Code

A deploy SHALL leave the collector and the bot running the deployed image and their own configuration once the schema is at head. It SHALL restart either only when its image or the configuration it reads differs from what it is running; a deploy that changed neither SHALL NOT restart it. A restart SHALL stop the old instance before the new one starts.

#### Scenario: New code is deployed

- **GIVEN** a running collector and bot
- **WHEN** a deploy deploys an image they are not running
- **THEN** both are restarted after the schema is at head

#### Scenario: A re-run after a deploy that failed midway

- **GIVEN** an earlier deploy that migrated to a new image and failed before the services
- **WHEN** the deploy runs again with the same image
- **THEN** both are restarted after the schema is at head, because they still run the old image

#### Scenario: Configuration of one workload

- **WHEN** the deploy runs and only the bot's configuration changed
- **THEN** the bot is restarted and the collector is not

#### Scenario: Nothing changed

- **WHEN** the deploy runs and neither the image nor any configuration changed
- **THEN** neither is restarted

#### Scenario: Never two at once

- **WHEN** the collector or the bot is restarted by a deploy
- **THEN** at no moment do two instances of it run

### Requirement: The Run Reports What It Did

At the end of a deploy, the report SHALL state the release and image digests deployed, whether a migration ran and between which revisions, where the pre-migration dump was written, how many channels are there, which workloads were restarted, and, for each check, whether it passed or could not be made.

#### Scenario: A re-run with nothing to migrate

- **WHEN** a deploy on a live host at head finishes
- **THEN** the report names the release and digests, the channel count, that no migration ran, and which workloads were restarted, if any

#### Scenario: Inside quiet hours

- **GIVEN** a deploy that ends inside the collector's quiet hours
- **WHEN** it reports
- **THEN** it says that polls advancing could not be checked, and until when

### Requirement: The Deployed Code Is The Pinned Image

Every workload that runs itgraph's code SHALL run one image, built by CI from a commit pushed to the repository and referenced by digest, as the release's pin gives it. The image SHALL contain nothing the repository does not track. A deploy SHALL NOT pick a version itself.

#### Scenario: One image everywhere

- **WHEN** a deploy finishes
- **THEN** the collector, the bot, the exporter and every scheduled job reference the same image digest

#### Scenario: A moved tag changes nothing running

- **WHEN** an image tag in the registry is moved to a different image
- **THEN** no running workload or scheduled job changes image

#### Scenario: Nothing ignored is in the image

- **WHEN** the image's files are listed
- **THEN** none of `.env`, a session file, a vault password, an inventory or a dump is among them

#### Scenario: The database's client and server

- **GIVEN** a database image whose major version is not the one the application image's client is built for
- **WHEN** the role runs
- **THEN** it fails before changing anything, naming both
