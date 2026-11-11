# Spec Delta

## MODIFIED Requirements

### Requirement: Channels That Disappear

The system SHALL treat a channel YouTube no longer returns when asked for by id, by any command, as gone: its payload SHALL be removed and the time recorded, and its record and review SHALL remain. A gone channel that YouTube returns again SHALL be restored.

#### Scenario: A channel YouTube no longer returns

- **GIVEN** a channel whose id YouTube answers with no channel
- **WHEN** a refresh asks for it
- **THEN** its payload is removed
- **AND** it is marked gone, with the time
- **AND** its record and review remain

#### Scenario: The poll loop finds a channel gone

- **GIVEN** a seed channel whose id YouTube answers with no channel
- **WHEN** the poll loop asks for it
- **THEN** it is marked gone exactly as a refresh would mark it

#### Scenario: One missing channel does not cost the others their refresh

- **GIVEN** a request for several channel ids, one of which YouTube no longer returns
- **WHEN** a refresh makes it
- **THEN** every other channel in the request is refreshed
- **AND** only the missing one is marked gone

#### Scenario: A gone channel comes back

- **GIVEN** a channel marked gone
- **WHEN** a later refresh finds YouTube returning it again
- **THEN** its payload is stored and the gone mark is cleared

## ADDED Requirements

### Requirement: Videos That Disappear

The system SHALL remove at once a tracked video YouTube no longer returns when asked for by id, with its readings. One missing video MUST NOT cost the other videos in the same request their reading.

#### Scenario: A deleted or private video

- **GIVEN** a tracked video with readings
- **WHEN** a request for it is answered without it
- **THEN** its payload and every reading of it are removed

#### Scenario: One missing video in a batch

- **GIVEN** a request for fifty videos, one of which YouTube no longer returns
- **WHEN** the answer arrives
- **THEN** the other forty-nine are read
- **AND** only the missing one is removed

#### Scenario: A batch answered not found

- **GIVEN** a request for several videos answered not found as a whole
- **WHEN** the poll loop handles it
- **THEN** each video in it is asked for alone
- **AND** only those YouTube does not return are removed
