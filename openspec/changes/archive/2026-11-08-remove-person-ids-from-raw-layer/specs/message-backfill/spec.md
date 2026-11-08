# Spec Delta

## MODIFIED Requirements

### Requirement: Raw Message Storage

The system SHALL store every fetched message as its payload as received, keyed by channel and message id, but for what names a person, which is removed before the payload is written (personal-data, "What Names A Person"). It MUST NOT derive anything from it while collecting.

#### Scenario: A message is stored verbatim
- **WHEN** a message is fetched
- **THEN** its complete payload is stored as received, but for what names a person
- **AND** the channel id, message id and a fetch timestamp are stored alongside it

#### Scenario: Re-fetching does not duplicate
- **GIVEN** a message already stored
- **WHEN** the same message is fetched again
- **THEN** no second row is created
- **AND** the stored payload is left unchanged

#### Scenario: Nothing is derived during collection
- **WHEN** a backfill run completes
- **THEN** no forward edges, mentions, external links or language labels have been written by it

#### Scenario: A message naming people is stored without them
- **GIVEN** a fetched message whose latest commenters and paid reactors include users
- **WHEN** it is stored
- **THEN** the stored payload names none of them
- **AND** every other field is stored as received

### Requirement: Channel Metadata Pass

The system SHALL fetch extended information for an in-scope channel when what it holds is absent or stale, store the payload as received but for what names a person (personal-data, "What Names A Person"), and use it to resolve discussion chats. This pass SHALL be independent of the history walk: it is requested on its own, bounded on its own, and a rate limit that stops it MUST NOT stop a history walk. A history walk MUST NOT trigger it.

#### Scenario: The history walk never fetches extended information

- **GIVEN** an in-scope channel whose stored extended information is absent or stale
- **WHEN** backfill walks it
- **THEN** no extended-information request is made
- **AND** the stored extended information is left as it was

#### Scenario: A recent payload is not re-fetched

- **GIVEN** a channel whose extended information was stored within the configured freshness window
- **WHEN** the metadata pass runs
- **THEN** no extended-information request is made for it

#### Scenario: A stale payload is refreshed

- **GIVEN** a channel whose stored extended information is older than the freshness window
- **WHEN** the metadata pass runs
- **THEN** the extended information is fetched again
- **AND** the stored payload is replaced with the newer one

#### Scenario: A channel never seen before is fetched

- **GIVEN** an in-scope channel with no stored extended information
- **WHEN** the metadata pass runs
- **THEN** the extended information is fetched and stored

#### Scenario: A refresh can be demanded

- **WHEN** the operator asks the metadata pass for a refresh
- **THEN** extended information is fetched for every channel the pass covers, regardless of freshness

#### Scenario: The pass is bounded

- **WHEN** a limit is supplied to the metadata pass
- **THEN** at most that many channels are fetched
- **AND** the remaining channels stay pending for a later run

#### Scenario: The pass spends no username resolution

- **WHEN** the metadata pass fetches extended information for a channel
- **THEN** the peer is obtained from the session's entity cache
- **AND** no username is resolved

#### Scenario: A halted metadata pass leaves history collectable

- **GIVEN** a metadata pass stopped by a rate limit
- **WHEN** backfill runs afterwards
- **THEN** it walks history for every in-scope channel
- **AND** the missing extended information does not prevent any walk

#### Scenario: Stale metadata is reported rather than fetched

- **GIVEN** in-scope channels whose extended information is absent or past the freshness window
- **WHEN** a backfill run reports what it did
- **THEN** it states how many channels are waiting on the metadata pass
- **AND** it made no request to establish that count

#### Scenario: Channel identity comes from the response

- **WHEN** extended information is fetched for a channel
- **THEN** the channel's id, title and username are read from the response itself
- **AND** no separate lookup is made to obtain them

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

#### Scenario: A channel's payload is stored without people

- **GIVEN** extended information listing a user who is not a bot
- **WHEN** it is stored
- **THEN** the stored payload does not list that user, and lists the channel's bots as received
