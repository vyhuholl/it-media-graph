## MODIFIED Requirements

### Requirement: The Service Is Tried On The Restored Data

A restore SHALL end with real writes through the running service, under its read-only root: a baseline refresh of each platform, a full dump, and an alert delivered through the environment's own bot to each destination the environment configures. Each SHALL be read back, by rows, and a restore that cannot prove one SHALL say which.

#### Scenario: The baseline refresh

- **WHEN** the restore runs a baseline refresh on the restored data
- **THEN** a new completed baseline run is in the database

#### Scenario: The YouTube baseline refresh

- **WHEN** the restore runs a YouTube baseline refresh on the restored data
- **THEN** a new completed YouTube baseline run is in the database
- **AND** no request has left the cluster

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
