## MODIFIED Requirements

### Requirement: Channel Record

The system SHALL store one record per Telegram channel or chat, keyed by its Telegram id.

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

### Requirement: Manual Review

The system SHALL provide `itgraph mark`, recording the review outcome for a single channel addressed by its Telegram id or by its username.

#### Scenario: Addressing a channel by username
- **WHEN** the channel is given as a username, with or without a leading `@`
- **THEN** the matching record is reviewed, matching case-insensitively

#### Scenario: A username held by two records
- **GIVEN** two records carry the same username, one of them stale
- **WHEN** that username is given
- **THEN** the command fails, naming both ids, and nothing is written

#### Scenario: Accepting a channel
- **WHEN** a channel is marked as in scope
- **THEN** its status becomes `seed`
- **AND** its kind is set to the given value, defaulting to `personal`
- **AND** its review timestamp is set

#### Scenario: Rejecting a channel
- **WHEN** a channel is rejected with a reason from the rejection enum
- **THEN** its status becomes `rejected` and the reason is stored
- **AND** an optional free-text note is stored alongside the reason
- **AND** its review timestamp is set

#### Scenario: Rejecting without a reason fails
- **WHEN** a channel is rejected and no reason is supplied
- **THEN** the command fails and nothing is written

#### Scenario: Deferring a decision
- **WHEN** a channel is marked as undecided
- **THEN** its status becomes `maybe` and its review timestamp is set

#### Scenario: Reviewing an unknown channel
- **WHEN** the given id or username is not in the inventory
- **THEN** the command fails and nothing is written

#### Scenario: Linked discussion chats are not reviewed independently
- **GIVEN** a chat whose parent channel is recorded
- **WHEN** the review queue is built
- **THEN** the chat is excluded from it
- **AND** no human decision about the chat is required
- **AND** this exemption governs review only: whether the chat's contents are collected is decided separately by each collecting capability

#### Scenario: Unresolved channels are not queued
- **GIVEN** a channel awaiting resolution and therefore having no username or title
- **WHEN** the review queue is built
- **THEN** it is excluded from the queue
- **AND** it enters the queue once resolved