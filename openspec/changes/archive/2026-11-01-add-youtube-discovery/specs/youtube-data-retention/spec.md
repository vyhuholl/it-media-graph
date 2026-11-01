## MODIFIED Requirements

### Requirement: API Data Has One Home

The system SHALL store data read from the API in one home only: a channel's as its latest payload, a video's as its latest payload, a reading's counters as that reading, which channel a linked video, handle or legacy name belongs to as that name's lookup, which channels a seed features as that seed's featured list, and which channels a search returned as that search's results. A title, handle, description or statistic MUST NOT be copied into any other table, column, log record or backup, so that removing the home removes the data. What the system works out from a video's data SHALL be stored with that video's payload and removed with it.

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

#### Scenario: A lookup holds a channel id only

- **WHEN** a lookup is stored
- **THEN** it holds the name looked up, the channel id YouTube named or that it named none, and the moment
- **AND** no title, handle read from the API, description or statistic

#### Scenario: Removing a lookup removes the answer

- **GIVEN** a video whose channel is known only from its lookup
- **WHEN** the lookup is removed
- **THEN** nothing in the database says which channel the video belongs to

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

### Requirement: Expiry

The system SHALL remove every channel payload and video payload fetched, every lookup and featured list fetched, every search run made with its results, and every reading taken, more than 30 days ago, except readings the derived-metrics decision keeps longer. The removal SHALL happen in every YouTube command that reaches the database, whether or not it makes requests, before the command reads or writes anything else there. The channel record SHALL remain.

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

#### Scenario: An expired lookup is removed before any work

- **GIVEN** a lookup fetched 31 days ago, found or not found
- **WHEN** any YouTube command reaches the database, the offline ranking of Telegram links included
- **THEN** the lookup is removed before the command reads or writes anything else there

#### Scenario: An expired featured list and search run are removed before any work

- **GIVEN** a featured list fetched 31 days ago and a search run made 31 days ago
- **WHEN** any YouTube command reaches the database, an offline candidates report included
- **THEN** the list, the run and its results are removed before the command reads or writes anything else there
- **AND** the operator's record of the query remains

#### Scenario: A command that refuses its input touches nothing

- **GIVEN** a command whose arguments or input are refused before it reaches the database, or one asked only for its help
- **WHEN** it exits
- **THEN** it has neither read nor written the database, expired payloads included
- **AND** the next command that reaches the database removes them

#### Scenario: Expiry needs no network

- **GIVEN** no API key configured and no network
- **WHEN** a YouTube command that makes no request starts
- **THEN** expired payloads are still removed

### Requirement: Backups Carry No API Data

Database dumps SHALL NOT contain YouTube API data, nor the history walk's records or the search runs, which describe API data the dumps leave out. The daily inventory dump SHALL contain the YouTube channel records and the operator's search queries; restoring any dump SHALL leave payloads, readings, lookups, featured lists, search runs and results, and walk records empty until YouTube is asked again.

#### Scenario: A full dump leaves the payloads out

- **WHEN** a full dump is taken, including the one taken before a migration
- **THEN** it holds the definition of the payload table and none of its rows

#### Scenario: A full dump leaves videos and readings out

- **WHEN** a full dump is taken
- **THEN** it holds the definitions of the video and reading tables and none of their rows

#### Scenario: A full dump leaves the lookups out

- **WHEN** a full dump is taken
- **THEN** it holds the definition of the lookup table and none of its rows

#### Scenario: A full dump leaves the walk records out

- **WHEN** a full dump is taken
- **THEN** it holds the definition of the walk records' table and none of its rows

#### Scenario: A full dump leaves featured lists and searches out

- **WHEN** a full dump is taken
- **THEN** it holds the definitions of the featured lists', search runs' and search results' tables and none of their rows

#### Scenario: The inventory dump keeps the review

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every YouTube channel record with its review

#### Scenario: The inventory dump keeps the queries

- **WHEN** the daily inventory dump is taken
- **THEN** it holds every search query the operator has asked, with its type and dates

#### Scenario: A restore is completed by a refresh

- **GIVEN** a database restored from a dump
- **WHEN** a refresh runs
- **THEN** every channel's payload is fetched again
- **AND** every seed's featured list is read again

#### Scenario: A restore is completed by a walk

- **GIVEN** a database restored from a dump
- **AND** the walk enabled
- **WHEN** the next walk runs
- **THEN** every seed is walked anew

#### Scenario: A restore is completed by a lookup run

- **GIVEN** a database restored from a dump
- **WHEN** `itgraph yt tg-lookup` runs
- **THEN** every name linked from the seeds that no stored payload answers is looked up anew

#### Scenario: A restored query is not taken for a recent run

- **GIVEN** a database restored from a dump, holding a query searched 5 days before the dump
- **WHEN** the query is searched again
- **THEN** it is asked, since no run of it is in date
