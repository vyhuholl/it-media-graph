# Spec Delta

## MODIFIED Requirements

### Requirement: The Service Is Tried On The Restored Data

A restore SHALL end with real writes through the running service, under its read-only root: a baseline refresh of each platform, a full dump, and an alert delivered through the environment's own bot to each destination the environment configures; and with each platform's weekly list computed on the copy without writing. Each SHALL be read back, by rows or by the pass's exit, and a restore that cannot prove one SHALL say which.

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

#### Scenario: Telegram's weekly list on the copy

- **WHEN** the restore runs Telegram's weekly pass with `--preview` on the restored data
- **THEN** it exits zero, no Telegram weekly list is written, and the report says Telegram's weekly list was computed

#### Scenario: A dump of the copy

- **WHEN** the restore runs a full dump
- **THEN** a new verified dump is on the environment's backups volume

#### Scenario: A delivered alert

- **GIVEN** a moment outside quiet hours, from which they do not begin before the trials' wait would end
- **WHEN** the restore raises a synthetic alert on a restored post
- **THEN** the bot delivers it to the environment's chat, and its delivery is recorded

#### Scenario: A delivered YouTube alert per topic

- **GIVEN** an environment that configures a YouTube chat and its two topics, and a moment outside their quiet hours, from which they do not begin before the trials' wait would end
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

#### Scenario: Quiet hours that would begin during the wait

- **GIVEN** a moment outside quiet hours, from which they would begin before the trials' wait ends — Telegram's, and YouTube's where the environment configures it
- **WHEN** the restore reaches the alerts
- **THEN** it raises no alert of either platform, and the report says each could not be proven and when quiet hours begin

#### Scenario: Quiet hours past midnight

- **GIVEN** quiet hours that start later in the day than they end, and a moment after their start or before their end
- **WHEN** the restore reaches the alerts
- **THEN** it raises no alert, and the report says it could not be proven

#### Scenario: Quiet hours with equal bounds

- **GIVEN** quiet hours whose start and end are the same hour, and a moment at that hour
- **WHEN** the restore reaches the alerts
- **THEN** it raises them and waits for their delivery, as at any other moment: equal bounds are no quiet hours

