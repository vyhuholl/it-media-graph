# Spec Delta

## REMOVED Requirements

### Requirement: Data From YouTube Has One Place

**Reason**: Featured sections stop being a latest-payload table: every fetch appends a reading and nothing is replaced, so the scenario "a newer featured list replaces the old one" no longer holds, and the enumeration changes with it — featured joins the readings, and the chart's readings arrive. Renamed and re-added as "Data From YouTube Is Stored Once".

**Migration**: Nothing for an operator to do. The rows already stored become each seed's first reading; nothing about them changes.

## ADDED Requirements

### Requirement: Data From YouTube Is Stored Once

The system SHALL store data read from the API in one place: a channel's and a video's as its latest payload, a reading's counters as that reading, the channel behind a linked video, handle or legacy name as its lookup, a seed's featured channels as its featured readings, a search's channels as its results, and the chart's videos as its chart readings. A title, handle, description or statistic MUST NOT be copied into another table, column or log record. What is worked out from a video's data SHALL be stored and removed with its payload.

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

#### Scenario: A featured reading holds channel ids only

- **WHEN** a seed's sections are read
- **THEN** the reading holds the seed's id, the channel ids its sections list, and the moment
- **AND** no section type or title, and nothing about the listed channels

#### Scenario: A newer featured reading is stored beside the old one

- **WHEN** a seed's sections are read again
- **THEN** a new reading is appended, and the readings before it are unchanged

#### Scenario: A chart reading holds ids only

- **WHEN** a chart reading is stored
- **THEN** it holds each video's id, its channel's id and its position, and nothing else

#### Scenario: A search result holds a channel id only

- **WHEN** a search's results are stored
- **THEN** each holds its run, its position and a channel id
- **AND** no title, description or video id

#### Scenario: Removing a run removes its results

- **GIVEN** a search run with results
- **WHEN** the run is removed
- **THEN** nothing in the database says which channels it returned

## MODIFIED Requirements

### Requirement: Nothing Is Removed By Age

The system SHALL NOT remove YouTube data because of its age: channel and video payloads, readings, lookups, featured readings, chart readings, search runs and results, and the walk's records stay until YouTube stops returning the object or the operator removes them. An answer that decides what to ask YouTube next — a lookup, a featured reading, a search run and its results — SHALL be read only while younger than `youtube_answers_fresh_days` (30 by default); an older one is kept unread and replaced when asked again.

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

- **GIVEN** a featured reading fetched 31 days ago and a search run made 31 days ago
- **WHEN** a candidates report is made
- **THEN** neither is read as evidence
- **AND** both are still stored, the run with its results

#### Scenario: An old chart reading is kept and not read

- **GIVEN** a chart reading fetched eight days ago
- **WHEN** the day's chart ranking is worked out
- **THEN** that reading is not the day's chart
- **AND** it is still stored

#### Scenario: Walk records are kept

- **GIVEN** a walk that ended 90 days ago
- **WHEN** any YouTube command runs, whether or not the walk is enabled
- **THEN** its record remains
