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

#### Scenario: Standalone community chats are out of scope for now
- **GIVEN** a chat with status `seed` and no parent channel
- **WHEN** backfill runs
- **THEN** it is not walked
- **AND** it is reported as deferred rather than silently skipped

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

The system SHALL comply with Telegram's rate limits by waiting, MUST NOT attempt to circumvent them, and SHALL stop a run rather than sleep through a wait longer than it is willing to hold a connection open for. Every request the system makes SHALL be bounded by a deadline; a request that passes it SHALL be abandoned and reported as a failed request rather than waited on further. The deadline SHALL apply to the request alone and MUST NOT bound a rate-limit wait, and it MUST be configured above the longest wait the client sleeps off inside a request.

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

#### Scenario: A request that never answers is abandoned

- **GIVEN** a request that has been issued and neither answers nor fails
- **WHEN** the configured deadline passes
- **THEN** the request is abandoned
- **AND** the caller is told the request timed out, distinguishably from any other failure
- **AND** the pass does not wait on it further

#### Scenario: A rate-limit wait is not bounded by the request deadline

- **GIVEN** a rate limit short enough to sleep off but longer than the request deadline
- **WHEN** the collector waits it out
- **THEN** the wait completes in full
- **AND** the request is retried afterwards with a fresh deadline

#### Scenario: A deadline below the sleep-off threshold is refused

- **GIVEN** a request deadline configured no higher than the wait the client sleeps off inside a request
- **WHEN** settings are loaded
- **THEN** the configuration is rejected with a message naming both settings

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

### Requirement: Per-Channel Message Ceiling

The system SHALL bound how many messages a single channel may ever contribute to the corpus, so that a few high-volume aggregators cannot dominate it, and MUST treat a channel that reaches the ceiling as finished for good.

#### Scenario: The ceiling stops the walk
- **GIVEN** a channel with more history above the cutoff than the ceiling allows
- **WHEN** backfill walks it
- **THEN** the walk stops at the ceiling
- **AND** the depth recorded for the channel is the date of the oldest message actually stored, not the requested cutoff

#### Scenario: A capped channel is not reopened
- **GIVEN** a channel that already holds its ceiling of messages
- **WHEN** backfill runs again, including with an earlier cutoff
- **THEN** no request is made for that channel

#### Scenario: The ceiling spans runs
- **WHEN** the messages a channel holds are counted against the ceiling
- **THEN** rows collected by earlier runs count too

#### Scenario: The ceiling can be lifted deliberately
- **WHEN** the ceiling is set to zero
- **THEN** the walk is bounded only by the cutoff

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
- **THEN** counts of completed, capped, skipped and failed channels are reported

### Requirement: Channel Metadata Pass

The system SHALL fetch extended information for an in-scope channel when what it holds is absent or stale, store the payload verbatim, and use it to resolve discussion chats. This pass SHALL be independent of the history walk: it is requested on its own, bounded on its own, and a rate limit that stops it MUST NOT stop a history walk. A history walk MUST NOT trigger it.

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

### Requirement: Rate Limit Events Are Recorded

The system SHALL record every rate limit it observes, identifying the request method that caused it, and MUST NOT let a failure to record affect how the rate limit is handled. Every command that can issue a request SHALL be representable in the record and distinguishable from the others, so that a method appearing under a command that has no business issuing it is detectable.

#### Scenario: A slept-off wait is recorded

- **WHEN** a rate limit short enough to wait out is encountered
- **THEN** an event is recorded carrying the time, the request method, the duration, and the command that was running
- **AND** the event says the run was not halted
- **AND** the wait is still slept off and the request still retried

#### Scenario: A halting wait is recorded

- **WHEN** a rate limit long enough to stop the run is encountered
- **THEN** an event is recorded the same way
- **AND** the event says the run was halted
- **AND** the run still stops as it would have

#### Scenario: The method recorded is the one that was called

- **GIVEN** a rate limit whose request is wrapped in one or more of Telegram's invocation wrappers
- **WHEN** the event is recorded
- **THEN** the method stored is the innermost request, not the wrapper

#### Scenario: A rate limit naming no request is still recorded

- **GIVEN** a rate limit that carries no request
- **WHEN** the event is recorded
- **THEN** the method is stored as unknown
- **AND** the duration and the time are recorded as usual

#### Scenario: The channel being walked is recorded when there is one

- **WHEN** a rate limit is encountered while walking a channel
- **THEN** the event names that channel
- **AND** an event raised outside a channel walk names none

#### Scenario: Every command is told apart

- **WHEN** history collection, reference resolution, the metadata pass and the watch loop each encounter a rate limit
- **THEN** each event names the command it came from
- **AND** all of them are distinguishable even where the request method is the same

#### Scenario: A quota-bearing method under the wrong command is visible

- **GIVEN** a command that is required to spend no quota-bearing request
- **WHEN** a rate limit naming such a method is recorded against it
- **THEN** the record attributes the method to that command rather than to the command that is allowed to spend it

#### Scenario: Recording cannot break collection

- **GIVEN** recording an event fails for any reason
- **WHEN** a rate limit is encountered
- **THEN** the failure is logged and otherwise ignored
- **AND** the rate limit is handled exactly as it would have been

#### Scenario: Recording does not disturb work in progress

- **GIVEN** a rate limit arriving part-way through a channel's walk
- **WHEN** the event is recorded
- **THEN** history already committed is retained
- **AND** no partial batch is committed by the recording

### Requirement: Recorded Rate Limits Can Be Read Back

The system SHALL let the operator read the recorded events, both individually and summarized by method, and MUST NOT present a recorded event as proof that a request was sent.

#### Scenario: Recent events are listed

- **WHEN** the operator asks for recorded rate limits
- **THEN** events are listed newest first with their time, method, duration, command, channel and whether they halted a run

#### Scenario: Events are summarized by method

- **WHEN** the operator asks for a summary over a window
- **THEN** each method is reported with how many times it was limited and its longest wait

#### Scenario: The limits of the record are stated

- **WHEN** events are presented
- **THEN** the output states that an event does not establish that a request reached Telegram

#### Scenario: An empty record says so

- **GIVEN** no rate limit has been recorded
- **WHEN** the operator asks for them
- **THEN** the output says the record is empty

### Requirement: Quota-Free History Walk

The system SHALL walk a channel's history without issuing any request that carries a per-day quota. In particular a walk MUST NOT resolve a username: `contacts.resolveUsername` SHALL be issued by the resolution pass alone, and by no other command.

#### Scenario: The peer comes from the session's own cache

- **GIVEN** an in-scope channel whose peer the session can supply
- **WHEN** backfill walks it
- **THEN** the peer is obtained from the session's entity cache
- **AND** no username is resolved

#### Scenario: A walk resolves no username, whatever the metadata state

- **GIVEN** an in-scope channel with no stored extended information
- **WHEN** backfill walks it
- **THEN** no username resolution is requested
- **AND** no extended-information request is made
- **AND** its history is fetched

#### Scenario: A channel with no cached peer is skipped rather than resolved

- **GIVEN** an in-scope channel whose peer the session cannot supply
- **WHEN** backfill walks it
- **THEN** no username resolution is requested
- **AND** the channel is recorded as skipped, with the reason
- **AND** the run continues with the remaining channels

#### Scenario: Resolution stays confined to its own command

- **WHEN** a backfill run completes, whatever it skipped or failed on
- **THEN** it has issued no username resolution

