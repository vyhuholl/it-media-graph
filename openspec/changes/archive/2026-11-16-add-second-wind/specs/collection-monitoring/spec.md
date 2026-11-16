# Spec Delta

## MODIFIED Requirements

### Requirement: Metrics Carry Nothing YouTube Said

No series SHALL carry a title, handle, description, channel id, video id or statistic read from YouTube. What the exporter reads of YouTube's tables SHALL be limited to when each row was fetched, enforced by the database role it connects as. Of a YouTube alert it SHALL read only its moments and delivery state; of a baseline run of either platform, only when it completed and how many channels it counted; of a YouTube weekly list, only its language and its moments, and of a Telegram weekly list only its moments, and nothing of either's entries or second wind entries; of a refresh's record, only when it ended and how.

#### Scenario: The role cannot read a title

- **GIVEN** the exporter's database role
- **WHEN** it selects `payload` from any `yt_raw_*` table
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what an alert is about

- **GIVEN** the exporter's database role
- **WHEN** it selects the video, the reading or the score of a YouTube alert
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read a baseline

- **GIVEN** the exporter's database role
- **WHEN** it selects a channel's reference, a curve point or a factor of either platform
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what a list holds

- **GIVEN** the exporter's database role
- **WHEN** it selects any column of a weekly list's entries or second wind entries of either platform, or a list's week, counts or parameters
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read a refresh's counts

- **GIVEN** the exporter's database role
- **WHEN** it selects how many late readings a refresh found due or took, or its error
- **THEN** the database refuses with a permission error

#### Scenario: The role can write nothing

- **WHEN** the exporter's role attempts an insert, update or delete on any table
- **THEN** the database refuses

#### Scenario: A label names no channel

- **WHEN** a client scrapes
- **THEN** no label value in the body names a Telegram or YouTube channel, a video or a person
