## MODIFIED Requirements

### Requirement: Discovery Is Recorded

The system SHALL record, with each tracked video, when it was found and by which path: a changed video count, the daily check, the channel's first listing, or — for a broadcast yet to start that the history walk handed over — the walk.

#### Scenario: A video found by the daily check

- **GIVEN** a video the video count did not reveal
- **WHEN** the daily check lists it
- **THEN** the video is recorded as found by the daily check, with the moment

#### Scenario: A broadcast handed over by the walk

- **GIVEN** a scheduled stream the history walk handed to the poll loop
- **WHEN** the poll loop checks or reads it
- **THEN** it is still recorded as found by the history walk

### Requirement: Status Report

The system SHALL provide `itgraph yt watch-status`, reporting the number of tracked videos, how many readings are due and the oldest overdue one, the last pass and what it deferred or could not do, the poll pool's spending against its cap and its pace, and, over the last 7 days, how many videos each of the poll loop's own discovery paths found, not counting videos the history walk found. It makes no request.

#### Scenario: What the count missed is visible

- **GIVEN** videos found over the last week by a changed count and by the daily check
- **WHEN** the command runs
- **THEN** it reports how many each path found

#### Scenario: The walk's finds stay out of the loop's counts

- **GIVEN** a thousand videos stored by the history walk this week
- **WHEN** the command runs
- **THEN** none of them is counted under any of the poll loop's discovery paths

#### Scenario: Reporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made
