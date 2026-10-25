## Purpose

What is read from the YouTube Data API with an API key may be kept at most 30 days (YouTube API Services Developer Policies III.E.4.b, III.E.4.d). This capability owns where that data may live, how it is kept current, and how it is removed — including from places a copy could otherwise outlive the rule, such as backups and review files.

## ADDED Requirements

### Requirement: API Data Has One Home

The system SHALL store channel data read from the API only as the channel's latest payload, one per channel. A title, handle, description or statistic MUST NOT be copied into any other table, column, log record or backup, so that removing the payload removes the data.

#### Scenario: Removing the payload removes the data

- **GIVEN** a channel whose payload is removed
- **WHEN** the database is searched for that channel's title or handle
- **THEN** neither is found

#### Scenario: A newer payload replaces the old one

- **WHEN** a channel's payload is fetched again
- **THEN** the stored payload is replaced, not added to
- **AND** its fetch time becomes the time of the new fetch

### Requirement: Refresh

The system SHALL provide `itgraph yt refresh`, fetching anew the payload of every channel whose payload is older than the refresh age or absent, whatever the channel's status. The refresh age SHALL be configurable, default to 25 days, and MUST be under 30 days.

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

#### Scenario: A refresh age of thirty days or more is refused

- **GIVEN** a configured refresh age of 30 days or more
- **WHEN** settings are loaded
- **THEN** loading fails with an error naming the setting and the policy limit

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

### Requirement: Expiry

The system SHALL remove every channel payload fetched more than 30 days ago. The removal SHALL happen in every YouTube command that reaches the database, whether or not it makes requests, before the command reads or writes anything else there. The channel record SHALL remain.

#### Scenario: An expired payload is removed before any work

- **GIVEN** a channel whose payload was fetched 31 days ago
- **WHEN** any YouTube command reaches the database
- **THEN** the payload is removed before the command reads or writes anything else there
- **AND** the channel record, with its review, remains

#### Scenario: A command that refuses its input touches nothing

- **GIVEN** a command whose arguments or input are refused before it reaches the database, or one asked only for its help
- **WHEN** it exits
- **THEN** it has neither read nor written the database, expired payloads included
- **AND** the next command that reaches the database removes them

#### Scenario: Expiry needs no network

- **GIVEN** no API key configured and no network
- **WHEN** a YouTube command that makes no request starts
- **THEN** expired payloads are still removed

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

### Requirement: Backups Carry No API Data

Database dumps SHALL NOT contain YouTube API data. The daily inventory dump SHALL contain the YouTube channel records; restoring any dump SHALL leave payloads empty until the next refresh.

#### Scenario: A full dump leaves the payloads out

- **WHEN** a full dump is taken, including the one taken before a migration
- **THEN** it holds the definition of the payload table and none of its rows

#### Scenario: The inventory dump keeps the review

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every YouTube channel record with its review

#### Scenario: A restore is completed by a refresh

- **GIVEN** a database restored from a dump
- **WHEN** a refresh runs
- **THEN** every channel's payload is fetched again

### Requirement: Review Files Expire

A review file holds API data and SHALL state its own expiry: 30 days after its export moment. The system SHALL refuse to apply a review file after that moment.

#### Scenario: The file says when it must be gone

- **WHEN** a review file is exported
- **THEN** its header states the date after which it may not be kept or applied

#### Scenario: An expired file is refused

- **GIVEN** a review file exported more than 30 days ago
- **WHEN** it is applied
- **THEN** the command fails without writing
- **AND** says to export a new file and delete the old one
