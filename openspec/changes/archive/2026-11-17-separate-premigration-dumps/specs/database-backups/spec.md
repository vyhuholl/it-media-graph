# Spec Delta

## MODIFIED Requirements

### Requirement: Full Dumps Stay Four Weeks

The system SHALL keep every local full dump taken in the last 28 days, a full dump taken by hand included, and SHALL keep the four newest whatever their age. A full dump SHALL be pruned only when it is both older than 28 days and not among the four newest. A dump taken before a migration is not a full dump here: it neither counts toward these bounds nor is pruned by them.

#### Scenario: Full dumps taken by hand

- **GIVEN** weekly full dumps from the last four weeks
- **WHEN** three full dumps are taken by hand in one day
- **THEN** every weekly full dump from the last four weeks is still there

#### Scenario: A day of migrations

- **GIVEN** weekly full dumps from the last four weeks
- **WHEN** three migrations in one day each take their dump first
- **THEN** the host's full dumps are the same files as before the first migration

#### Scenario: Dumps that stopped

- **GIVEN** no full dump taken for six weeks
- **WHEN** the next one is taken
- **THEN** the four newest full dumps remain

## REMOVED Requirements

### Requirement: Every Verified Dump Is Copied Off-Site

**Reason**: The dump taken before a migration no longer leaves the host. Copied off-site, one dump per deploy that migrated put 16 full copies into the store in 17 days where its budget allows about five, until the account reached its cap. Replaced by "Inventory And Full Dumps Are Copied Off-Site", which keeps every other scenario of this requirement.

**Migration**: Nothing for an operator to do. Pre-migration dumps taken before the release are in `weekly/` as full dumps, and stay eligible for copying until they leave the seven-day window. Until then, the operator deletes from the store only copies outside that window, as before.

## ADDED Requirements

### Requirement: Inventory And Full Dumps Are Copied Off-Site

The system SHALL copy every inventory and full dump that passed its read-back to an off-site store, a full dump taken by hand included. A dump taken before a migration SHALL NOT be copied: it stays on the host. A dump that failed its read-back SHALL NOT be copied. The copy is the same dump.

#### Scenario: A scheduled dump is copied

- **WHEN** a scheduled backup takes and verifies a dump
- **THEN** the same run copies it off-site

#### Scenario: A full dump taken by hand is copied

- **WHEN** a full dump is taken by hand
- **THEN** it is copied off-site no later than the next scheduled backup run

#### Scenario: The pre-migration dump stays on the host

- **GIVEN** a migration that took its dump first
- **WHEN** the next scheduled backup run ends
- **THEN** nothing was uploaded for that dump, and the store holds no copy of it

#### Scenario: A dump that failed its read-back stays home

- **WHEN** a dump fails `pg_restore --list`
- **THEN** nothing is uploaded for it

#### Scenario: A failed upload is caught up

- **GIVEN** a verified inventory or full dump whose upload failed
- **WHEN** the next backup run starts
- **THEN** that dump is uploaded before the run reports success

#### Scenario: A busy store is asked again

- **WHEN** the store answers an upload with a transient failure — busy, timed out, rate-limited, or a dropped connection
- **THEN** the upload is tried again at a new upload URL after a growing pause, a bounded number of times
- **AND** a refusal that is not transient, such as the spending cap, is not tried again

#### Scenario: A failed upload is a failed run

- **WHEN** an upload fails
- **THEN** the backup run exits non-zero and says which dump was not copied
- **AND** the local dump and its pruning are unaffected

### Requirement: A Migration's Dump Is A Tier Of Its Own

Before a migration, the system SHALL take its dump of the whole database into a tier of its own, apart from full dumps, with its own place and name on the backups storage and its own pointer to its newest verified dump. A pre-migration dump SHALL NOT count toward the full dumps' schedule, retention or pointer, and full dumps SHALL NOT count toward its own.

#### Scenario: Migrations do not hold back the weekly dump

- **GIVEN** a newest full dump seven days old
- **AND** a migration's dump taken on each day since
- **WHEN** a scheduled backup runs
- **THEN** a full dump is taken
- **AND** it is copied off-site

#### Scenario: Its own pointer

- **GIVEN** a pointer to the newest verified full dump
- **WHEN** a migration takes its dump first
- **THEN** the pre-migration pointer names that dump
- **AND** the full dumps' pointer names the same dump as before

#### Scenario: A full dump leaves the pre-migration pointer alone

- **GIVEN** a pointer to the newest verified pre-migration dump
- **WHEN** a full dump is taken
- **THEN** the pre-migration pointer names the same dump as before

#### Scenario: Read back like every dump

- **WHEN** a migration takes its dump first
- **THEN** the dump is read back with `pg_restore --list` before it counts, and before its pointer moves

### Requirement: Pre-Migration Dumps Are Kept By Count

The system SHALL keep the five newest pre-migration dumps whatever their age, and SHALL prune an older one only after a new pre-migration dump has been taken and verified. Pruning them SHALL touch no other dump.

#### Scenario: A sixth migration

- **GIVEN** five pre-migration dumps
- **WHEN** a migration takes a sixth
- **THEN** the oldest pre-migration dump is removed
- **AND** no full or inventory dump is removed

#### Scenario: Old ones stay while no migration comes

- **GIVEN** five pre-migration dumps, the newest of them 90 days old
- **WHEN** scheduled backups run
- **THEN** all five remain

#### Scenario: A failed dump prunes nothing

- **GIVEN** five pre-migration dumps
- **WHEN** a migration's dump fails its read-back
- **THEN** all five remain, and the migration does not run
