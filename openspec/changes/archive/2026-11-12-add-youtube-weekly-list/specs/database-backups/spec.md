## MODIFIED Requirements

### Requirement: A Restored Copy Acts On Nobody

After a restore, nothing in the copy SHALL cause a message to anyone but the environment's own chat, or a request to Telegram, YouTube or B2. Every alert and every weekly list not yet delivered in the dump SHALL be marked delivered before the bot starts, and the restore's read-back SHALL fail if one remains.

#### Scenario: An undelivered alert in the dump

- **GIVEN** a dump holding an alert not delivered
- **WHEN** the restore finishes and the bot starts
- **THEN** no message about that alert is sent

#### Scenario: An undelivered weekly list in the dump

- **GIVEN** a dump holding a weekly list computed and not delivered, one of its entries sent and the rest not
- **WHEN** the restore finishes and the bot starts
- **THEN** no message of that list is sent, and the list and every entry are marked delivered

#### Scenario: A weekly list left undelivered fails the read-back

- **GIVEN** a restored copy still holding an undelivered weekly list or entry
- **WHEN** the read-back runs
- **THEN** it fails, naming undelivered weekly lists

#### Scenario: No collection and no YouTube work

- **WHEN** the restore finishes
- **THEN** the collector is at zero replicas, no YouTube job exists, and the backup makes no upload

#### Scenario: A second restore

- **GIVEN** a dump taken of an already neutralised copy
- **WHEN** it is restored
- **THEN** the restore succeeds and the copy holds the same as the first

### Requirement: The Service Is Tried On The Restored Data

A restore SHALL end with real writes through the running service, under its read-only root: a baseline refresh of each platform, a full dump, and an alert delivered through the environment's own bot to each destination the environment configures; and with the weekly list computed on the copy without writing. Each SHALL be read back, by rows or by the pass's exit, and a restore that cannot prove one SHALL say which.

#### Scenario: The baseline refresh

- **WHEN** the restore runs a baseline refresh on the restored data
- **THEN** a new completed baseline run is in the database

#### Scenario: The YouTube baseline refresh

- **WHEN** the restore runs a YouTube baseline refresh on the restored data
- **THEN** a new completed YouTube baseline run is in the database
- **AND** no request has left the cluster

#### Scenario: The weekly list on the copy

- **WHEN** the restore runs the weekly list's pass with `--preview` on the restored data
- **THEN** it exits zero, no weekly list is written, and the report says the weekly list was computed

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

#### Scenario: A copy with no recent YouTube reading

- **GIVEN** a restored copy whose newest YouTube reading is 60 days old
- **WHEN** the YouTube baseline refresh and scoring pass run
- **THEN** each exits zero, and the scoring pass raises nothing

#### Scenario: The weekly list on a stale copy

- **GIVEN** a restored copy whose newest YouTube reading is 60 days old
- **WHEN** the weekly list's pass runs
- **THEN** it exits zero, and each language's list is empty, with no video scored
