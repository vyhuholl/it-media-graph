## ADDED Requirements

### Requirement: Past Uploads Are Kept Fresh While The Walk Is Enabled

While the history walk is enabled, `itgraph yt refresh` SHALL fetch again, before the refresh age and fifty to a request, every video of a seed channel that is past the tracking horizon, was published inside the walk's depth, and is not a broadcast yet to start. Once it is, each such fetch SHALL also take a reading.

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

#### Scenario: A channel out of scope is not kept

- **GIVEN** the walk enabled
- **AND** a past upload of a channel that is no longer a seed
- **WHEN** a refresh runs
- **THEN** it is not asked for

#### Scenario: A past upload YouTube no longer returns

- **GIVEN** the walk enabled
- **WHEN** a refresh asks for a past upload and the answer does not hold it
- **THEN** the video is removed
