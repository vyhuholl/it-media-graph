# youtube-api-access Specification

## Purpose
Every request to the YouTube Data API is authenticated by one key and paid for from one daily quota. This capability owns both: how a request is made, and how the quota is booked, shared between commands, and stopped when it runs out.

## Requirements

### Requirement: API Key Authentication

The system SHALL authenticate every YouTube Data API request with the configured API key, sent in a request header. The key MUST NOT appear in a request URL, a log record, or an error message.

#### Scenario: The key travels in a header

- **WHEN** a YouTube command makes a request
- **THEN** the request carries the key in the `x-goog-api-key` header
- **AND** the request URL does not contain the key

#### Scenario: The key never reaches the log

- **GIVEN** logging at debug level
- **WHEN** a request succeeds, fails, or is retried
- **THEN** no log record contains the key

#### Scenario: A missing key stops YouTube commands before any request

- **GIVEN** no API key configured, or an empty one
- **WHEN** a YouTube command that makes requests runs
- **THEN** it exits with a non-zero status before making any request
- **AND** the error names the setting to fill in

#### Scenario: A missing key does not affect anything else

- **GIVEN** no API key configured
- **WHEN** a Telegram command, the alert bot, or a YouTube command that makes no request runs
- **THEN** it runs as it would with the key present

### Requirement: Outbound Route

The system SHALL connect to the YouTube Data API directly. The MTProto proxy configured for Telegram MUST NOT be used for YouTube requests, and its absence or failure MUST NOT affect them.

#### Scenario: The MTProto proxy is not used

- **GIVEN** a configured MTProto proxy
- **WHEN** a YouTube command makes a request
- **THEN** the request does not go through that proxy

### Requirement: Quota Day

The system SHALL account quota per quota day: the calendar date in the `America/Los_Angeles` time zone, which is when YouTube resets the daily quota. A quota day SHALL be 23 or 25 hours long when Pacific time changes to or from daylight saving.

#### Scenario: Spending is counted against the Pacific date

- **WHEN** units are booked at 06:59 UTC on a day in summer
- **THEN** they count towards the previous Pacific date
- **AND** units booked at 07:00 UTC count towards the next one

#### Scenario: The day that leaves daylight saving is 25 hours long

- **WHEN** units are booked at 07:30 UTC on 2 November 2026
- **THEN** they count towards 1 November 2026
- **AND** the reset after that day is reported as 08:00 UTC on 2 November 2026

### Requirement: Units Are Booked Before a Request

The system SHALL book a request's quota cost before sending it, and MUST NOT send a request whose units cannot be booked. Booked units SHALL never be returned, whatever the request's outcome.

#### Scenario: A request that fits is booked, then sent

- **GIVEN** a pool with units remaining for the day
- **WHEN** a command makes a one-unit request
- **THEN** the pool's spending for the day rises by one before the request is sent

#### Scenario: A request that does not fit is not sent

- **GIVEN** a pool whose remaining units are fewer than a request costs
- **WHEN** a command attempts that request
- **THEN** no request is sent
- **AND** the command stops, reporting that the pool is spent and when the next quota day begins

#### Scenario: A failed request still costs its units

- **WHEN** a booked request is answered with an error, is not found, or times out
- **THEN** its units remain counted

### Requirement: Spending Caps

The system SHALL cap each pool's daily spending. The general pool's cap SHALL be the daily quota less a safety margin and less every share reserved for another pool. The defaults SHALL be a daily quota of 10,000 units and a margin of 10%.

#### Scenario: The general cap leaves the margin and the reserve unspent

- **GIVEN** a daily quota of 10,000 units, a 10% margin and 3,000 units reserved for the poll loop
- **WHEN** the commands of this capability spend for a day
- **THEN** together they spend at most 6,000 units that day

#### Scenario: Caps that leave nothing to spend are refused

- **GIVEN** a margin and reserves that together reach the daily quota
- **WHEN** settings are loaded
- **THEN** loading fails with an error naming the settings involved

### Requirement: Reserved Share

The system SHALL hold a configurable share of the daily quota for the YouTube poll loop. No command other than the poll loop MAY spend from it, including when the poll loop is not running or does not yet exist.

#### Scenario: Other commands cannot reach the reserve

- **GIVEN** a reserved share for the poll loop
- **AND** a general pool that is spent for the day
- **WHEN** an import or a refresh attempts a request
- **THEN** no request is sent, although the reserve is untouched

### Requirement: Concurrent Spending

The system SHALL keep each pool within its cap when several processes book units at the same moment.

#### Scenario: Two processes race for the last units

- **GIVEN** a pool with one unit remaining
- **WHEN** two processes each book one unit at the same time
- **THEN** exactly one booking succeeds
- **AND** only that process sends its request

### Requirement: Quota Exhaustion

The system SHALL treat a `quotaExceeded` response as the end of the quota day for every YouTube command. It MUST NOT retry the request, and every YouTube command that makes requests SHALL refuse to start until the next quota day begins.

#### Scenario: Exhaustion stops the running command

- **WHEN** a request is answered with `quotaExceeded`
- **THEN** the command stops without retrying
- **AND** what it completed before the answer is kept and reported
- **AND** the exhaustion is recorded against the quota day

#### Scenario: Exhaustion stops every later command until the reset

- **GIVEN** exhaustion recorded for the current quota day
- **WHEN** any YouTube command that makes requests starts
- **THEN** it exits without making a request
- **AND** it reports the moment of the next reset in UTC and in local time

#### Scenario: The next quota day starts clean

- **GIVEN** exhaustion recorded for the previous quota day
- **WHEN** a YouTube command starts after the reset
- **THEN** it proceeds

#### Scenario: Exhaustion is not mistaken for a pool's own cap

- **GIVEN** a general pool with units remaining by its own count
- **WHEN** YouTube answers `quotaExceeded`
- **THEN** the exhaustion stops every pool, not only the general one

### Requirement: Transient Failures

The system SHALL retry a request answered with a server error or a rate-limit response a bounded number of times, waiting longer before each attempt, and SHALL book units for every attempt. When the attempts are used up the command SHALL stop, keeping and reporting what it completed.

#### Scenario: A server error is retried

- **WHEN** a request is answered with a 5xx status and the next attempt succeeds
- **THEN** the command continues
- **AND** both attempts are counted against the quota

#### Scenario: Persistent failure stops the command

- **WHEN** every attempt at a request fails with a 5xx status
- **THEN** the command exits with a non-zero status
- **AND** what it completed before is kept

#### Scenario: A client error is not retried

- **WHEN** a request is answered with a 4xx status other than not-found or a rate limit
- **THEN** it is not retried
- **AND** the command exits with a non-zero status, reporting the status and the reason YouTube gave

### Requirement: Request Journal

The system SHALL record every booked request with the command that made it, the API method, its units, the quota day and pool, and its outcome. A request whose outcome was never learned — the process died or the connection broke — SHALL be recorded as of unknown outcome, not as a success or a failure.

#### Scenario: A request is attributed to its command

- **WHEN** an import makes a request
- **THEN** the journal records it against the import command, with the method and its units

#### Scenario: A killed process leaves an honest record

- **GIVEN** a process killed after booking a request and before its response arrived
- **WHEN** the journal is read
- **THEN** that request appears with its units and an unknown outcome

### Requirement: Quota Report

The system SHALL provide `itgraph yt quota`, reporting the current quota day's spending per pool against its cap, whether the day is exhausted, the moment of the next reset in UTC and in local time, and the day's spending by command and method. It makes no request.

#### Scenario: Today's position is reported

- **WHEN** the command runs
- **THEN** it reports units spent and remaining per pool
- **AND** the next reset in UTC and in local time
- **AND** units spent per command and method for the day

#### Scenario: Reporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made
