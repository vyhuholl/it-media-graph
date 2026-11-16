# Spec Delta

## ADDED Requirements

### Requirement: Past Uploads Are Read Daily, Then Weekly

While the history walk is enabled, `itgraph yt refresh` SHALL take a reading of every past upload it keeps fresh whose newest reading is older than its interval. The interval is one day up to 120 days of age and seven days after, and each interval comes due a few hours early. The readings SHALL be taken after past uploads' data is fetched again, fifty videos to a unit, from the general pool.

#### Scenario: A young past upload is read daily

- **GIVEN** the walk enabled
- **AND** a seed's video 60 days old, last read 24 hours ago
- **WHEN** a refresh runs
- **THEN** a reading of it is stored

#### Scenario: An older past upload is read weekly

- **GIVEN** the walk enabled
- **AND** a seed's video 200 days old, last read 3 days ago, and another as old, last read 7 days ago
- **WHEN** a refresh runs
- **THEN** the second is read and the first is not

#### Scenario: A daily run does not slip a day

- **GIVEN** the walk enabled
- **AND** a seed's video 60 days old, last read by the refresh of the day before, 23 hours ago
- **WHEN** a refresh runs
- **THEN** a reading of it is stored

#### Scenario: A video read once in a run is not read twice

- **GIVEN** the walk enabled
- **AND** a past upload whose data this run fetched again, taking a reading
- **WHEN** the same run takes the late readings
- **THEN** no second reading of it is stored

#### Scenario: What the refresh does not keep is not read

- **GIVEN** the walk enabled
- **AND** a seed's video published 370 days ago with a depth of 365 days, a video the poll loop still reads, a broadcast yet to start, and a past upload of a channel that is no longer a seed
- **WHEN** a refresh runs
- **THEN** no late reading of any of them is taken

#### Scenario: Nothing is read while the walk is disabled

- **GIVEN** the walk disabled
- **AND** a past upload last read 30 days ago
- **WHEN** a refresh runs
- **THEN** it is not read

#### Scenario: Every format is read alike

- **GIVEN** the walk enabled
- **AND** a 40-second past upload and a finished stream, each last read 2 days ago and 60 days old
- **WHEN** a refresh runs
- **THEN** a reading of each is stored

#### Scenario: Videos share requests

- **GIVEN** 120 past uploads due a late reading
- **WHEN** a refresh runs
- **THEN** three requests are made for them

### Requirement: A Late Reading Leaves The Video's Data As It Was

A late reading SHALL store the counts as a reading and nothing else. The video's stored data and its fetch time SHALL stay as they were. A video the answer does not hold SHALL be removed with its readings, as any other request for videos removes it.

#### Scenario: The stored data is not replaced

- **GIVEN** a past upload whose data was fetched 10 days ago
- **WHEN** a late reading of it is taken
- **THEN** its stored data and fetch time are unchanged
- **AND** a reading holding its views, likes and comments, absent ones as absent, is stored with the moment of the answer

#### Scenario: A past upload YouTube no longer returns

- **GIVEN** a past upload due a late reading
- **WHEN** the answer does not hold it
- **THEN** the video and every reading of it are removed
- **AND** the other videos of the request are read

#### Scenario: Exhaustion stops the readings part way

- **WHEN** the general pool is spent, or the quota exhausted, during the late readings
- **THEN** the readings taken before it are kept
- **AND** the next run reads those still due

### Requirement: Each Refresh Is Recorded

Every run of `itgraph yt refresh` SHALL record when it started and ended, how it ended — complete, stopped for quota, or failed — and how many late readings were due and how many were taken. A run that ends without a record of completing SHALL NOT count as complete. The records SHALL be kept.

#### Scenario: A complete run

- **WHEN** a refresh does everything that was due
- **THEN** a record of it is stored as complete, with its counts

#### Scenario: A run stopped for quota

- **GIVEN** a general pool spent part way through the late readings
- **WHEN** the refresh ends
- **THEN** its record says it stopped for quota
- **AND** it does not count as complete

#### Scenario: A run that fails

- **GIVEN** a refresh whose request fails for a reason other than quota
- **WHEN** it ends
- **THEN** its record says it failed
