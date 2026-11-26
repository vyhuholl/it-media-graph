# Spec Delta

## MODIFIED Requirements

### Requirement: Nothing Is Removed By Age

The system SHALL NOT remove YouTube data because of its age: channel and video payloads, readings, lookups, featured readings, search runs and results, the walk's records, and video embeddings stay until YouTube stops returning the object or the operator removes them. An answer that decides what to ask YouTube next — a lookup, a featured reading, a search run and its results — SHALL be read only while younger than `youtube_answers_fresh_days` (30 by default); an older one is kept unread and replaced when asked again.

#### Scenario: An old payload is kept

- **GIVEN** a channel whose payload was fetched 90 days ago
- **WHEN** any YouTube command runs
- **THEN** the payload is still there, with the channel record and its review

#### Scenario: Old videos and readings are kept

- **GIVEN** a video whose payload was fetched 31 days ago, with readings taken 32 and 31 days ago
- **WHEN** any YouTube command runs
- **THEN** the video, both readings and its embedding are still there

#### Scenario: An old lookup is kept and not read

- **GIVEN** a lookup fetched 31 days ago
- **WHEN** what is answered is read
- **THEN** that lookup is not among the answers
- **AND** it is still stored, until the name is looked up again and the answer replaced

#### Scenario: An old featured list and search run are kept and not read

- **GIVEN** a featured reading fetched 31 days ago and a search run made 31 days ago
- **WHEN** a candidates report is made
- **THEN** neither is read as evidence
- **AND** both are still stored, the run with its results

#### Scenario: Walk records are kept

- **GIVEN** a walk that ended 90 days ago
- **WHEN** any YouTube command runs, whether or not the walk is enabled
- **THEN** its record remains

### Requirement: Videos That Disappear

The system SHALL remove at once a tracked video YouTube no longer returns when asked for by id, with its readings and its embedding. One missing video MUST NOT cost the other videos in the same request their reading.

#### Scenario: A deleted or private video

- **GIVEN** a tracked video with readings
- **WHEN** a request for it is answered without it
- **THEN** its payload, every reading of it and its embedding are removed

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
