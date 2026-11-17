# Spec Delta

## MODIFIED Requirements

### Requirement: Quota-Free History Walk

The system SHALL walk a channel's history without issuing any request that carries a per-day quota. In particular a walk MUST NOT resolve a username. `contacts.resolveUsername` SHALL be issued only by the resolution pass, by `itgraph add`, and by the poll loop resolving the head of the rising ranking under its cap — never by a walk, a poll or the metadata pass.

#### Scenario: The peer comes from the session's own cache

- **GIVEN** an in-scope channel whose peer the session can supply
- **WHEN** backfill walks it
- **THEN** the peer is obtained from the session's entity cache
- **AND** no username is resolved

#### Scenario: A walk resolves no username, whatever the metadata state

- **GIVEN** an in-scope channel with no stored extended information
- **WHEN** backfill walks it
- **THEN** no username resolution is requested
- **AND** no extended-information request is made
- **AND** its history is fetched

#### Scenario: A channel with no cached peer is skipped rather than resolved

- **GIVEN** an in-scope channel whose peer the session cannot supply
- **WHEN** backfill walks it
- **THEN** no username resolution is requested
- **AND** the channel is recorded as skipped, with the reason
- **AND** the run continues with the remaining channels

#### Scenario: Resolution stays confined to its own command

- **WHEN** a backfill run completes, whatever it skipped or failed on
- **THEN** it has issued no username resolution

