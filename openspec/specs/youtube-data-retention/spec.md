# youtube-data-retention Specification

## Purpose
This capability owns where data read from the YouTube Data API lives, how it is kept current, what happens to what YouTube stops returning, and what dumps and restores carry of it.

## Requirements

### Requirement: Data From YouTube Has One Place

The system SHALL store data read from the API in one place only: a channel's as its latest payload, a video's as its latest payload, a reading's counters as that reading, which channel a linked video, handle or legacy name belongs to as that name's lookup, which channels a seed features as that seed's featured list, and which channels a search returned as that search's results. A title, handle, description or statistic MUST NOT be copied into any other table, column or log record. What the system works out from a video's data SHALL be stored with that video's payload and removed with it.

#### Scenario: The title lives in the payload alone

- **GIVEN** a channel with a stored payload
- **WHEN** the database is searched for that channel's title
- **THEN** it is found in the channel payloads' table and nowhere else

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

#### Scenario: A lookup holds a channel id only

- **WHEN** a lookup is stored
- **THEN** it holds the name looked up, the channel id YouTube named or that it named none, and the moment
- **AND** no title, handle read from the API, description or statistic

#### Scenario: A featured list holds channel ids only

- **WHEN** a seed's featured list is stored
- **THEN** it holds the seed's id, the channel ids its sections list, and the moment
- **AND** no section type or title, and nothing about the listed channels

#### Scenario: A newer featured list replaces the old one

- **WHEN** a seed's sections are read again
- **THEN** its stored list is replaced, not added to

#### Scenario: A search result holds a channel id only

- **WHEN** a search's results are stored
- **THEN** each holds its run, its position and a channel id
- **AND** no title, description or video id

#### Scenario: Removing a run removes its results

- **GIVEN** a search run with results
- **WHEN** the run is removed
- **THEN** nothing in the database says which channels it returned

### Requirement: Refresh

The system SHALL provide `itgraph yt refresh`, fetching anew the payload of every channel whose payload is older than the refresh age or absent, whatever the channel's status. The refresh age SHALL be configurable and default to 25 days.

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

### Requirement: Nothing Is Removed By Age

The system SHALL NOT remove YouTube data because of its age: channel and video payloads, readings, lookups, featured lists, search runs and results, and the history walk's records stay until YouTube stops returning the object or the operator removes them. An answer that decides what to ask YouTube next — a lookup, a featured list, a search run with its results — SHALL be read only while it is younger than `youtube_answers_fresh_days` (30 by default); an older one is kept, not read, and replaced when the question is asked again.

#### Scenario: An old payload is kept

- **GIVEN** a channel whose payload was fetched 90 days ago
- **WHEN** any YouTube command runs
- **THEN** the payload is still there, with the channel record and its review

#### Scenario: Old videos and readings are kept

- **GIVEN** a video whose payload was fetched 31 days ago, with readings taken 32 and 31 days ago
- **WHEN** any YouTube command runs
- **THEN** the video and both readings are still there

#### Scenario: An old lookup is kept and not read

- **GIVEN** a lookup fetched 31 days ago
- **WHEN** what is answered is read
- **THEN** that lookup is not among the answers
- **AND** it is still stored, until the name is looked up again and the answer replaced

#### Scenario: An old featured list and search run are kept and not read

- **GIVEN** a featured list fetched 31 days ago and a search run made 31 days ago
- **WHEN** a candidates report is made
- **THEN** neither is read as evidence
- **AND** both are still stored, with the run's results

#### Scenario: Walk records are kept

- **GIVEN** a walk that ended 90 days ago
- **WHEN** any YouTube command runs, whether or not the walk is enabled
- **THEN** its record remains

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

### Requirement: Backups Carry YouTube Data

Full dumps SHALL contain every YouTube table with its rows, like every other table: the readings are a time series nothing can fetch again. The daily inventory dump SHALL contain the YouTube channel records and the operator's search queries.

#### Scenario: A full dump leaves nothing out

- **WHEN** a full dump is taken, including the one taken before a migration
- **THEN** no table's rows are left out of it

#### Scenario: The inventory dump keeps the review

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every YouTube channel record with its review

#### Scenario: The inventory dump keeps the queries

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every search query the operator has asked, with its type and dates

#### Scenario: A restore from the inventory dump is completed by a refresh

- **GIVEN** a database restored from an inventory dump
- **WHEN** a refresh runs
- **THEN** every channel's payload is fetched again
- **AND** every seed's featured list is read again

#### Scenario: A restore without walk records is completed by a walk

- **GIVEN** a database with neither videos nor walk records
- **AND** the walk enabled
- **WHEN** the next walk runs
- **THEN** every seed is walked anew

### Requirement: Review Files

A review file holds collected channel data. Its header SHALL say to delete it once applied and never to commit it. A file's age SHALL NOT stop it being applied.

#### Scenario: The file says what to do with it

- **WHEN** a review file is exported
- **THEN** its header states its export moment, and that it is to be deleted once applied and never committed

#### Scenario: An old file is applied

- **GIVEN** a review file exported more than 30 days ago
- **WHEN** it is applied
- **THEN** its decisions are written like a new file's

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

### Requirement: Past Uploads Are Kept Fresh While The Walk Is Enabled

While the history walk is enabled, `itgraph yt refresh` SHALL fetch again, before the refresh age and fifty to a request, every video of a seed channel that is past the tracking horizon, was published inside the walk's depth, and is not a broadcast yet to start. Each such fetch SHALL also take a reading.

#### Scenario: A past upload is refreshed

- **GIVEN** the walk enabled
- **AND** a seed's video published 100 days ago whose payload is older than the refresh age
- **WHEN** a refresh runs
- **THEN** its payload is fetched again and replaced

#### Scenario: A video the poll loop finished joins the year

- **GIVEN** the walk enabled
- **AND** a video the poll loop stopped reading at the horizon, whose payload is older than the refresh age
- **WHEN** a refresh runs
- **THEN** its payload is fetched again

#### Scenario: A video past the depth is not read again

- **GIVEN** the walk enabled and a depth of 365 days
- **AND** a seed's video published 370 days ago
- **WHEN** a refresh runs
- **THEN** it is not asked for, and stays as it was last read

#### Scenario: A channel out of scope is not kept

- **GIVEN** the walk enabled
- **AND** a past upload of a channel that is no longer a seed
- **WHEN** a refresh runs
- **THEN** it is not asked for

#### Scenario: Nothing is read again while the walk is disabled

- **GIVEN** the walk disabled
- **AND** a past upload stored by a walk run by hand
- **WHEN** a refresh runs
- **THEN** it is not asked for

#### Scenario: Every refresh of a past upload is a reading

- **GIVEN** the walk enabled
- **WHEN** a refresh fetches a past upload
- **THEN** a reading is stored from the same answer

#### Scenario: A past upload YouTube no longer returns

- **GIVEN** the walk enabled
- **WHEN** a refresh asks for a past upload and the answer does not hold it
- **THEN** the video is removed

### Requirement: A Restored Copy Sends No YouTube Alert

After a restore into a test environment, every YouTube alert that was not delivered in production SHALL be closed as delivered before anything can send it, and the restore's read-back SHALL fail if one remains. A test environment SHALL hold no YouTube API key.

#### Scenario: Restoring a real dump

- **GIVEN** a full dump of a database with YouTube data and undelivered YouTube alerts
- **WHEN** it is restored
- **THEN** the YouTube data is there
- **AND** no YouTube alert is left undelivered

#### Scenario: An undelivered YouTube alert fails the read-back

- **GIVEN** a restored copy still holding an undelivered YouTube alert
- **WHEN** the read-back runs
- **THEN** it fails, naming undelivered YouTube alerts

#### Scenario: No key in a test environment

- **GIVEN** an environment that does not collect
- **WHEN** the role has run
- **THEN** no pod holds a YouTube API key, and no YouTube job exists
