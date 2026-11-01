# youtube-featured-channels Specification

## Purpose
The channels a seed lists in its channel sections: the request that reads them, when it is made, where the answer lives for at most 30 days, and the review queue built from them. Featuring a channel on one's own page is a deliberate recommendation, so one seed is enough evidence by default.

## Requirements

### Requirement: Featured Lists Are Read With The Refresh

`itgraph yt refresh` SHALL read the channel sections of every seed not marked gone whose featured list is absent or older than the refresh age, one channel to a request, from the general pool. A channel that is not a seed SHALL NOT be asked about.

#### Scenario: A seed without a list

- **GIVEN** a seed whose featured list was never read
- **WHEN** the refresh runs
- **THEN** its sections are asked for once, and its list is stored

#### Scenario: A fresh list costs nothing

- **GIVEN** a seed whose list is younger than the refresh age
- **WHEN** the refresh runs
- **THEN** no request is made for its sections

#### Scenario: Other statuses are not asked about

- **GIVEN** a `candidate`, a `maybe` and a `rejected` channel
- **WHEN** the refresh runs
- **THEN** no request is made for their sections

#### Scenario: Exhaustion stops the lists part way

- **WHEN** the quota is exhausted while lists are read
- **THEN** the lists stored before it are kept
- **AND** the next run continues with those still due

### Requirement: Any Section's Channels Are Featured

The system SHALL take as a seed's featured list every channel id listed in any of its sections, whatever the section's type, and SHALL NOT read the type. A seed whose sections list no channel, or for whose channel YouTube returns no sections, SHALL be stored with an empty list.

#### Scenario: A shelf among other sections

- **GIVEN** a seed with eight sections, one of which lists two channel ids
- **WHEN** its sections are read
- **THEN** its featured list holds those two ids

#### Scenario: Sections of an undocumented type

- **GIVEN** a seed whose sections carry a type the reference does not list
- **WHEN** its sections are read
- **THEN** they are read like any other, and only their channel ids are kept

#### Scenario: No shelf

- **GIVEN** a seed whose sections list no channel
- **WHEN** its sections are read
- **THEN** its list is stored empty, and it is not asked about again before the refresh age

### Requirement: Featured Evidence

The system SHALL count, for each channel, the distinct seeds whose stored featured lists name it. A seed's own channel in its list SHALL NOT count.

#### Scenario: Two seeds featuring one channel

- **GIVEN** two seeds whose lists both name `UCfakeChannel0000000000b`
- **WHEN** the queue is read
- **THEN** that channel is featured by two seeds

#### Scenario: A list naming the seed itself

- **GIVEN** a seed whose list names its own channel id
- **WHEN** the queue is read
- **THEN** that id is no evidence

### Requirement: Featured Ranking

The system SHALL rank the channels absent from the inventory, whatever a record's status, gone included, by the number of distinct seeds featuring them, most first, then by channel id. A channel featured by fewer seeds than the minimum, by default one, SHALL NOT be ranked.

#### Scenario: More seeds rank first

- **GIVEN** a channel featured by two seeds and another by one
- **WHEN** the ranking runs
- **THEN** the channel featured by two seeds is first

#### Scenario: Known channels are never ranked

- **GIVEN** featured channels of which one is a `seed`, one `maybe`, one `rejected`, one `candidate` and one marked gone
- **WHEN** the ranking runs
- **THEN** none of them is ranked

### Requirement: Featured Candidates Report

The system SHALL provide `itgraph yt featured-candidates`, reporting how many seeds have a list in date, how many await one, the top of the ranking with each channel's id, seed count and the seeds' ids, and the rest in one line. It SHALL show no data read from the API beyond channel ids, and make no request.

#### Scenario: A ranked row

- **GIVEN** a channel featured by one seed
- **WHEN** the report runs
- **THEN** its row shows its channel id, one seed and that seed's id
- **AND** no title or handle

#### Scenario: Seeds without a list

- **GIVEN** seeds whose lists have not been read yet
- **WHEN** the report runs
- **THEN** it states how many, pointing to `itgraph yt refresh`

### Requirement: Featured Candidates Are Added On Request

With `--add`, `itgraph yt featured-candidates` SHALL create a record for each ranked channel shown, 100 by default, with status `candidate` and discovery source `featured`. It SHALL refuse, writing nothing, while any seed not marked gone lacks a list in date.

#### Scenario: The top hundred are added

- **GIVEN** every seed with a list in date and 150 ranked channels
- **WHEN** the command runs with `--add`
- **THEN** the first 100 become `candidate` records with discovery source `featured`

#### Scenario: A missing list refuses the cut

- **GIVEN** a seed whose list has not been read
- **WHEN** the command runs with `--add`
- **THEN** it fails, saying how many seeds lack a list and to run `itgraph yt refresh`
- **AND** no record is created

### Requirement: Nothing Joined From Featured Lists Is Stored

The system SHALL work the featured evidence and the ranking out of the stored lists each time it is asked, and MUST NOT store which seeds feature a channel, a count or a rank.

#### Scenario: Reporting writes nothing

- **WHEN** the report runs without `--add`
- **THEN** no row in the database is inserted, updated or deleted, apart from what expiry removes before any command

### Requirement: Featured Candidates Are Not Scheduled

No scheduled job SHALL run `itgraph yt featured-candidates`.

#### Scenario: The deployed jobs

- **WHEN** the deploy's manifests are rendered with an API key
- **THEN** no scheduled job runs `yt featured-candidates`

### Requirement: Featured Logs Name Ids Only

Log records written while reading sections or ranking SHALL identify channels by id only.

#### Scenario: Reading sections at debug level

- **GIVEN** logging at debug level
- **WHEN** the refresh reads sections
- **THEN** no log record contains a section's title or a channel's title or handle
