## MODIFIED Requirements

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
