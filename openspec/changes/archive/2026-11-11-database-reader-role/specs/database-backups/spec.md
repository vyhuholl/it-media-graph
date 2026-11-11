# Spec Delta

## MODIFIED Requirements

### Requirement: A Full Dump Restores Into A Test Environment With One Entry Point

The role's restore entry point SHALL take the path of a full dump on the host and replace the environment's database with it. It SHALL refuse to run unless the environment allows restores and does not collect, and SHALL refuse a file `pg_restore` cannot read, before touching anything. It SHALL never touch the session's volume.

#### Scenario: Refused in an environment that collects

- **GIVEN** an environment that collects
- **WHEN** the restore entry point runs, whatever else it is given
- **THEN** it fails before reading the file or touching anything

#### Scenario: Refused without the input

- **GIVEN** an environment that does not collect, and no input allowing restores
- **WHEN** the restore entry point runs
- **THEN** it fails before reading the file or touching anything

#### Scenario: Not a dump

- **GIVEN** a missing file, a text file, or a dump cut short inside its table of contents
- **WHEN** the restore entry point runs
- **THEN** it fails naming the path, and the database is as it was

#### Scenario: Writers stopped first

- **WHEN** the data is replaced
- **THEN** the bot is stopped, the schedules are suspended, and no Job of the namespace is running
- **AND** afterwards the bot runs and the schedules are as they were

#### Scenario: A copy from the off-site store

- **GIVEN** a dump fetched from the off-site store and decrypted
- **WHEN** it is given to the restore entry point
- **THEN** it restores as any other dump does

#### Scenario: A dump that carries the reader's grants

- **GIVEN** a full dump of a database at head, whose grants and default privileges name `itgraph_reader`
- **WHEN** it is restored into a test environment deployed at the same release
- **THEN** it loads whole, every table's rows match the dump, the read-back passes
- **AND** the reader reads every table of the restored copy, including through the default privileges, and can write none

#### Scenario: A dump older than the reader

- **GIVEN** a full dump of a database at a revision before the reader's
- **WHEN** it is restored
- **THEN** the migration to head grants the reader every table of the restored copy
