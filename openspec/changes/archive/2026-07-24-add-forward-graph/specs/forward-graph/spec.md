## ADDED Requirements

### Requirement: Derivation Is Rebuildable

The system SHALL derive all graph data from the raw layer alone, and MUST NOT modify the raw layer while doing so.

#### Scenario: Rebuilding is repeatable
- **GIVEN** a derived graph built from the raw layer
- **WHEN** derivation runs again over the same raw data
- **THEN** the resulting edges are the same as before
- **AND** no duplicate edges exist

#### Scenario: Derivation reads only raw data
- **WHEN** derivation runs
- **THEN** its output depends only on stored payloads and the inventory
- **AND** no previously derived state is required for it to produce a complete result

#### Scenario: Raw data is untouched
- **WHEN** derivation runs
- **THEN** no stored payload is modified or deleted

### Requirement: Forward Edges

The system SHALL record an edge for every message forwarded from one channel into another.

#### Scenario: Forward from a channel
- **GIVEN** a stored message forwarded from another channel
- **WHEN** derivation runs
- **THEN** an edge of kind `forward` is recorded from the referencing channel to the referenced one
- **AND** the edge carries the referencing message's id and publication date

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

The system SHALL record an edge for every reference to a channel by username or by `t.me` link in a message.

#### Scenario: Username mention
- **GIVEN** a stored message containing an `@username` mention entity
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to the referenced channel

#### Scenario: Link to a channel
- **GIVEN** a stored message containing a `t.me` link to a channel
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to that channel

#### Scenario: Link to a single message
- **GIVEN** a `t.me` link pointing at one message within a channel
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to the channel

#### Scenario: References that are not channels
- **GIVEN** a mention or link that addresses a user, a bot, an invite, or a non-Telegram destination
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Repeated reference in one message
- **GIVEN** a stored message referencing the same channel more than once
- **WHEN** derivation runs
- **THEN** one edge of that kind is recorded for that message

### Requirement: Edges Record Observations

The system SHALL store one edge per observed reference, and MUST NOT store aggregated counts or weights.

#### Scenario: Repeated references over time
- **GIVEN** one channel referencing another in several messages
- **WHEN** derivation runs
- **THEN** each referencing message produces its own edge
- **AND** each edge carries the publication date of its message

#### Scenario: No precomputed weight
- **WHEN** an edge is stored
- **THEN** no weight, decay factor or aggregate count is stored with it

### Requirement: Discovery Through References

The system SHALL add every referenced channel that is not yet known to the inventory, and MUST NOT alter records that already exist.

#### Scenario: An unknown channel is discovered
- **GIVEN** a reference to a channel absent from the inventory
- **WHEN** derivation runs
- **THEN** it is added with status `candidate` and no review fields set
- **AND** its discovery source is `forward` or `mention` according to the reference

#### Scenario: A known channel keeps its provenance
- **GIVEN** a reference to a channel already in the inventory
- **WHEN** derivation runs
- **THEN** its discovery source and first-seen timestamp are unchanged

#### Scenario: A rejected channel is not reopened
- **GIVEN** a reference to a channel previously reviewed and rejected
- **WHEN** derivation runs
- **THEN** its status, rejection reason and review timestamp are unchanged
- **AND** it does not reappear in the review queue

#### Scenario: Every edge endpoint is known
- **WHEN** an edge is stored
- **THEN** both of its endpoints exist in the inventory

### Requirement: Reference Resolution

The system SHALL provide `itgraph resolve`, obtaining username and title for channels that entered the inventory by reference.

#### Scenario: An identifier is resolved
- **GIVEN** a channel discovered by reference and lacking a username
- **WHEN** resolution runs
- **THEN** its username and title are stored
- **AND** it is marked as resolved

#### Scenario: An identifier cannot be resolved
- **GIVEN** a channel that cannot be resolved — unknown to the collecting account, private, or deleted
- **WHEN** resolution runs
- **THEN** it is marked unresolvable with the reason
- **AND** later runs do not attempt it again

#### Scenario: Resolved channels are not revisited
- **GIVEN** channels already resolved
- **WHEN** resolution runs
- **THEN** no request is made for them

#### Scenario: Resolution obeys collection limits
- **WHEN** resolution runs
- **THEN** requests are paced and made one at a time
- **AND** a FloodWait is waited out rather than circumvented
- **AND** a channel limit may bound the run

#### Scenario: Derivation needs no network
- **WHEN** derivation runs
- **THEN** no request is made to Telegram