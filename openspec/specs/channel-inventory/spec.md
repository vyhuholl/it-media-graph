# channel-inventory Specification

## Purpose
TBD - created by archiving change add-channel-inventory. Update Purpose after archive.
## Requirements
### Requirement: Telegram Session Authentication

The system SHALL connect to Telegram using an existing Telethon session file and MUST NOT attempt an interactive login.

#### Scenario: Authorized session present
- **GIVEN** a session file at the configured path belonging to an authorized account
- **WHEN** a command requiring Telegram access runs
- **THEN** the client connects and the command proceeds

#### Scenario: Session missing or unauthorized
- **GIVEN** no session file, or one whose account is not authorized
- **WHEN** a command requiring Telegram access runs
- **THEN** the command exits with a non-zero status
- **AND** the error points to the bootstrap instructions in the README
- **AND** no prompt for a phone number, code or password is shown

### Requirement: Channel Record

The system SHALL store one record per Telegram channel or chat, keyed by its Telegram id.

#### Scenario: Identity is recorded
- **WHEN** a channel record is created
- **THEN** the Telegram id is its primary key
- **AND** username, title, and whether the entity is a chat are stored
- **AND** the discovery source and a first-seen timestamp are recorded

#### Scenario: A new record is unreviewed
- **WHEN** a channel record is created
- **THEN** its status is `candidate`
- **AND** kind, rejection reason and review timestamp are empty

#### Scenario: Rejection cannot be reasonless
- **WHEN** a record is written with status `rejected` and no rejection reason
- **THEN** the write is refused at the database level

### Requirement: Subscription Import

The system SHALL provide `itgraph dump-dialogs`, importing every channel and chat
the authorized account is subscribed to.

#### Scenario: First run populates the inventory
- **GIVEN** an empty inventory
- **WHEN** the command runs
- **THEN** every broadcast channel and group in the account's dialog list is inserted
- **AND** each record has discovery source `own_subscriptions` and status `candidate`
- **AND** the number of inserted records is reported

#### Scenario: Re-running preserves review work
- **GIVEN** an inventory in which some channels have already been reviewed
- **WHEN** the command runs again
- **THEN** username and title are refreshed from Telegram
- **AND** status, kind, rejection reason and review timestamp are left unchanged
- **AND** the discovery source of existing records is left unchanged

#### Scenario: Unsubscribing does not remove a record
- **GIVEN** a channel in the inventory that is no longer in the dialog list
- **WHEN** the command runs
- **THEN** the record is retained unchanged

### Requirement: Manual Review

The system SHALL provide `itgraph mark`, recording the review outcome for a single
channel.

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
- **WHEN** the given id is not in the inventory
- **THEN** the command fails and nothing is written

### Requirement: Records Are Never Deleted

The system SHALL retain every discovered channel, including rejected ones, and MUST NOT delete channel records.

#### Scenario: Rejected channels remain queryable
- **WHEN** the inventory is queried without a status filter
- **THEN** rejected channels are included in the result

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

