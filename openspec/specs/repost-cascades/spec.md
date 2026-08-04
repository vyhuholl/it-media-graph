# repost-cascades Specification

## Purpose
TBD - created by archiving change add-alert-bot. Update Purpose after archive.
## Requirements
### Requirement: A Cascade Is Distinct Unaffiliated Sources

The system SHALL measure how far a post has travelled by the number of distinct affiliation families observed carrying it, not by the number of reposts. A family that carries a post repeatedly SHALL count once.

#### Scenario: Distinct families are counted

- **GIVEN** a post carried by channels belonging to three different families
- **WHEN** the cascade is measured
- **THEN** its value is three

#### Scenario: One family carrying a post repeatedly counts once

- **GIVEN** a post carried several times by channels of a single family
- **WHEN** the cascade is measured
- **THEN** its value is one

#### Scenario: Two channels of one family are one source

- **GIVEN** two channels confirmed to share an author, both carrying a post
- **WHEN** the cascade is measured
- **THEN** they contribute one to the value

#### Scenario: An unaffiliated channel is its own family

- **GIVEN** a channel belonging to no confirmed family
- **WHEN** it carries a post
- **THEN** it contributes one to the value

### Requirement: A Post's Own Family Does Not Count

The system SHALL exclude reposts made by the publishing channel's own family. A network distributing its own post across the channels one author runs is distribution, not travel.

#### Scenario: A self-repost is not a cascade

- **GIVEN** a post carried only by channels sharing an author with the one that published it
- **WHEN** the cascade is measured
- **THEN** its value is zero

#### Scenario: Outside carriers still count

- **GIVEN** a post carried both by its own family and by two unrelated families
- **WHEN** the cascade is measured
- **THEN** its value is two

### Requirement: Album Parts Are One Post

The system SHALL treat the parts of an album as a single post, both as the thing carried and as the thing alerted about.

#### Scenario: An album is one alert, not several

- **GIVEN** an album published as several messages and carried elsewhere
- **WHEN** it crosses a band
- **THEN** one alert is raised for the album

#### Scenario: The alert names the album's first part

- **WHEN** an alert about an album is raised
- **THEN** the post it names is the part a link to the album resolves to

### Requirement: The Window Bounds What Counts

The system SHALL count only reposts that occurred within a configured window of the post's publication, so that the measure describes what is travelling now rather than what has ever travelled.

#### Scenario: A repost inside the window counts

- **GIVEN** a post carried by a family within the configured window
- **WHEN** the cascade is measured
- **THEN** that family is counted

#### Scenario: A repost after the window does not

- **GIVEN** a post carried by a family long after the window closed
- **WHEN** the cascade is measured
- **THEN** that family is not counted

#### Scenario: A post older than the window raises nothing

- **GIVEN** a corpus of posts published long before the pass first ran
- **WHEN** the pass runs for the first time
- **THEN** no alert is raised about them
- **AND** no record of already-handled posts was needed to achieve this

#### Scenario: A repost recorded before its original is ignored

- **GIVEN** an edge whose repost predates the post it refers to
- **WHEN** the cascade is measured
- **THEN** it does not count toward the value

### Requirement: Detection Reads Only Derived Data

The system SHALL detect cascades from the derived edges alone. It MUST issue no network request, MUST NOT read metric snapshots, and MUST NOT modify the raw layer, the inventory or the edges.

#### Scenario: No request is made

- **WHEN** the detection pass runs
- **THEN** no Telegram request is issued
- **AND** it does not acquire the session lease

#### Scenario: Snapshots are not consulted

- **WHEN** the detection pass runs
- **THEN** it reads no metric snapshots
- **AND** a cascade is detected identically whether or not any exist

#### Scenario: Collected data is untouched

- **WHEN** the detection pass runs
- **THEN** no raw message, edge, channel record or collection state is modified

### Requirement: Detection Is Re-runnable

The system SHALL produce the same alerts from the same edges however many times it runs, so that the pass is safe to put on a short schedule.

#### Scenario: A second run writes nothing

- **GIVEN** a completed detection pass
- **WHEN** it is run again over unchanged edges
- **THEN** no alert is added or altered

#### Scenario: New edges produce new alerts

- **GIVEN** a completed detection pass
- **WHEN** derivation adds a repost that takes a post over a band
- **AND** the pass runs again
- **THEN** an alert is raised for that band

### Requirement: Thresholds Are Parameters

The system SHALL take the bands and the window as configuration, with defaults chosen from the measured rate rather than from intuition.

#### Scenario: Bands are settable

- **GIVEN** a configured set of bands
- **WHEN** a post crosses one of them
- **THEN** an alert is raised for that band and no other

#### Scenario: The window is settable

- **GIVEN** a configured window
- **WHEN** the cascade is measured
- **THEN** only reposts inside that window are counted

#### Scenario: Defaults are usable

- **WHEN** the pass runs with no thresholds given
- **THEN** it uses defaults that produce alerts at the measured rate rather than at the rate of noise

### Requirement: The Freshness Of The Evidence Is Reported

The system SHALL report how recent the derived edges it read are. An alerting system whose normal state is silence MUST make "nothing has happened" distinguishable from "nothing has been derived".

#### Scenario: The pass reports the age of its evidence

- **WHEN** the detection pass finishes
- **THEN** it reports how recent the newest edge it considered was

#### Scenario: Stale evidence is reported rather than assumed fresh

- **GIVEN** derivation has not run for some time
- **WHEN** the detection pass runs
- **THEN** it still completes
- **AND** it reports that its evidence is stale

