# Spec Delta

## MODIFIED Requirements

### Requirement: The Bot Cannot Write Collection State

The system SHALL confine the bot's writes to the delivery state of the alert queues, of both platforms' weekly lists and their second wind entries, and of both platforms' rising offers, and to an offer's decision. The bot MUST NOT modify the channel inventory of either platform, the raw layer, metric snapshots, derived edges, collection progress, YouTube's stored data, what an alert is about and the score that raised it, what a weekly list holds, or which channel an offer is and what it was offered with, and this SHALL be enforced by the database's own permissions rather than by convention.

#### Scenario: The bot's credentials cannot write collection tables

- **WHEN** the bot attempts to write to the inventory of either platform, the raw layer, the snapshots, the derived edges, or YouTube's payloads and readings
- **THEN** the database refuses it

#### Scenario: The bot can record what it is for

- **WHEN** the bot marks an alert of either queue delivered, held, failed or excluded
- **THEN** the write succeeds

#### Scenario: The bot cannot raise or rewrite a YouTube alert

- **WHEN** the bot attempts to insert a YouTube alert, or to change the video, the reading, the kind or the score of one
- **THEN** the database refuses it

#### Scenario: The bot records a list's delivery

- **WHEN** the bot marks a weekly list of either platform, its entries or its second wind entries delivered, or counts a failed attempt on a list
- **THEN** the write succeeds

#### Scenario: The bot cannot write a list

- **WHEN** the bot attempts to insert a weekly list or an entry of either platform, or to change an entry's rank, channel, post or video, reading, signal, count or score
- **THEN** the database refuses it

#### Scenario: The bot cannot write a second wind entry

- **WHEN** the bot attempts to insert a second wind entry of either platform, or to change its rank, channel, post or video, readings, acceleration or share
- **THEN** the database refuses it

#### Scenario: The bot records an offer's delivery and its decision

- **WHEN** the bot marks a rising offer of either platform delivered, counts a failed attempt on it, or records its decision, kind, language or reason and when it was decided
- **THEN** the write succeeds

#### Scenario: The bot cannot write an offer or apply a decision

- **WHEN** the bot attempts to insert a rising offer of either platform or a day's selection, to change an offer's channel, rank or numbers, to mark a decision applied, or to change a channel's status, kind, language or reason in either inventory
- **THEN** the database refuses it
