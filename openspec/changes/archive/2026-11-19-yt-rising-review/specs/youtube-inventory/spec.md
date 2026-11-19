# Spec Delta

## MODIFIED Requirements

### Requirement: Review Values

The system SHALL accept exactly these review values for YouTube. Status: `candidate`, `seed`, `maybe`, `rejected`. Kind: `personal`, `company`, `media`, `event`, `education`. Rejection reason: `not_it`, `adjacent`, `crypto`, `infobiz`, `ads`, `content_farm`, `consumer_tech`. Language: `en`, `ru`, `other`. Discovery source: `manual`, `telegram`, `mention`, `featured`, `search`, `chart`.

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

#### Scenario: A chart offer records `chart`

- **WHEN** the rising selection offers a channel of the day's chart
- **THEN** the record it creates carries the discovery source `chart`
- **AND** no option of `itgraph yt add` records it
