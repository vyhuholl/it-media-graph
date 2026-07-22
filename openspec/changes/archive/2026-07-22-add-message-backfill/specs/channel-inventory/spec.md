## MODIFIED Requirements

### Requirement: Manual Review

The system SHALL provide `itgraph mark`, recording the review outcome for a single
channel addressed by its Telegram id or by its username.

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
- **AND** this exemption governs review only: whether the chat's contents are
  collected is decided separately by each collecting capability