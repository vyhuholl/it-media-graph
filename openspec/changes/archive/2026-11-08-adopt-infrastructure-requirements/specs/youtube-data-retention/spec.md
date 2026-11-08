# Spec Delta

## ADDED Requirements

### Requirement: A Restored Copy Holds No API Data

After a restore into a test environment, every table whose rows dumps leave out SHALL be read back as empty, and the restore SHALL fail otherwise. A test environment SHALL hold no YouTube API key, and so fetches nothing that would need refreshing or expiring.

#### Scenario: Restoring a real dump

- **GIVEN** a full dump of a database with payloads, readings, lookups, featured lists, search runs and walk records
- **WHEN** it is restored
- **THEN** each of those tables holds no row
- **AND** the YouTube channel records and their review are there

#### Scenario: A dump that carries API data by mistake

- **GIVEN** a dump holding a row in a `yt_raw_*` table
- **WHEN** it is restored
- **THEN** the restore fails naming the table, and the database is left empty

#### Scenario: The list of tables cannot drift

- **WHEN** a table is added to what dumps leave out
- **THEN** the test suite fails until the restore's read-back covers it

#### Scenario: No key in a test environment

- **GIVEN** an environment that does not collect
- **WHEN** the role has run
- **THEN** no pod holds a YouTube API key, and no YouTube job exists
