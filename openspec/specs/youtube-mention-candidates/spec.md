# youtube-mention-candidates Specification

## Purpose
The review queue built from what the seeds' own titles and descriptions reference, once each reference names a channel through what is stored and the lookups. It owns how a reference names a channel, what is dropped, how the channels outside the inventory are ranked and cut, the report and the records it adds — all worked out on reading and never stored.

## Requirements

### Requirement: Evidence Is The Seeds' Videos

The system SHALL take the evidence for a channel from the references of the mention graph made in seeds' videos, in every form it follows. A reference made by a featured list SHALL NOT count here.

#### Scenario: A handle link and a video link

- **GIVEN** one seed's video linking `youtube.com/@fake-unknown` and another seed's video linking a video of the same channel
- **WHEN** the queue is read
- **THEN** both references count as evidence for that channel

#### Scenario: A featured channel is not evidence here

- **GIVEN** a channel outside the inventory listed only in a seed's featured list
- **WHEN** the queue is read
- **THEN** it has no evidence in this queue

### Requirement: A Reference Names A Channel Through What Is Stored

The system SHALL take a channel id reference as that channel, and a handle, legacy name or video reference as the channel a stored channel payload, a stored video payload or a lookup in date names. A reference nothing stored answers SHALL await a lookup; one looked up as not found SHALL name no channel.

#### Scenario: A video named by its lookup

- **GIVEN** a seed's video linking an unknown video whose lookup names `UCfakeChannel0000000000b`
- **WHEN** the queue is read
- **THEN** the reference names `UCfakeChannel0000000000b`

#### Scenario: A lookup made for Telegram serves here too

- **GIVEN** a handle looked up for a Telegram link, still in date
- **AND** a seed's video linking the same handle
- **WHEN** the queue is read
- **THEN** the reference names the channel that lookup names, and no lookup is awaited

#### Scenario: A reference not yet looked up

- **GIVEN** a seed's video linking a handle with no stored payload and no lookup
- **WHEN** the queue is read
- **THEN** the handle awaits a lookup and names no channel

#### Scenario: A reference looked up as not found

- **GIVEN** a seed's video linking a video looked up as not found
- **WHEN** the queue is read
- **THEN** it names no channel, and is counted as not found

### Requirement: A Reference To Its Own Channel Is Dropped

The system SHALL drop a reference that names the channel of the video making it, once its lookup has named that channel.

#### Scenario: An old episode of the same channel

- **GIVEN** a seed's video linking one of the seed's own videos older than anything stored
- **AND** its lookup names the seed
- **WHEN** the queue is read
- **THEN** the reference is no evidence for any channel

### Requirement: Distinct Seeds Are The Unit Of Evidence

The system SHALL count, for each channel, the distinct seeds whose videos reference it, in any form. References within one seed SHALL count once.

#### Scenario: Two forms, two seeds

- **GIVEN** one seed linking `youtube.com/@fake-unknown` and another linking a video, both naming `UCfakeChannel0000000000b`
- **WHEN** the queue is read
- **THEN** `UCfakeChannel0000000000b` is referenced by two seeds

#### Scenario: One seed referencing repeatedly

- **GIVEN** one seed referencing the same channel in five of its videos, by handle and by video
- **WHEN** the queue is read
- **THEN** that channel is referenced by one seed

### Requirement: Mention Ranking

The system SHALL rank the channels absent from the inventory, whatever a record's status, gone included, by the number of distinct seeds referencing them, most first, then by channel id. A channel referenced by fewer seeds than the minimum, by default two, SHALL NOT be ranked. No other number SHALL order or cut it.

#### Scenario: More seeds rank first

- **GIVEN** a channel referenced by three seeds and another by two
- **WHEN** the ranking runs
- **THEN** the channel referenced by three seeds is first

#### Scenario: A tie goes by id

- **GIVEN** two channels referenced by two seeds each
- **WHEN** the ranking runs twice over unchanged data
- **THEN** both runs order them by channel id

#### Scenario: Known channels are never ranked

- **GIVEN** channels referenced by many seeds, one of them a `seed`, one `maybe`, one `rejected`, one `candidate` and one marked gone
- **WHEN** the ranking runs
- **THEN** none of them is ranked

#### Scenario: One seed is enough when asked

- **GIVEN** a channel referenced by one seed
- **WHEN** the ranking runs with a minimum of one
- **THEN** it is ranked

### Requirement: Mention Candidates Report

The system SHALL provide `itgraph yt mention-candidates`, reporting what was read, the top of the ranking, the rest in one line by seed count, and how many references await a lookup or were not found. Each ranked row SHALL show the channel id, its seed count and the seeds' ids. The report SHALL show no data read from the API.

#### Scenario: A ranked row

- **GIVEN** a channel referenced by two seeds
- **WHEN** the report runs
- **THEN** its row shows its channel id, two seeds and both seeds' ids
- **AND** no handle, title or other data read from the API

#### Scenario: The top and the rest

- **GIVEN** 150 ranked channels
- **WHEN** the report runs with a top of 100
- **THEN** 100 rows are shown, and one line says how many more are ranked, by seed count

#### Scenario: What could not be counted

- **WHEN** the report runs
- **THEN** it states how many references await a lookup, pointing to `itgraph yt mention-lookup`, and how many were not found

#### Scenario: Without a key

- **GIVEN** no API key configured
- **WHEN** the report runs
- **THEN** it completes and makes no request

### Requirement: Mention Candidates Are Added On Request

With `--add`, `itgraph yt mention-candidates` SHALL create a record for each ranked channel shown, 100 by default, with status `candidate` and discovery source `mention`, unreviewed and without API data. It SHALL refuse, writing nothing, while any reference awaits a lookup.

#### Scenario: The top hundred are added

- **GIVEN** 150 ranked channels and nothing awaiting a lookup
- **WHEN** the command runs with `--add`
- **THEN** the first 100 become `candidate` records with discovery source `mention`
- **AND** no request is made

#### Scenario: An incomplete lookup refuses the cut

- **GIVEN** references awaiting a lookup
- **WHEN** the command runs with `--add`
- **THEN** it fails, saying how many await one and to run `itgraph yt mention-lookup`
- **AND** no record is created

#### Scenario: The next run brings the next channels

- **GIVEN** a previous `--add` whose candidates have since been reviewed or left unreviewed
- **WHEN** the command runs with `--add` again
- **THEN** none of them is added again

### Requirement: Nothing Joined From Mentions Is Stored

The system SHALL work the references, the channels they name and the ranking out of the stored payloads and the lookups each time it is asked, and MUST NOT store which seeds reference a channel, a count, a rank or anything else joined from them. A record created by `--add` holds only what any record holds.

#### Scenario: Reporting writes nothing

- **WHEN** the report runs without `--add`
- **THEN** no row in the database is inserted, updated or deleted, apart from what expiry removes before any command

#### Scenario: An expired lookup changes the next report

- **GIVEN** a channel ranked through a video's lookup
- **WHEN** that lookup expires and the report runs before the next lookup run
- **THEN** that video awaits a lookup, and the channel loses that reference

### Requirement: Mention Candidates Are Not Scheduled

No scheduled job SHALL run `itgraph yt mention-candidates`.

#### Scenario: The deployed jobs

- **WHEN** the deploy's manifests are rendered with an API key
- **THEN** no scheduled job runs `yt mention-candidates`

### Requirement: Mention Candidates Logs Name Ids Only

Log records written while reading the queue SHALL identify channels and videos by id only, and MUST NOT carry a handle, a legacy name or a link.

#### Scenario: Ranking at debug level

- **GIVEN** logging at debug level
- **WHEN** the report runs over videos linking handles
- **THEN** no log record contains a handle, a legacy name or a link
