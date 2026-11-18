# Spec Delta

## MODIFIED Requirements

### Requirement: Featured Lists Are Read With The Refresh

`itgraph yt refresh` SHALL read the channel sections of every seed not marked gone whose featured list is absent or older than `youtube_sections_after_days`, one channel to a request, from the general pool. A channel that is not a seed SHALL NOT be asked about. Every read SHALL append a reading — the channel ids the sections list, and the moment — and SHALL NOT replace, change or remove one already stored.

#### Scenario: A seed without a list

- **GIVEN** a seed whose featured list was never read
- **WHEN** the refresh runs
- **THEN** its sections are asked for once, and its first reading is stored

#### Scenario: A fresh list costs nothing

- **GIVEN** a seed whose newest reading is younger than `youtube_sections_after_days`
- **WHEN** the refresh runs
- **THEN** no request is made for its sections

#### Scenario: Other statuses are not asked about

- **GIVEN** a `candidate`, a `maybe` and a `rejected` channel
- **WHEN** the refresh runs
- **THEN** no request is made for their sections

#### Scenario: A second reading does not replace the first

- **GIVEN** a seed with one reading, and a newer read of its sections
- **WHEN** the refresh stores it
- **THEN** both readings are there, each with its own moment, and the older is unchanged

#### Scenario: Exhaustion stops the lists part way

- **WHEN** the quota is exhausted while lists are read
- **THEN** the readings stored before it are kept
- **AND** the next run continues with those still due

### Requirement: Featured Evidence

The system SHALL count, for each channel, the distinct seeds whose newest in-date featured reading names it, a reading older than `youtube_answers_fresh_days` naming nobody. A seed's own channel in its list SHALL NOT count.

#### Scenario: Two seeds featuring one channel

- **GIVEN** two seeds whose newest readings both name `UCfakeChannel0000000000b`
- **WHEN** the queue is read
- **THEN** that channel is featured by two seeds

#### Scenario: A list naming the seed itself

- **GIVEN** a seed whose newest reading names its own channel id
- **WHEN** the queue is read
- **THEN** that id is no evidence
