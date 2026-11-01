## MODIFIED Requirements

### Requirement: Review Values

The system SHALL accept exactly these review values for YouTube. Status: `candidate`, `seed`, `maybe`, `rejected`. Kind: `personal`, `company`, `media`, `event`, `education`. Rejection reason: `not_it`, `adjacent`, `crypto`, `infobiz`, `ads`, `content_farm`, `consumer_tech`. Language: `en`, `ru`, `other`. Discovery source: `manual`, `telegram`, `mention`, `featured`, `search`.

#### Scenario: A value outside the set is refused

- **WHEN** a review names a kind, reason or language outside its set
- **THEN** the review fails, naming the value and the accepted ones
- **AND** nothing is written

#### Scenario: Telegram's values are unaffected

- **WHEN** a Telegram channel is reviewed
- **THEN** it accepts the Telegram kinds and rejection reasons as before
- **AND** none of the YouTube-only values

#### Scenario: Only the ranking of Telegram links records `telegram`

- **WHEN** a channel is added with `itgraph yt add`
- **THEN** its discovery source is `manual`
- **AND** no option of that command records `telegram`

#### Scenario: Only a queue records its own source

- **WHEN** candidates are added from the mentions, featured lists or search
- **THEN** their discovery source is `mention`, `featured` or `search` respectively
- **AND** no option of `itgraph yt add` records any of the three

### Requirement: Review File Export

The system SHALL provide `itgraph yt review-export`, writing a tab-separated review file of the channels awaiting review to standard output or a path. Each row SHALL hold the channel id, the editable review columns filled with the current values, and read-only columns from the channel's API data and its provenance. A header SHALL state the export moment and the accepted values.

#### Scenario: Unreviewed channels are exported by default

- **WHEN** the command runs without a status filter
- **THEN** every channel with no review is exported, and no other

#### Scenario: Undecided channels can be exported for another look

- **WHEN** the command runs filtered to `maybe`
- **THEN** those channels are exported with their current kind, language and note filled in

#### Scenario: The file says what may be written in it

- **WHEN** a file is exported
- **THEN** its header states the export moment in UTC
- **AND** lists the accepted decisions, kinds, languages and rejection reasons
- **AND** points to the review guidelines in the README

#### Scenario: The file gives what a reviewer needs to recognise a channel

- **WHEN** a channel with stored API data is exported
- **THEN** its row holds the handle, title, subscriber count, the language and country the channel declares, and a link to the channel
- **AND** a channel without stored API data is exported with its id and link only

#### Scenario: The file says how a channel was found

- **WHEN** a channel is exported
- **THEN** its row's `source` column holds its discovery source
- **AND** for a channel found by search, also the query of the earliest run in date that returned it
- **AND** no count of seeds, families or results

#### Scenario: Exporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made
