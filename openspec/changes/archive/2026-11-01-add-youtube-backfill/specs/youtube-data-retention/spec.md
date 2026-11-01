## ADDED Requirements

### Requirement: Past Uploads Are Kept Fresh While The Walk Is Enabled

While the history walk is enabled, `itgraph yt refresh` SHALL fetch again, before the refresh age and fifty to a request, every video of a seed channel that is past the tracking horizon, was published inside the walk's depth, and is not a broadcast yet to start. Until acceptance is recorded it SHALL ask for no counts. Once it is, each such fetch SHALL also take a reading.

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

#### Scenario: A video past the depth is left to expire

- **GIVEN** the walk enabled and a depth of 365 days
- **AND** a seed's video published 370 days ago, with no reading kept beyond 30 days
- **WHEN** a refresh runs
- **THEN** it is not asked for, and it expires 30 days after its last fetch

#### Scenario: A channel out of scope is not kept

- **GIVEN** the walk enabled
- **AND** a past upload of a channel that is no longer a seed
- **WHEN** a refresh runs
- **THEN** it is not asked for

#### Scenario: Nothing is kept while the walk is disabled

- **GIVEN** the walk disabled
- **AND** a past upload stored by a walk run by hand
- **WHEN** a refresh runs
- **THEN** it is not asked for, and it expires 30 days after its fetch

#### Scenario: Before acceptance no counts are read

- **GIVEN** the walk enabled and no acceptance date recorded
- **WHEN** a refresh fetches a past upload
- **THEN** the request asks for no counts, the stored data holds none, and no reading is stored

#### Scenario: After acceptance every refresh is a reading

- **GIVEN** the walk enabled and an acceptance date recorded
- **WHEN** a refresh fetches a past upload
- **THEN** a reading is stored from the same answer

#### Scenario: A past upload YouTube no longer returns

- **GIVEN** the walk enabled
- **WHEN** a refresh asks for a past upload and the answer does not hold it
- **THEN** the video is removed

## MODIFIED Requirements

### Requirement: Videos Are Refreshed While Their Readings Are Kept

Once an acceptance date is recorded, `itgraph yt refresh` SHALL fetch again, before the refresh age, the payload of every video past the tracking horizon that has a reading kept beyond 30 days, fifty videos to a request, whether or not the walk keeps it. Before then it SHALL refresh no video but the past uploads the walk keeps fresh.

#### Scenario: A mature video stays attached to its readings

- **GIVEN** an acceptance date recorded
- **AND** a video past the horizon, with readings taken after that date, whose payload is older than the refresh age
- **WHEN** a refresh runs
- **THEN** its payload is fetched again
- **AND** its readings remain

#### Scenario: Readings outlive the depth

- **GIVEN** an acceptance date recorded and a depth of 365 days
- **AND** a video published 400 days ago, with readings taken after that date, whose payload is older than the refresh age
- **WHEN** a refresh runs
- **THEN** its payload is fetched again

#### Scenario: Nothing to refresh before acceptance

- **GIVEN** no acceptance date recorded
- **AND** the walk disabled
- **WHEN** a refresh runs
- **THEN** no video is asked for

### Requirement: Backups Carry No API Data

Database dumps SHALL NOT contain YouTube API data, nor the history walk's records, which describe API data the dumps leave out. The daily inventory dump SHALL contain the YouTube channel records; restoring any dump SHALL leave payloads, readings and walk records empty until YouTube is asked again.

#### Scenario: A full dump leaves the payloads out

- **WHEN** a full dump is taken, including the one taken before a migration
- **THEN** it holds the definition of the payload table and none of its rows

#### Scenario: A full dump leaves videos and readings out

- **WHEN** a full dump is taken
- **THEN** it holds the definitions of the video and reading tables and none of their rows

#### Scenario: A full dump leaves the walk records out

- **WHEN** a full dump is taken
- **THEN** it holds the definition of the walk records' table and none of its rows

#### Scenario: The inventory dump keeps the review

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every YouTube channel record with its review

#### Scenario: A restore is completed by a refresh

- **GIVEN** a database restored from a dump
- **WHEN** a refresh runs
- **THEN** every channel's payload is fetched again

#### Scenario: A restore is completed by a walk

- **GIVEN** a database restored from a dump
- **AND** the walk enabled
- **WHEN** the next walk runs
- **THEN** every seed is walked anew
