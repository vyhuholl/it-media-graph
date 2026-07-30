## MODIFIED Requirements

### Requirement: Channel Record

The system SHALL store one record per Telegram channel or chat, keyed by its Telegram id. A record MAY name the canonical channel of the family it belongs to; that field is written only by a confirmed review decision.

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
- **THEN** its family pointer is empty
- **AND** no import, resolution or metadata pass writes it

#### Scenario: The family pointer names a channel in the inventory
- **WHEN** a record's family pointer is written naming a channel the inventory does not hold
- **THEN** the write is refused at the database level

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
- **WHEN** the command runs filtered to one family
- **THEN** every channel belonging to it is listed, canonical channel included
- **AND** which of them is canonical is visible

#### Scenario: Families are visible in the summary
- **WHEN** the command runs without a filter
- **THEN** it reports how many families are recorded
- **AND** how many channels belong to one
