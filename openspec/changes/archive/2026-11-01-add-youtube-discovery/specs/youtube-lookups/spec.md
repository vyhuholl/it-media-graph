## ADDED Requirements

### Requirement: Mention Lookup Command

The system SHALL provide `itgraph yt mention-lookup`, finding the channel behind every handle, legacy name and video that seeds' videos reference and that nothing stored answers yet. A channel id needs no lookup, and nothing a featured list names is looked up.

#### Scenario: Names from descriptions are looked up

- **GIVEN** seeds' videos referencing an unknown video, an unknown handle and an unknown legacy name
- **WHEN** the command runs
- **THEN** each is looked up, and each answer is stored

#### Scenario: A name already looked up for Telegram

- **GIVEN** a handle looked up for a Telegram link 5 days ago
- **AND** a seed's video referencing the same handle
- **WHEN** the command runs
- **THEN** no request is made for it

#### Scenario: Nothing to look up

- **GIVEN** every name the seeds' videos reference already answered
- **WHEN** the command runs
- **THEN** no request is made
- **AND** it reports that nothing awaits a lookup

## MODIFIED Requirements

### Requirement: Lookup Spending

The system SHALL book lookups against the general pool, leave no floor in it, and stop after the number of requests `--limit` gives. Names SHALL be looked up most evidence first — for `tg-lookup` by the number of distinct seed families linking them, for `mention-lookup` by the number of distinct seeds referencing them — then by kind and name. Each answer SHALL be committed before the next request.

#### Scenario: The best-evidenced names first

- **GIVEN** an unknown handle linked by three families and another linked by one
- **WHEN** the command runs with a limit of one request
- **THEN** the handle linked by three families is looked up
- **AND** the other is not

#### Scenario: The most-referenced names first

- **GIVEN** an unknown handle referenced by two seeds and another referenced by one
- **WHEN** `itgraph yt mention-lookup` runs with a limit of one request
- **THEN** the handle referenced by two seeds is looked up
- **AND** the other is not

#### Scenario: A stopped run resumes

- **GIVEN** a run that stopped at its limit part way through
- **WHEN** the command runs again
- **THEN** the names already answered cost no request

#### Scenario: Spent quota keeps what was done

- **WHEN** the general pool is spent, or YouTube answers `quotaExceeded`, during a run
- **THEN** the run stops
- **AND** the answers stored before it remain

#### Scenario: No floor is left

- **GIVEN** a general pool with fewer units left than the history walk's floor
- **WHEN** the command runs
- **THEN** it spends what is left

### Requirement: Lookup Report

Each lookup command SHALL report how many names it looked up by kind, how many were found and not found, how many requests it made, and how many names still await a lookup. It SHALL name no handle and no legacy name.

#### Scenario: What a run says

- **WHEN** a run ends
- **THEN** it reports videos, handles and legacy names looked up, found and not found, and requests made
- **AND** how many names still await a lookup

#### Scenario: Both commands report alike

- **WHEN** `itgraph yt mention-lookup` ends
- **THEN** its report has the same lines as `itgraph yt tg-lookup`'s
