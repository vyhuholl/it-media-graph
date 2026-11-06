# Spec Delta

## ADDED Requirements

### Requirement: A Full Dump Restores Into A Test Environment With One Entry Point

The role's restore entry point SHALL take the path of a full dump on the host and replace the environment's database with it. It SHALL refuse to run unless the environment allows restores and does not collect, and SHALL refuse a file `pg_restore` cannot read, before touching anything. It SHALL never touch the session's volume.

#### Scenario: Refused in an environment that collects

- **GIVEN** an environment that collects
- **WHEN** the restore entry point runs, whatever else it is given
- **THEN** it fails before reading the file or touching anything

#### Scenario: Refused without the input

- **GIVEN** an environment that does not collect, and no input allowing restores
- **WHEN** the restore entry point runs
- **THEN** it fails before reading the file or touching anything

#### Scenario: Not a dump

- **GIVEN** a missing file, a text file, or a dump cut short inside its table of contents
- **WHEN** the restore entry point runs
- **THEN** it fails naming the path, and the database is as it was

#### Scenario: Writers stopped first

- **WHEN** the data is replaced
- **THEN** the bot is stopped, the schedules are suspended, and no Job of the namespace is running
- **AND** afterwards the bot runs and the schedules are as they were

#### Scenario: A copy from the off-site store

- **GIVEN** a dump fetched from the off-site store and decrypted
- **WHEN** it is given to the restore entry point
- **THEN** it restores as any other dump does

### Requirement: A Restore Is Checked Against The Dump

After loading, the restore SHALL compare every table's row count with the rows the dump holds, and fail naming the table on any difference, so that a load that read nothing cannot report success. It SHALL then bring the schema to the deployed image's head before neutralising.

#### Scenario: A load that lost rows

- **GIVEN** a dump cut short inside a table's data
- **WHEN** the restore runs
- **THEN** it fails naming the failure, and the database is left empty

#### Scenario: A dump older than the code

- **GIVEN** a dump one revision behind the deployed image
- **WHEN** the restore runs
- **THEN** the schema reaches head before neutralising touches the new columns

### Requirement: A Restored Copy Acts On Nobody

After a restore, nothing in the copy SHALL cause a message to anyone but the environment's own chat, or a request to Telegram, YouTube or B2. Every alert not yet delivered in the dump SHALL be marked delivered before the bot starts.

#### Scenario: An undelivered alert in the dump

- **GIVEN** a dump holding an alert not delivered
- **WHEN** the restore finishes and the bot starts
- **THEN** no message about that alert is sent

#### Scenario: No collection and no YouTube work

- **WHEN** the restore finishes
- **THEN** the collector is at zero replicas, no YouTube job exists, and the backup makes no upload

#### Scenario: A second restore

- **GIVEN** a dump taken of an already neutralised copy
- **WHEN** it is restored
- **THEN** the restore succeeds and the copy holds the same as the first

### Requirement: A Restored Copy Names Nobody

After a restore, the copy SHALL hold no user id or person's name in a message payload's fields that carry one, no trace of which channels came from the operator's own subscriptions, and no note the operator wrote. The restore SHALL read this back on the real rows and fail if any remains.

#### Scenario: Person fields in payloads

- **GIVEN** seeded messages with recent repliers, a user as author, a post signature, a forward from a user, a hidden sender's name, recent reactions, and a mention by user id
- **WHEN** the restore finishes
- **THEN** none of those values is left in `raw_messages`
- **AND** a forward from a channel, the message's text and its counters are unchanged

#### Scenario: The operator's subscriptions

- **GIVEN** seeded channels from the operator's own subscriptions, a seed and a rejected one among them
- **WHEN** the restore finishes
- **THEN** the seed remains, under another source, and the rejected one is gone
- **AND** no row says `own_subscriptions`

#### Scenario: The operator's notes

- **GIVEN** seeded channels of both platforms with notes, and a decision on a pair of channels with a note
- **WHEN** the restore finishes
- **THEN** no note remains, and every label and decision does

#### Scenario: A table that gains a note

- **WHEN** a column holding a note is added to any table
- **THEN** the test suite fails until the restore clears it and reads it back

#### Scenario: Each statement is needed

- **WHEN** any one neutralising statement is removed in the test suite
- **THEN** the read-back fails

#### Scenario: A failure before neutralising has finished

- **GIVEN** a restore that fails after the data was loaded and before neutralising finished
- **WHEN** it ends
- **THEN** the database is dropped and recreated empty, and the run says so

#### Scenario: Old values off the table's files

- **WHEN** the restore finishes
- **THEN** every table has been rewritten on disk, those a deletion cascaded into included, so their files no longer hold the old rows

### Requirement: The Service Is Tried On The Restored Data

A restore SHALL end with real writes through the running service, under its read-only root: a baseline refresh, a full dump, and an alert delivered through the environment's own bot. Each SHALL be read back, by rows, and a restore that cannot prove one SHALL say which.

#### Scenario: The baseline refresh

- **WHEN** the restore runs a baseline refresh on the restored data
- **THEN** a new completed baseline run is in the database

#### Scenario: A dump of the copy

- **WHEN** the restore runs a full dump
- **THEN** a new verified dump is on the environment's backups volume

#### Scenario: A delivered alert

- **GIVEN** a moment outside quiet hours
- **WHEN** the restore raises a synthetic alert on a restored post
- **THEN** the bot delivers it to the environment's chat, and its delivery is recorded

#### Scenario: Inside quiet hours

- **GIVEN** a moment inside quiet hours
- **WHEN** the restore reaches the alert
- **THEN** the alert stays held, and the report says it could not be proven

### Requirement: A Stale Copy Does Not Fail The Passes

Scheduled passes on a copy that receives no new data SHALL exit zero, however old its newest row, so that a test environment stays quiet between restores.

#### Scenario: A copy weeks old

- **GIVEN** a restored copy whose newest message is 60 days old
- **WHEN** detection, baselines and the backup run
- **THEN** each exits zero
