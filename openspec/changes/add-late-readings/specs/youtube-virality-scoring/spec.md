# Spec Delta

## MODIFIED Requirements

### Requirement: Too Few Newest Videos Fall Back To The Settled Ones

Where a channel has too few newest videos of a format, the system SHALL use for views and likes the median of its settled videos of that format — one reading each, the earliest taken between 29 and 120 days of age by default — needing a configured minimum, scaled by its kind's factor. Comments SHALL take no fallback. A channel on the fallback SHALL be recorded as such.

#### Scenario: A channel uploading twice a month

- **GIVEN** a channel with three long-form videos watched from publication and fifteen read at 29 to 120 days of age
- **WHEN** baselines are refreshed
- **THEN** its long-form views and likes are referenced to the settled median times its kind's factor
- **AND** the baseline records that it is the fallback
- **AND** no comment reference is given

#### Scenario: One reading per settled video

- **GIVEN** a settled video read at 40, 65 and 90 days of age
- **WHEN** the settled median is taken
- **THEN** the video contributes its reading at 40 days alone

#### Scenario: Daily readings do not age the reference

- **GIVEN** a channel whose settled videos are each read daily from 29 to 120 days of age
- **WHEN** baselines are refreshed on two days a month apart
- **THEN** a video already past 29 days on the first day contributes the same reading on both days, its earliest inside the window
