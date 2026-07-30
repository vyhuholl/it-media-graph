## MODIFIED Requirements

### Requirement: Channel Record

The system SHALL store one record per Telegram channel or chat, keyed by its Telegram id. A record MAY carry the family it belongs to; that field is written only by a confirmed review decision, and it distinguishes no member of the family from any other.

#### Scenario: Identity is recorded
- **WHEN** a channel record is created
- **THEN** the Telegram id is its primary key
- **AND** username, title, and whether the entity is a chat are stored when known
- **AND** the discovery source and a first-seen timestamp are recorded

#### Scenario: A channel known only by id
- **WHEN** a channel is recorded from a reference that carries no username or title
- **THEN** the record is created with its Telegram id alone
- **AND** it is marked as awaiting resolution

#### Scenario: A new record is unreviewed
- **WHEN** a channel record is created
- **THEN** its status is `candidate`
- **AND** kind, rejection reason and review timestamp are empty

#### Scenario: Rejection cannot be reasonless
- **WHEN** a record is written with status `rejected` and no rejection reason
- **THEN** the write is refused at the database level

#### Scenario: A new record belongs to no family
- **WHEN** a channel record is created
- **THEN** it belongs to no family
- **AND** no import, resolution or metadata pass writes its family

#### Scenario: The record names no main channel of its family
- **WHEN** a channel's family is read from its record
- **THEN** the answer identifies the family
- **AND** it does not identify any channel as the family's main one

### Requirement: Inventory Listing

The system SHALL provide `itgraph channels`, showing the inventory so review
progress is visible.

#### Scenario: Listing by status
- **WHEN** the command runs with a status filter
- **THEN** only channels with that status are listed
- **AND** each row shows id, username, title, status and kind

#### Scenario: Progress summary
- **WHEN** the command runs without a filter
- **THEN** a count per status is reported

#### Scenario: Listing one family
- **GIVEN** any channel of a family
- **WHEN** the command runs filtered to that channel's family
- **THEN** every channel belonging to it is listed
- **AND** the same set is listed whichever member of the family was named

#### Scenario: Families are visible in the summary
- **WHEN** the command runs without a filter
- **THEN** it reports how many families are recorded
- **AND** how many channels belong to one
- **AND** a family of one unaffiliated channel is counted in neither
