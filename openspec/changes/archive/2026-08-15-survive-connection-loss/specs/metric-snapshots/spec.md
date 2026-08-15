## ADDED Requirements

### Requirement: The Loop Survives A Lost Connection

The system SHALL keep the watch loop able to recover from a connection that has been lost or has stopped answering, without operator action. The loop SHALL confirm it is connected before each poll and MUST NOT poll over a connection the client has given up on. A connection that is lost MUST NOT be recorded as a failure of the channels that were due while it was down. A request that passes its deadline SHALL cause the connection to be discarded rather than reused. Where the loop makes no progress at all while channels are due, it SHALL stop with a non-zero exit rather than continue to appear healthy.

#### Scenario: A poll is not issued over a connection the client gave up on

- **GIVEN** a client whose connection attempts have been abandoned
- **WHEN** the loop reaches a due channel
- **THEN** no history request is issued
- **AND** the loop attempts to re-establish the connection instead

#### Scenario: The connection is re-established and polling resumes

- **GIVEN** a loop that has lost its connection
- **WHEN** the connection can be established again
- **THEN** the loop resumes polling due channels
- **AND** it holds the same session lease it held before

#### Scenario: Channels due during an outage are not recorded as failures

- **GIVEN** channels due while the connection is down
- **WHEN** the loop cannot connect
- **THEN** no failure is recorded against any of them
- **AND** their next-due moments are not pushed out by a failure backoff

#### Scenario: A connection lost mid-batch stops the batch

- **GIVEN** a batch of due channels being polled
- **WHEN** the connection is lost partway through it
- **THEN** the remaining channels in the batch are not polled
- **AND** no request is issued until the connection is re-established

#### Scenario: A request that passes its deadline discards the connection

- **GIVEN** a poll whose request passes the configured deadline
- **WHEN** the loop handles it
- **THEN** the connection is discarded
- **AND** the next poll is issued over a newly established connection

#### Scenario: A poll that timed out does not stop the loop

- **GIVEN** a poll abandoned on its deadline
- **WHEN** the loop continues
- **THEN** the remaining due channels are polled once the connection is re-established
- **AND** the loop is still running

#### Scenario: A loop that makes no progress exits

- **GIVEN** channels that are due and a loop that completes no poll — neither stored, skipped nor failed — for the configured stall period
- **WHEN** the period passes
- **THEN** the loop stops with a message saying it made no progress
- **AND** the process exits non-zero, so that a supervisor restarts it

#### Scenario: An idle loop is not mistaken for a stalled one

- **GIVEN** a loop with no channel due, or inside quiet hours, or whose schedule is postponed by a rate limit
- **WHEN** the stall period passes without a poll
- **THEN** the loop keeps running
- **AND** nothing is reported as stalled
