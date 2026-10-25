# Spec Delta

## MODIFIED Requirements

### Requirement: API Data Has One Home

The system SHALL store data read from the API in one home only: a channel's as its latest payload, a video's as its latest payload, and a reading's counters as that reading. A title, handle, description or statistic MUST NOT be copied into any other table, column, log record or backup, so that removing the home removes the data. What the system works out from a video's data SHALL be stored with that video's payload and removed with it.

#### Scenario: Removing the payload removes the data

- **GIVEN** a channel whose payload is removed
- **WHEN** the database is searched for that channel's title or handle
- **THEN** neither is found

#### Scenario: A newer payload replaces the old one

- **WHEN** a channel's payload is fetched again
- **THEN** the stored payload is replaced, not added to
- **AND** its fetch time becomes the time of the new fetch

#### Scenario: Removing a video removes everything about it

- **GIVEN** a tracked video with readings
- **WHEN** its payload is removed
- **THEN** its title, its readings, its schedule and the record of how it was found are gone with it

#### Scenario: A reading holds counters only

- **WHEN** a reading is stored
- **THEN** it holds the video's id, the moment, and the views, likes and comments
- **AND** no title or description

### Requirement: Expiry

The system SHALL remove every channel payload and video payload fetched, and every reading taken, more than 30 days ago, except readings the derived-metrics decision keeps longer. The removal SHALL happen in every YouTube command that reaches the database, whether or not it makes requests, before the command reads or writes anything else there. The channel record SHALL remain.

#### Scenario: An expired payload is removed before any work

- **GIVEN** a channel whose payload was fetched 31 days ago
- **WHEN** any YouTube command reaches the database
- **THEN** the payload is removed before the command reads or writes anything else there
- **AND** the channel record, with its review, remains

#### Scenario: Expired videos and readings are removed before any work

- **GIVEN** a video whose payload was fetched 31 days ago, and a reading of another video taken 31 days ago
- **AND** no derived-metrics decision recorded
- **WHEN** any YouTube command reaches the database
- **THEN** both are removed before the command reads or writes anything else there

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

The system SHALL treat a channel YouTube no longer returns when asked for by id, by any command, as gone: its payload SHALL be removed and the time recorded, and its record and review SHALL remain. A gone channel that YouTube returns again SHALL be restored.

#### Scenario: A channel YouTube no longer returns

- **GIVEN** a channel whose id YouTube answers with no channel
- **WHEN** a refresh asks for it
- **THEN** its payload is removed
- **AND** it is marked gone, with the time
- **AND** its record and review remain

#### Scenario: The poll loop finds a channel gone

- **GIVEN** a seed channel whose id YouTube answers with no channel
- **WHEN** the poll loop asks for it
- **THEN** it is marked gone exactly as a refresh would mark it

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

Database dumps SHALL NOT contain YouTube API data. The daily inventory dump SHALL contain the YouTube channel records; restoring any dump SHALL leave payloads and readings empty until YouTube is asked again.

#### Scenario: A full dump leaves the payloads out

- **WHEN** a full dump is taken, including the one taken before a migration
- **THEN** it holds the definition of the payload table and none of its rows

#### Scenario: A full dump leaves videos and readings out

- **WHEN** a full dump is taken
- **THEN** it holds the definitions of the video and reading tables and none of their rows

#### Scenario: The inventory dump keeps the review

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every YouTube channel record with its review

#### Scenario: A restore is completed by a refresh

- **GIVEN** a database restored from a dump
- **WHEN** a refresh runs
- **THEN** every channel's payload is fetched again

## ADDED Requirements

### Requirement: Videos That Disappear

The system SHALL remove at once a tracked video YouTube no longer returns when asked for by id, with its readings. One missing video MUST NOT cost the other videos in the same request their reading.

#### Scenario: A deleted or private video

- **GIVEN** a tracked video with readings
- **WHEN** a request for it is answered without it
- **THEN** its payload and every reading of it are removed

#### Scenario: One missing video in a batch

- **GIVEN** a request for fifty videos, one of which YouTube no longer returns
- **WHEN** the answer arrives
- **THEN** the other forty-nine are read
- **AND** only the missing one is removed

#### Scenario: A batch answered not found

- **GIVEN** a request for several videos answered not found as a whole
- **WHEN** the poll loop handles it
- **THEN** each video in it is asked for alone
- **AND** only those YouTube does not return are removed

### Requirement: Statistics Retention Follows The Recorded Decision

Until the date YouTube accepted the derived-metrics use case is recorded in the code, readings SHALL be kept at most 30 days. Once it is recorded, readings taken on or after it SHALL be kept up to 36 calendar months and earlier ones still 30 days. Titles, handles and descriptions SHALL stay at 30 days in any case. The date MUST NOT be a setting.

#### Scenario: Before acceptance

- **GIVEN** no acceptance date recorded
- **WHEN** a reading is 31 days old
- **THEN** it is removed

#### Scenario: After acceptance

- **GIVEN** an acceptance date recorded
- **AND** a reading taken on that date, 31 days ago
- **AND** a reading taken the day before it, 32 days ago
- **WHEN** expiry runs
- **THEN** the first is kept and the second is removed

#### Scenario: Thirty-six months is the limit

- **GIVEN** an acceptance date recorded
- **WHEN** a reading taken after it is more than 36 calendar months old
- **THEN** it is removed

#### Scenario: Titles do not follow the readings

- **GIVEN** an acceptance date recorded
- **WHEN** a video's payload was fetched 31 days ago
- **THEN** the payload is removed

### Requirement: Videos Are Refreshed While Their Readings Are Kept

Once an acceptance date is recorded, `itgraph yt refresh` SHALL fetch again, before the refresh age, the payload of every video past the tracking horizon that has a reading kept beyond 30 days, fifty videos to a request. Before then it SHALL refresh no video.

#### Scenario: A mature video stays attached to its readings

- **GIVEN** an acceptance date recorded
- **AND** a video past the horizon, with readings taken after that date, whose payload is older than the refresh age
- **WHEN** a refresh runs
- **THEN** its payload is fetched again
- **AND** its readings remain

#### Scenario: Nothing to refresh before acceptance

- **GIVEN** no acceptance date recorded
- **WHEN** a refresh runs
- **THEN** no video is asked for
