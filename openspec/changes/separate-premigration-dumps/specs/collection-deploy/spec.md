# Spec Delta

## MODIFIED Requirements

### Requirement: A Migration Dump Only When Migrating

A deploy SHALL run a migration only when the database is behind head. A deploy against a database already at head SHALL run no migration, and so take no pre-migration dump and prune no older one. When a migration runs, the deploy SHALL report the revisions it moved between and where the pre-migration dump was written.

#### Scenario: Already at head

- **GIVEN** a host whose schema is at head
- **WHEN** the full deploy runs
- **THEN** no migration runs
- **AND** no new file appears among the host's pre-migration dumps or its full dumps, and none is pruned

#### Scenario: Behind head

- **GIVEN** a host whose schema is behind head
- **WHEN** the full deploy runs
- **THEN** exactly one pre-migration dump is taken, before the migration, and no full dump
- **AND** the output names the revision before and after, and the path of that dump

#### Scenario: Repeated re-runs

- **GIVEN** a host whose schema is at head
- **AND** no scheduled full dump falls due while the deploys run
- **WHEN** the full deploy is run four times in a row
- **THEN** the host's pre-migration dumps and full dumps are the same files after the fourth run as before the first
