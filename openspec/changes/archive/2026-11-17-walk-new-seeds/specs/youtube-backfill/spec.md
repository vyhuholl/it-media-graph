# Spec Delta

## REMOVED Requirements

### Requirement: Unattended Walk

**Reason**: The unattended walk runs every hour rather than once a day, so that a new seed is walked within the hour of being marked. Replaced by "Hourly Unattended Walk", which keeps the rest of what this requirement said.

**Migration**: Nothing for an operator to do. The role's default schedule for `itgraph-yt-backfill` changes with the release, and the walk itself is the same.

## ADDED Requirements

### Requirement: Hourly Unattended Walk

The system SHALL run a walk of the due seeds every hour on the collection host, without an operator, whenever an API key is configured there and the walk is enabled, and SHALL schedule no walk otherwise. A walk started by hand SHALL run whether the walk is enabled or not.

#### Scenario: An enabled host walks hourly

- **GIVEN** a collection host deployed with an API key and the walk enabled
- **THEN** a walk runs there every hour
- **AND** a run missed while the host was down runs when it is back, if that is within the hour; otherwise the next hour's run takes its place

#### Scenario: A new seed is walked within the hour

- **GIVEN** a channel marked as a seed, with its payload stored, and the walk enabled
- **WHEN** the next hourly walk runs
- **THEN** its uploads are walked back to the configured depth

#### Scenario: Nothing due spends nothing

- **GIVEN** every seed already walked
- **WHEN** the hourly walk runs
- **THEN** no request is made to YouTube and no unit is booked

#### Scenario: A host with the walk disabled schedules none

- **GIVEN** a collection host deployed with an API key and the walk disabled
- **THEN** no walk is scheduled there

#### Scenario: A walk by hand while disabled

- **GIVEN** the walk disabled
- **WHEN** the operator walks named channels
- **THEN** they are walked, and what is stored stays as it was read
