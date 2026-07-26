## ADDED Requirements

### Requirement: Randomized Request Pacing

The system SHALL draw the gap before each request anew rather than using a fixed interval, and SHALL take a longer pause between one channel and the next.

#### Scenario: Gaps vary around the configured delay

- **WHEN** requests are made in sequence
- **THEN** the gap before each one is drawn anew
- **AND** it falls within a band around the configured delay
- **AND** it is never negative, whatever delay is configured

#### Scenario: A run occasionally pauses for much longer

- **WHEN** many requests are made in sequence
- **THEN** a small, configurable fraction of the gaps are drawn from a much longer range instead
- **AND** such a pause replaces the ordinary gap rather than being added to it

#### Scenario: Pacing can be switched off

- **GIVEN** a configured delay of zero
- **WHEN** requests are made
- **THEN** no gap is taken — neither a jittered one nor a long one

#### Scenario: Gaps cannot be made reproducible

- **WHEN** a gap is chosen
- **THEN** it is drawn from a source that no global seed can make predictable

#### Scenario: Every request is preceded by a gap

- **WHEN** any request is made to Telegram during a run, whether for metadata or for history
- **THEN** a gap precedes it

#### Scenario: A longer pause separates channels

- **GIVEN** a run that walks more than one channel
- **WHEN** it finishes one channel and moves to the next
- **THEN** a pause substantially longer than a request gap is taken before that channel's first request
- **AND** the pause is drawn anew for each transition

#### Scenario: The first channel is not delayed

- **WHEN** a run begins
- **THEN** no inter-channel pause precedes the first channel it walks

#### Scenario: A channel that makes no request costs no pause

- **GIVEN** a channel skipped because it is already complete, at its ceiling, or has no username
- **WHEN** the run reaches it
- **THEN** no inter-channel pause is taken for it

## MODIFIED Requirements

### Requirement: Rate Limit Compliance

The system SHALL comply with Telegram's rate limits by waiting, MUST NOT attempt to circumvent them, and SHALL stop a run rather than sleep through a wait longer than it is willing to hold a connection open for.

#### Scenario: A short FloodWait is waited out

- **WHEN** Telegram returns a FloodWait no longer than the configured halt threshold
- **THEN** the collector sleeps for the requested duration and retries
- **AND** the wait and its duration are logged

#### Scenario: A long FloodWait halts the run

- **WHEN** Telegram returns a FloodWait longer than the configured halt threshold
- **THEN** the run stops instead of sleeping through it
- **AND** no further request is made
- **AND** the operator is told how long the wait was and when work may resume
- **AND** the work already committed is reported

#### Scenario: A halt is not mistaken for a channel failure

- **GIVEN** a run halted by a long FloodWait while walking a channel
- **WHEN** the halt propagates
- **THEN** it is not absorbed by the per-channel failure handler
- **AND** that channel is not recorded as having failed
- **AND** the run does not continue to the next channel

#### Scenario: A halted run resumes like an interrupted one

- **GIVEN** a run halted by a long FloodWait
- **WHEN** backfill runs again
- **THEN** it continues from the progress committed before the halt

#### Scenario: Limits are never circumvented

- **WHEN** a rate limit is encountered
- **THEN** no alternative session, account or connection is used to continue

#### Scenario: Requests are paced

- **WHEN** history is fetched
- **THEN** channels are processed one at a time
- **AND** a configurable delay separates consecutive requests

### Requirement: Channel Metadata Pass

The system SHALL fetch extended information for an in-scope channel when what it holds is absent or stale, store the payload verbatim, and use it to resolve discussion chats.

#### Scenario: A recent payload is not re-fetched

- **GIVEN** a channel whose extended information was stored within the configured freshness window
- **WHEN** backfill walks it again
- **THEN** no extended-information request is made
- **AND** the peer the history walk needs is obtained from the session's own cache

#### Scenario: A stale payload is refreshed

- **GIVEN** a channel whose stored extended information is older than the freshness window
- **WHEN** backfill walks it
- **THEN** the extended information is fetched again
- **AND** the stored payload is replaced with the newer one

#### Scenario: A channel never seen before is fetched

- **GIVEN** an in-scope channel with no stored extended information
- **WHEN** backfill walks it
- **THEN** the extended information is fetched and stored

#### Scenario: Skipping falls back rather than failing

- **GIVEN** a channel with a recent payload whose peer the session cannot supply
- **WHEN** backfill walks it
- **THEN** the full metadata pass runs instead
- **AND** the walk proceeds

#### Scenario: A refresh can be demanded

- **WHEN** the operator asks for a metadata refresh
- **THEN** extended information is fetched for every channel the run walks, regardless of freshness

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
