## Purpose

This capability owns where that data may live, how it is kept current, and how it is removed — including from places a copy could otherwise outlive the rule, such as backups and review files.

## ADDED Requirements

### Requirement: Refresh

The system SHALL provide `itgraph yt refresh`, fetching anew the payload of every channel whose payload is older than the refresh age or absent, whatever the channel's status.

#### Scenario: A stale payload is refreshed

- **GIVEN** a channel whose payload is older than the refresh age
- **WHEN** the command runs
- **THEN** its payload is fetched and replaced

#### Scenario: A fresh payload costs nothing

- **GIVEN** a channel whose payload is younger than the refresh age
- **WHEN** the command runs
- **THEN** no request is made for it

#### Scenario: Rejected channels are refreshed too

- **GIVEN** a rejected channel with a stale payload
- **WHEN** the command runs
- **THEN** its payload is refreshed like any other

#### Scenario: Channels share requests

- **GIVEN** a hundred channels due for refresh
- **WHEN** the command runs
- **THEN** two requests are made

#### Scenario: Exhaustion stops a refresh part way

- **WHEN** the quota is exhausted during a refresh
- **THEN** the payloads refreshed before it are kept
- **AND** the next run continues with those still due

### Requirement: Unattended Refresh

The system SHALL run the refresh daily on the collection host without an operator, whenever an API key is configured there.

#### Scenario: A host with a key refreshes daily

- **GIVEN** a collection host deployed with an API key
- **THEN** the refresh runs there once a day
- **AND** a run missed while the host was off runs when it is back

#### Scenario: A host without a key schedules nothing

- **GIVEN** a collection host deployed without an API key
- **THEN** no refresh is scheduled there

### Requirement: Channels That Disappear

The system SHALL treat a channel YouTube no longer returns when asked for by id as gone: its payload SHALL be removed and the time recorded, and its record and review SHALL remain. A gone channel that YouTube returns again SHALL be restored.

#### Scenario: A channel YouTube no longer returns

- **GIVEN** a channel whose id YouTube answers with no channel
- **WHEN** a refresh asks for it
- **THEN** its payload is removed
- **AND** it is marked gone, with the time
- **AND** its record and review remain

#### Scenario: One missing channel does not cost the others their refresh

- **GIVEN** a request for several channel ids, one of which YouTube no longer returns
- **WHEN** a refresh makes it
- **THEN** every other channel in the request is refreshed
- **AND** only the missing one is marked gone

#### Scenario: A gone channel comes back

- **GIVEN** a channel marked gone
- **WHEN** a later refresh finds YouTube returning it again
- **THEN** its payload is stored and the gone mark is cleared
