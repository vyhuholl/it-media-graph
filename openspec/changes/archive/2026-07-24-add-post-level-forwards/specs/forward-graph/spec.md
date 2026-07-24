## MODIFIED Requirements

### Requirement: Forward Edges

The system SHALL record an edge for every message forwarded from one channel into another, identifying the referenced message wherever the payload names it.

#### Scenario: Forward from a channel
- **GIVEN** a stored message forwarded from another channel
- **WHEN** derivation runs
- **THEN** an edge of kind `forward` is recorded from the referencing channel to the referenced one
- **AND** the edge carries the referencing message's id and publication date
- **AND** the edge carries the referenced message's id and its original publication date

#### Scenario: Forward naming no original message
- **GIVEN** a stored message forwarded from a channel whose payload does not name the original message
- **WHEN** derivation runs
- **THEN** the edge is recorded with its referenced-message fields left empty
- **AND** the edge is not discarded

#### Scenario: Forwarded album
- **GIVEN** several stored messages forming one forwarded album
- **WHEN** derivation runs
- **THEN** each message produces its own edge
- **AND** every one of those edges carries the same group identifier
- **AND** derivation does not merge them

#### Scenario: Forward from an individual
- **GIVEN** a stored message forwarded from a user rather than a channel
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Forward with a hidden origin
- **GIVEN** a stored message whose forward origin is withheld by its author's privacy settings
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Self-forward
- **GIVEN** a stored message a channel forwarded from itself
- **WHEN** derivation runs
- **THEN** no edge is recorded

### Requirement: Mention Edges

The system SHALL record an edge for every reference to a channel by username or by `t.me` link in a message, identifying the referenced message where the link names one.

#### Scenario: Username mention
- **GIVEN** a stored message containing an `@username` mention entity
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to the referenced channel
- **AND** its referenced-message fields are left empty

#### Scenario: Link to a channel
- **GIVEN** a stored message containing a `t.me` link to a channel
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to that channel
- **AND** its referenced-message fields are left empty

#### Scenario: Link to a single message
- **GIVEN** a `t.me` link pointing at one message within a channel
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to the channel
- **AND** the edge carries the referenced message's id

#### Scenario: References that are not channels
- **GIVEN** a mention or link that addresses a user, a bot, an invite, or a non-Telegram destination
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: The same post referenced twice in one message
- **GIVEN** a stored message referencing the same post of the same channel more than once
- **WHEN** derivation runs
- **THEN** one edge is recorded for that reference

#### Scenario: Different posts of one channel referenced in a single message
- **GIVEN** a stored message linking to two different posts of the same channel
- **WHEN** derivation runs
- **THEN** an edge is recorded for each referenced post

#### Scenario: A channel referenced both by name and by post link
- **GIVEN** a stored message that both mentions a channel by username and links to one of its posts
- **WHEN** derivation runs
- **THEN** two edges are recorded: one naming no post, one naming that post

### Requirement: Edges Record Observations

The system SHALL store one edge per observed reference, and MUST NOT store aggregated counts, weights or measures derived from its own fields.

#### Scenario: Repeated references over time
- **GIVEN** one channel referencing another in several messages
- **WHEN** derivation runs
- **THEN** each referencing message produces its own edge
- **AND** each edge carries the publication date of its message

#### Scenario: No precomputed measures
- **WHEN** an edge is stored
- **THEN** no weight, decay factor or aggregate count is stored with it
- **AND** no elapsed time between the referenced and referencing publication dates is stored, that interval being derivable from the two dates

#### Scenario: Grouping is recorded, not applied
- **WHEN** an edge comes from a message belonging to a group
- **THEN** the group identifier is stored on the edge
- **AND** derivation treats the edge no differently for belonging to a group