## ADDED Requirements

### Requirement: Derivation Scope

The system SHALL derive edges only from channels that are in scope, and MUST NOT treat an out-of-scope channel or a discussion chat as a source of references.

#### Scenario: Sources are selected by status
- **GIVEN** channels holding stored history in each of the inventory statuses
- **WHEN** derivation runs
- **THEN** only channels with status `candidate`, `seed` or `maybe` are read as sources
- **AND** rejected channels are not read

#### Scenario: Discussion chats are never sources
- **GIVEN** stored messages belonging to a discussion chat
- **WHEN** derivation runs
- **THEN** no edge is recorded with that chat as its source

#### Scenario: An out-of-scope source discovers nothing
- **GIVEN** stored history for a channel that is out of scope
- **WHEN** derivation runs
- **THEN** no channel enters the inventory through its references
- **AND** no username from its messages is recorded as pending

#### Scenario: Out-of-scope channels remain valid targets
- **GIVEN** an in-scope channel referencing a rejected one
- **WHEN** derivation runs
- **THEN** the edge is recorded

#### Scenario: A scope change takes effect on rebuild
- **GIVEN** edges previously derived from a channel that has since been rejected
- **WHEN** derivation runs without a rebuild
- **THEN** those edges remain
- **AND** running it with a rebuild removes them

#### Scenario: Channels discovered through a source later rejected are retained
- **GIVEN** a channel that entered the inventory through a source since rejected
- **WHEN** derivation runs with a rebuild
- **THEN** that channel's record is retained with its original discovery source

## MODIFIED Requirements

### Requirement: Discovery Through References

The system SHALL add every channel referenced from an in-scope source that is not yet known to the inventory, and MUST NOT alter records that already exist.

#### Scenario: An unknown channel is discovered
- **GIVEN** an in-scope source referencing a channel absent from the inventory
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