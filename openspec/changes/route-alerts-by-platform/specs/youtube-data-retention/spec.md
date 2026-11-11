# Spec Delta

## MODIFIED Requirements

### Requirement: Backups Carry No API Data

Database dumps SHALL NOT contain YouTube API data, nor the history walk's records, the search runs or the YouTube alerts, which describe API data the dumps leave out. The daily inventory dump SHALL contain the YouTube channel records and the operator's search queries; restoring any dump SHALL leave payloads, readings, lookups, featured lists, search runs and results, walk records and YouTube alerts empty until YouTube is asked again.

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

#### Scenario: A full dump leaves the YouTube alerts out

- **WHEN** a full dump is taken
- **THEN** it holds the definition of the YouTube alerts' table and none of its rows

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

### Requirement: A Restored Copy Holds No API Data

After a restore into a test environment, every table whose rows dumps leave out SHALL be read back as empty, and the restore SHALL fail otherwise. A test environment SHALL hold no YouTube API key, and so fetches nothing that would need refreshing or expiring.

#### Scenario: Restoring a real dump

- **GIVEN** a full dump of a database with payloads, readings, lookups, featured lists, search runs, walk records and YouTube alerts
- **WHEN** it is restored
- **THEN** each of those tables holds no row
- **AND** the YouTube channel records and their review are there

#### Scenario: A dump that carries API data by mistake

- **GIVEN** a dump holding a row in a `yt_raw_*` table
- **WHEN** it is restored
- **THEN** the restore fails naming the table, and the database is left empty

#### Scenario: A dump that carries a YouTube alert by mistake

- **GIVEN** a dump holding a YouTube alert
- **WHEN** it is restored
- **THEN** the restore fails, naming the alerts' table among what it found, and the database is left empty

#### Scenario: The list of tables cannot drift

- **WHEN** a table is added to what dumps leave out
- **THEN** the test suite fails until the restore's read-back covers it

#### Scenario: No key in a test environment

- **GIVEN** an environment that does not collect
- **WHEN** the role has run
- **THEN** no pod holds a YouTube API key, and no YouTube job exists
