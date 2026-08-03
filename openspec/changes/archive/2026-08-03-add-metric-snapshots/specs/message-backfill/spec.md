## MODIFIED Requirements

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
