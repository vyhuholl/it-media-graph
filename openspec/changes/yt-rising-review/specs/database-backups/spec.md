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
