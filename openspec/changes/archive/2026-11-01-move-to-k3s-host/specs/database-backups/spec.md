# Spec Delta

## Purpose

Where the database's dumps live and what leaves the collection host: every verified dump is also copied off-site, encrypted before it leaves, into a store the host can add to but never delete from or read back, within a fixed budget that costs nothing.

## ADDED Requirements

### Requirement: Dumps Outlive The Workloads

The system SHALL keep dumps on storage that survives the removal and recreation of every workload in the collection namespace, the database's own included. Taking and verifying a dump SHALL need nothing installed on the host itself.

#### Scenario: Workloads are recreated

- **GIVEN** dumps taken on the collection host
- **WHEN** every workload in the namespace is deleted and deployed again
- **THEN** the same dumps are present afterwards

#### Scenario: Nothing on the host but the cluster

- **GIVEN** a host with no container runtime or PostgreSQL client of its own beyond the cluster's
- **WHEN** a scheduled backup runs
- **THEN** the dump is taken and read back with `pg_restore --list` as before

### Requirement: Every Verified Dump Is Copied Off-Site

The system SHALL copy every dump that passed its read-back to an off-site store: inventory and full dumps alike, the dump taken before a migration included. A dump that failed its read-back SHALL NOT be copied. The copy is the same dump, so it carries no YouTube API data.

#### Scenario: A scheduled dump is copied

- **WHEN** a scheduled backup takes and verifies a dump
- **THEN** the same run copies it off-site

#### Scenario: The pre-migration dump is copied

- **WHEN** a migration takes its full dump first
- **THEN** that dump is copied off-site no later than the next scheduled backup run

#### Scenario: A dump that failed its read-back stays home

- **WHEN** a dump fails `pg_restore --list`
- **THEN** nothing is uploaded for it

#### Scenario: A failed upload is caught up

- **GIVEN** a verified dump whose upload failed
- **WHEN** the next backup run starts
- **THEN** that dump is uploaded before the run reports success

#### Scenario: A failed upload is a failed run

- **WHEN** an upload fails
- **THEN** the backup run exits non-zero and says which dump was not copied
- **AND** the local dump and its pruning are unaffected

### Requirement: Copies Are Encrypted Before They Leave

The system SHALL encrypt every copy on the host, to a public recipient key, before any byte of it is sent. The private key SHALL NOT be on the collection host or in the repository.

#### Scenario: The store holds ciphertext

- **WHEN** an off-site copy is downloaded
- **THEN** it is not readable as a PostgreSQL archive without the private key

#### Scenario: The host cannot read its own copies

- **WHEN** the collection host's configuration and volumes are searched
- **THEN** no key able to decrypt an off-site copy is found

#### Scenario: A copy decrypts to the dump

- **GIVEN** an off-site copy and the operator's private key
- **WHEN** the copy is decrypted
- **THEN** the result is byte-identical to the local dump it was made from
- **AND** `pg_restore --list` reads it

### Requirement: The Host Cannot Remove Or Replace A Copy

The credential the collection host holds for the off-site store SHALL allow adding copies and listing their names, and SHALL NOT allow deleting, overwriting or downloading them. A copy's name SHALL be unique, so that a new copy never takes the place of an old one.

#### Scenario: Deleting with the host's credential

- **WHEN** a delete of an off-site copy is attempted with the host's credential
- **THEN** the store refuses it

#### Scenario: A name already in the store

- **GIVEN** a dump whose copy is already in the store
- **WHEN** a backup run starts
- **THEN** it is not uploaded again

### Requirement: A Copy Counts Once The Store Confirms It

The system SHALL treat a dump as copied only when the store has confirmed the upload against a checksum computed on the host. An upload whose checksum the store did not confirm SHALL count as failed.

#### Scenario: The checksum does not match

- **WHEN** the store rejects an upload because the content does not match the checksum sent with it
- **THEN** the dump is reported as not copied, and the run fails

### Requirement: Retention Belongs To The Store

Off-site copies SHALL be removed by the store's own rules after a fixed age, one age for inventory copies and one for full copies. The collection host SHALL NOT prune off-site copies, and pruning local dumps SHALL NOT affect them.

#### Scenario: An old copy goes by itself

- **GIVEN** an off-site copy older than the retention for its kind
- **THEN** the store removes it without any action from the host

#### Scenario: Local pruning stays local

- **WHEN** a backup run prunes local dumps
- **THEN** no off-site copy is removed

### Requirement: Off-Site Copies Cost Nothing

The off-site copies SHALL stay under a fixed size ceiling inside the store's free tier, and the store's spending cap SHALL be zero, so that going over fails uploads instead of billing.

#### Scenario: Retention fits the ceiling

- **GIVEN** the current sizes of inventory and full dumps and the configured retention
- **THEN** the copies the store keeps add up to less than the ceiling

#### Scenario: Over the cap

- **WHEN** an upload would take the store past its free tier
- **THEN** the store refuses it
- **AND** the backup run fails and says so, rather than anything being billed
