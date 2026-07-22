# message-backfill Specification

## Purpose
TBD - created by archiving change add-message-backfill. Update Purpose after archive.
## Requirements
### Requirement: Collection Scope

The system SHALL fetch history only from channels that were reviewed and accepted, and MUST NOT fetch from any other entity.

#### Scenario: Only accepted channels are walked
- **GIVEN** an inventory holding candidate, maybe, rejected and accepted channels
- **WHEN** a backfill run starts
- **THEN** only channels with status `seed` are selected
- **AND** discussion chats are excluded regardless of their parent's status

#### Scenario: Entities without a public username are refused
- **GIVEN** a selected channel that has no username
- **WHEN** the collector is asked for its history
- **THEN** the fetch is refused and the channel is recorded as skipped
- **AND** the run continues with the remaining channels

#### Scenario: Media is never downloaded
- **WHEN** a message carrying a photo, video, document or voice note is stored
- **THEN** only the metadata present in the payload is retained
- **AND** no file is downloaded

### Requirement: Raw Message Storage

The system SHALL store every fetched message as a verbatim payload keyed by channel and message id, and MUST NOT derive anything from it while collecting.

#### Scenario: A message is stored verbatim
- **WHEN** a message is fetched
- **THEN** its complete payload is stored as received
- **AND** the channel id, message id and a fetch timestamp are stored alongside it

#### Scenario: Re-fetching does not duplicate
- **GIVEN** a message already stored
- **WHEN** the same message is fetched again
- **THEN** no second row is created
- **AND** the stored payload is left unchanged

#### Scenario: Nothing is derived during collection
- **WHEN** a backfill run completes
- **THEN** no forward edges, mentions, external links or language labels have been written by it

### Requirement: Resumable Progress

The system SHALL record per-channel progress so an interrupted run resumes rather than restarts.

#### Scenario: Interrupted run resumes
- **GIVEN** a channel whose history was partially fetched
- **WHEN** backfill runs again
- **THEN** it continues from the oldest message already retrieved
- **AND** messages already stored are not requested again

#### Scenario: Completed channel is skipped
- **GIVEN** a channel already fetched back to the configured cutoff
- **WHEN** backfill runs again with the same cutoff
- **THEN** that channel is not walked again

#### Scenario: Progress survives process death
- **WHEN** the process is killed part-way through a channel
- **THEN** the progress recorded before the interruption is retained

### Requirement: Rate Limit Compliance

The system SHALL comply with Telegram's rate limits by waiting, and MUST NOT attempt to circumvent them.

#### Scenario: FloodWait is waited out
- **WHEN** Telegram returns a FloodWait
- **THEN** the collector sleeps for the requested duration and retries
- **AND** the wait and its duration are logged

#### Scenario: Limits are never circumvented
- **WHEN** a rate limit is encountered
- **THEN** no alternative session, account or connection is used to continue

#### Scenario: Requests are paced
- **WHEN** history is fetched
- **THEN** channels are processed one at a time
- **AND** a configurable delay separates consecutive requests

### Requirement: Bounded Runs

The system SHALL let the operator bound a run by history depth and by number of channels.

#### Scenario: Depth cutoff
- **WHEN** a cutoff date is supplied
- **THEN** messages published before it are not requested

#### Scenario: Cautious first run
- **WHEN** a channel limit is supplied
- **THEN** at most that many channels are processed
- **AND** the remaining channels stay pending for a later run

#### Scenario: Conservative defaults
- **WHEN** no pacing options are supplied
- **THEN** the slowest configured defaults apply

### Requirement: Failure Isolation

The system SHALL record per-channel failures and continue the run.

#### Scenario: Inaccessible channel
- **GIVEN** a channel the collection account cannot reach — private, deleted, or restricted
- **WHEN** backfill reaches it
- **THEN** the failure and its reason are recorded against that channel
- **AND** the run continues with the remaining channels
- **AND** the inventory record is retained

#### Scenario: Run summary
- **WHEN** a run finishes
- **THEN** counts of completed, skipped and failed channels are reported

### Requirement: Channel Metadata Pass

The system SHALL fetch extended information once per in-scope channel, store the payload verbatim, and use it to resolve discussion chats.

#### Scenario: Linked chat is resolved
- **GIVEN** an in-scope channel with a linked discussion chat
- **WHEN** its extended information is fetched
- **THEN** the chat is present in the inventory with discovery source `linked_chat`
- **AND** the chat's `linked_to` points at that channel
- **AND** the chat's review fields are left empty

#### Scenario: An already-known chat is linked, not duplicated
- **GIVEN** a discussion chat already imported from the operator's subscriptions
- **WHEN** its parent channel's extended information is fetched
- **THEN** the existing row is updated with `linked_to`
- **AND** its discovery source and first-seen timestamp are unchanged

#### Scenario: Newest post date is recorded
- **WHEN** a channel's history is fetched
- **THEN** the publication date of its newest message is stored on the channel

