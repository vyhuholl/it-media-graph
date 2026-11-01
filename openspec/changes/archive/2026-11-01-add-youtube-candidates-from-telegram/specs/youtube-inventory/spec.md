## MODIFIED Requirements

### Requirement: Review Values

The system SHALL accept exactly these review values for YouTube. Status: `candidate`, `seed`, `maybe`, `rejected`. Kind: `personal`, `company`, `media`, `event`, `education`. Rejection reason: `not_it`, `adjacent`, `crypto`, `infobiz`, `ads`, `content_farm`, `consumer_tech`. Language: `en`, `ru`, `other`. Discovery source: `manual`, `telegram`.

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
