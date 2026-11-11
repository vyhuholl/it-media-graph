# database-backups Specification

## Purpose
Where the database's dumps live and what leaves the collection host: every verified dump is also copied off-site, encrypted before it leaves, into a store the host can add to but never delete from or read back, within a fixed budget that costs nothing.

## Requirements

### Requirement: Dumps Outlive The Workloads

The system SHALL keep dumps on storage that survives the removal and recreation of every workload in the collection namespace, the database's own included. Taking and verifying a dump SHALL need nothing installed on the host itself.

#### Scenario: Workloads are recreated

- **GIVEN** dumps taken on the collection host
- **WHEN** every workload in the namespace is deleted and deployed again
- **THEN** the same dumps are present afterwards

#### Scenario: Nothing on the host but the cluster

- **GIVEN** a host with no container runtime or PostgreSQL client of its own beyond the cluster's
- **WHEN** a scheduled backup runs
- **THEN** the dump is taken and read back with `pg_restore --list` as before

### Requirement: Full Dumps Stay Four Weeks

The system SHALL keep every local full dump taken in the last 28 days, the dump taken before a migration included, and SHALL keep the four newest whatever their age. A full dump SHALL be pruned only when it is both older than 28 days and not among the four newest.

#### Scenario: A day of migrations

- **GIVEN** weekly full dumps from the last four weeks
- **WHEN** three migrations in one day each take a full dump first
- **THEN** every weekly full dump from the last four weeks is still there

#### Scenario: Dumps that stopped

- **GIVEN** no full dump taken for six weeks
- **WHEN** the next one is taken
- **THEN** the four newest full dumps remain

### Requirement: Every Verified Dump Is Copied Off-Site

The system SHALL copy every dump that passed its read-back to an off-site store: inventory and full dumps alike, the dump taken before a migration included. A dump that failed its read-back SHALL NOT be copied. The copy is the same dump.

#### Scenario: A scheduled dump is copied

- **WHEN** a scheduled backup takes and verifies a dump
- **THEN** the same run copies it off-site

#### Scenario: The pre-migration dump is copied

- **WHEN** a migration takes its full dump first
- **THEN** that dump is copied off-site no later than the next scheduled backup run

#### Scenario: A dump that failed its read-back stays home

- **WHEN** a dump fails `pg_restore --list`
- **THEN** nothing is uploaded for it

#### Scenario: A failed upload is caught up

- **GIVEN** a verified dump whose upload failed
- **WHEN** the next backup run starts
- **THEN** that dump is uploaded before the run reports success

#### Scenario: A busy store is asked again

- **WHEN** the store answers an upload with a transient failure — busy, timed out, rate-limited, or a dropped connection
- **THEN** the upload is tried again at a new upload URL after a growing pause, a bounded number of times
- **AND** a refusal that is not transient, such as the spending cap, is not tried again

#### Scenario: A failed upload is a failed run

- **WHEN** an upload fails
- **THEN** the backup run exits non-zero and says which dump was not copied
- **AND** the local dump and its pruning are unaffected

### Requirement: Copies Are Encrypted Before They Leave

The system SHALL encrypt every copy on the host, to a public recipient key, before any byte of it is sent. The private key SHALL NOT be on the collection host or in the repository.

#### Scenario: The store holds ciphertext

- **WHEN** an off-site copy is downloaded
- **THEN** it is not readable as a PostgreSQL archive without the private key

#### Scenario: The host cannot read its own copies

- **WHEN** the collection host's configuration and volumes are searched
- **THEN** no key able to decrypt an off-site copy is found

#### Scenario: A copy decrypts to the dump

- **GIVEN** an off-site copy and the operator's private key
- **WHEN** the copy is decrypted
- **THEN** the result is byte-identical to the local dump it was made from
- **AND** `pg_restore --list` reads it

### Requirement: The Host Cannot Remove Or Replace A Copy

The credential the collection host holds for the off-site store SHALL allow adding copies and listing their names, and SHALL NOT allow deleting, overwriting or downloading them. A copy's name SHALL be unique, so that a new copy never takes the place of an old one.

#### Scenario: Deleting with the host's credential

- **WHEN** a delete of an off-site copy is attempted with the host's credential
- **THEN** the store refuses it

#### Scenario: A name already in the store

- **GIVEN** a dump whose copy is already in the store
- **WHEN** a backup run starts
- **THEN** it is not uploaded again

### Requirement: A Copy Counts Once The Store Confirms It

The system SHALL treat a dump as copied only when the store has confirmed the upload against a checksum computed on the host. An upload whose checksum the store did not confirm SHALL count as failed.

#### Scenario: The checksum does not match

- **WHEN** the store rejects an upload because the content does not match the checksum sent with it
- **THEN** the dump is reported as not copied, and the run fails

### Requirement: Retention Belongs To The Store

Off-site copies SHALL be removed by the store's own rules after a fixed age, one age for inventory copies and one for full copies. The collection host SHALL NOT prune off-site copies, and pruning local dumps SHALL NOT affect them.

#### Scenario: An old copy goes by itself

- **GIVEN** an off-site copy older than the retention for its kind
- **THEN** the store removes it without any action from the host

#### Scenario: Local pruning stays local

- **WHEN** a backup run prunes local dumps
- **THEN** no off-site copy is removed

### Requirement: Off-Site Copies Cost Nothing

The off-site copies SHALL stay under a fixed size ceiling inside the store's free tier, and the store's spending cap SHALL be zero, so that going over fails uploads instead of billing.

#### Scenario: Retention fits the ceiling

- **GIVEN** the current sizes of inventory and full dumps and the configured retention
- **THEN** the copies the store keeps add up to less than the ceiling

#### Scenario: Over the cap

- **WHEN** an upload would take the store past its free tier
- **THEN** the store refuses it
- **AND** the backup run fails and says so, rather than anything being billed

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

#### Scenario: A dump that carries the reader's grants

- **GIVEN** a full dump of a database at head, whose grants and default privileges name `itgraph_reader`
- **WHEN** it is restored into a test environment deployed at the same release
- **THEN** it loads whole, every table's rows match the dump, the read-back passes
- **AND** the reader reads every table of the restored copy, including through the default privileges, and can write none

#### Scenario: A dump older than the reader

- **GIVEN** a full dump of a database at a revision before the reader's
- **WHEN** it is restored
- **THEN** the migration to head grants the reader every table of the restored copy

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

After a restore, the copy SHALL hold nothing that names a person anywhere in a message's or a channel's payload (personal-data, "What Names A Person"), no trace of which channels came from the operator's own subscriptions, and no note the operator wrote. The restore SHALL read this back on the real rows, by type and not by path, and fail if any remains.

#### Scenario: Person fields in payloads

- **GIVEN** a dump of the revision before people were cleared, with seeded messages naming people in every way the rule names, paid reactors and commenters among them, and a channel among each
- **WHEN** the restore finishes
- **THEN** no payload names a person
- **AND** the channels among the commenters and the reactors, a forward from a channel, the message's text and its counters are unchanged

#### Scenario: A person in a dump at head

- **GIVEN** a dump of the current revision in which a payload names a person, at a path no earlier rule named
- **WHEN** the restore reads back
- **THEN** it fails, naming what it found, and the database is left empty

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

A restore SHALL end with real writes through the running service, under its read-only root: a baseline refresh, a full dump, and an alert delivered through the environment's own bot to each destination the environment configures. Each SHALL be read back, by rows, and a restore that cannot prove one SHALL say which.

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

#### Scenario: A delivered YouTube alert per topic

- **GIVEN** an environment that configures a YouTube chat and its two topics, and a moment outside their quiet hours
- **WHEN** the restore raises a synthetic YouTube alert on a synthetic video of a restored English seed, and one of a restored Russian seed
- **THEN** the bot delivers each to its topic of the environment's YouTube chat, and each delivery and route is recorded
- **AND** the synthetic videos, their readings and their alerts are gone once the restore ends

#### Scenario: No YouTube chat in the environment

- **GIVEN** an environment that configures no YouTube chat
- **WHEN** the restore reaches the alerts
- **THEN** it raises no YouTube alert, and the report says YouTube delivery was not configured

#### Scenario: Inside quiet hours

- **GIVEN** a moment inside a destination's quiet hours
- **WHEN** the restore reaches that destination's alert
- **THEN** the alert is not sent, and the report says it could not be proven

### Requirement: A Stale Copy Does Not Fail The Passes

Scheduled passes on a copy that receives no new data SHALL exit zero, however old its newest row, so that a test environment stays quiet between restores.

#### Scenario: A copy weeks old

- **GIVEN** a restored copy whose newest message is 60 days old
- **WHEN** detection, baselines and the backup run
- **THEN** each exits zero
