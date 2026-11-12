## MODIFIED Requirements

### Requirement: The Bot Cannot Write Collection State

The system SHALL confine the bot's writes to the delivery state of the alert queues and of the weekly lists. The bot MUST NOT modify the channel inventory, the raw layer, metric snapshots, derived edges, collection progress, YouTube's stored data, what an alert is about and the score that raised it, or what a weekly list holds, and this SHALL be enforced by the database's own permissions rather than by convention.

#### Scenario: The bot's credentials cannot write collection tables

- **WHEN** the bot attempts to write to the inventory, the raw layer, the snapshots, the derived edges, or YouTube's payloads and readings
- **THEN** the database refuses it

#### Scenario: The bot can record what it is for

- **WHEN** the bot marks an alert of either queue delivered, held, failed or excluded
- **THEN** the write succeeds

#### Scenario: The bot cannot raise or rewrite a YouTube alert

- **WHEN** the bot attempts to insert a YouTube alert, or to change the video, the reading, the kind or the score of one
- **THEN** the database refuses it

#### Scenario: The bot records a list's delivery

- **WHEN** the bot marks a weekly list or its entries delivered, or counts a failed attempt on a list
- **THEN** the write succeeds

#### Scenario: The bot cannot write a list

- **WHEN** the bot attempts to insert a weekly list or an entry, or to change an entry's rank, channel, video, reading, signal or score
- **THEN** the database refuses it
