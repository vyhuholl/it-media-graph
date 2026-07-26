## ADDED Requirements

### Requirement: Rate Limit Events Are Recorded

The system SHALL record every rate limit it observes, identifying the request method that caused it, and MUST NOT let a failure to record affect how the rate limit is handled.

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

#### Scenario: Both commands are told apart

- **WHEN** history collection and reference resolution each encounter a rate limit
- **THEN** each event names the command it came from
- **AND** the two are distinguishable even where the request method is the same

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
