# Spec Delta

## MODIFIED Requirements

### Requirement: Polling Is Sequential And Paced

The system SHALL poll one channel at a time, with a gap before each request, and MUST NOT issue concurrent requests to Telegram. When no channel is due, the loop MAY ask for one page of a new seed's history instead of waiting (message-backfill, "A Walk Yields To Due Polls"); that page is a request like any other here, sequential and preceded by a gap.

#### Scenario: Channels are polled one at a time

- **GIVEN** several channels due at the same moment
- **WHEN** the loop polls them
- **THEN** their requests are issued sequentially
- **AND** no two requests are in flight at once

#### Scenario: Every request is preceded by a gap

- **WHEN** the loop issues a history request
- **THEN** it waits first, using the same pacing every other command uses

#### Scenario: An empty queue costs no requests

- **GIVEN** no channel is due and no seed awaits its walk
- **WHEN** the loop ticks
- **THEN** no request is issued
- **AND** the loop waits and checks again

#### Scenario: An empty queue and a seed to walk

- **GIVEN** no channel is due and a seed awaits its walk
- **WHEN** the loop ticks
- **THEN** it asks for one page of that seed's history, and for nothing else at the same time
