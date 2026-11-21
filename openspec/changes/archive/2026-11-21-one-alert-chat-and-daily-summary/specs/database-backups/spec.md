# Spec Delta

## MODIFIED Requirements

### Requirement: A Restored Copy Acts On Nobody

After a restore, nothing in the copy SHALL cause a message to anyone but the environment's own chat, or a request to Telegram, YouTube or B2. Every alert, every weekly list of either platform, every second wind entry and every rising offer of either platform not yet delivered in the dump SHALL be marked delivered before the bot starts, every rising offer's decision not yet applied SHALL be closed unapplied, and the restore's read-back SHALL fail if one remains.

#### Scenario: An undelivered alert in the dump

- **GIVEN** a dump holding an alert not delivered
- **WHEN** the restore finishes and the bot starts
- **THEN** no message about that alert is sent

#### Scenario: An undelivered weekly list in the dump

- **GIVEN** a dump holding a weekly list computed and not delivered, one of its entries sent and the rest not
- **WHEN** the restore finishes and the bot starts
- **THEN** no message of that list is sent, and the list and every entry are marked delivered

#### Scenario: An undelivered Telegram list in the dump

- **GIVEN** a dump holding a Telegram weekly list computed and not delivered
- **WHEN** the restore finishes and the bot starts
- **THEN** no message of that list is sent, and the list and every entry are marked delivered

#### Scenario: A weekly list left undelivered fails the read-back

- **GIVEN** a restored copy still holding an undelivered weekly list or entry of either platform
- **WHEN** the read-back runs
- **THEN** it fails, naming undelivered weekly lists

#### Scenario: An undelivered second wind entry in the dump

- **GIVEN** a dump holding a list of either platform with second wind entries not delivered
- **WHEN** the restore finishes and the bot starts
- **THEN** no message of them is sent, and each is marked delivered
- **AND** the read-back fails while one remains undelivered

#### Scenario: An undelivered rising offer in the dump

- **GIVEN** a dump holding a day's selection computed at 11:00 and not yet sent
- **WHEN** the restore finishes and the bot starts
- **THEN** no card of it is sent, and each offer is marked delivered
- **AND** the read-back fails while one remains undelivered

#### Scenario: An undelivered YouTube offer in the dump

- **GIVEN** a dump holding a YouTube offer computed and not yet sent
- **WHEN** the restore finishes and the bot starts
- **THEN** no card of it is sent, and the offer is marked delivered
- **AND** the read-back fails while it remains undelivered

#### Scenario: A day summary marker left open fails the read-back

- **GIVEN** a dump holding a day summary marker whose day was never sent
- **WHEN** the restore finishes and the bot starts
- **THEN** the marker is closed as sent, and no summary of that day goes out
- **AND** the read-back fails while one remains open

#### Scenario: A decision not yet applied in the dump

- **GIVEN** a dump holding a seed decided on a card and not yet applied
- **WHEN** the restore finishes and the detection pass runs on the copy
- **THEN** the channel's review in the copy is unchanged, and the decision is closed as not applied
- **AND** the read-back fails while one remains unapplied

#### Scenario: No collection and no YouTube work

- **WHEN** the restore finishes
- **THEN** the collector is at zero replicas, no YouTube job exists, and the backup makes no upload

#### Scenario: A second restore

- **GIVEN** a dump taken of an already neutralised copy
- **WHEN** it is restored
- **THEN** the restore succeeds and the copy holds the same as the first

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
- **THEN** the bot delivers it to the environment's chat's Telegram topic, and its delivery is recorded

#### Scenario: A delivered YouTube alert per topic

- **GIVEN** a moment outside the topics' quiet hours, from which they do not begin before the trials' wait would end
- **WHEN** the restore raises a synthetic YouTube alert on a synthetic video of a restored English seed, and one of a restored Russian seed
- **THEN** the bot delivers each to its topic of the environment's chat, and each delivery and route is recorded
- **AND** the synthetic videos, their readings and their alerts are gone once the restore ends

#### Scenario: No YouTube chat in the environment

- **GIVEN** an environment that configures no alert chat
- **WHEN** the restore reaches the alerts
- **THEN** it raises no alert of either platform, and the report says delivery was not configured

#### Scenario: Inside quiet hours

- **GIVEN** a moment inside a destination's quiet hours
- **WHEN** the restore reaches that destination's alert
- **THEN** the alert is not sent, and the report says it could not be proven

#### Scenario: Quiet hours that would begin during the wait

- **GIVEN** a moment outside quiet hours, from which they would begin before the trials' wait ends — Telegram's, and the topics' alike
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
