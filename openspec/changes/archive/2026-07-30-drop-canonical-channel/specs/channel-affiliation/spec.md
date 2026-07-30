## MODIFIED Requirements

### Requirement: Family Shape

A family SHALL be a set of channels, of any size, with no member privileged over another. It is the transitive closure of the confirmed pairs among its channels: two channels belong to the same family exactly when a chain of confirmed pairs connects them. A channel with no confirmed pair is a family of one.

#### Scenario: A family is a set with no head
- **GIVEN** a family of three channels
- **WHEN** the inventory is queried
- **THEN** all three are reported as belonging to one family
- **AND** none of them is distinguished as the family's main channel

#### Scenario: Any pair among the members establishes the same family
- **GIVEN** four channels confirmed as pairs A–B, A–C and D–B
- **WHEN** the family of any one of them is asked for
- **THEN** the answer names all four
- **AND** it does not depend on which pairs were confirmed, or in what order

#### Scenario: Confirming the same channels in a different order gives the same family
- **GIVEN** a set of pairs among several channels
- **WHEN** they are confirmed in any order
- **THEN** the resulting families are the same

#### Scenario: An unaffiliated channel is its own family
- **GIVEN** a channel with no confirmed pair
- **WHEN** its family is asked for
- **THEN** the answer is the channel itself

#### Scenario: A family is derivable from the confirmed pairs alone
- **GIVEN** the recorded confirmations and rejections
- **WHEN** family membership is computed from them
- **THEN** it agrees with what the inventory reports
- **AND** no separately maintained record can disagree with the pairs

### Requirement: Family Confirmation

The system SHALL provide a command that records a confirmed affiliation between two or more channels. It SHALL also record a rejection, so a proposal declined once is not proposed again. Confirmation MUST NOT ask which channel is canonical, and MUST NOT depend on the order in which pairs are confirmed.

#### Scenario: Confirming a pair
- **WHEN** the operator confirms a candidate pair
- **THEN** the two channels belong to one family
- **AND** the candidate is marked confirmed, with when it was decided
- **AND** no channel is named as the family's main one

#### Scenario: Rejecting a pair
- **WHEN** the operator rejects a candidate pair
- **THEN** neither channel's family membership changes
- **AND** the candidate is marked rejected, with when it was decided
- **AND** an optional free-text note is stored with the rejection

#### Scenario: Confirming a pair that bridges two families
- **GIVEN** two channels each already belonging to a different family
- **WHEN** the operator confirms the pair
- **THEN** the two families become one
- **AND** every channel of both belongs to it

#### Scenario: Confirming a pair inside one family
- **GIVEN** two channels already in the same family
- **WHEN** the operator confirms the pair
- **THEN** the pair is recorded as confirmed
- **AND** family membership is unchanged
- **AND** the command succeeds rather than reporting a conflict

#### Scenario: A group of channels is assembled from whatever pairs were found
- **GIVEN** several channels sharing an author, on which detection proposed pairs that form no star around any one of them
- **WHEN** the operator confirms those pairs in the order they were proposed
- **THEN** every one of the channels ends in the same family
- **AND** no confirmation is refused for naming the wrong channel

#### Scenario: Confirming a whole group at once
- **GIVEN** several channels the operator knows share an author
- **WHEN** they are confirmed in a single statement
- **THEN** every pair among them is recorded as confirmed
- **AND** all of them belong to one family

#### Scenario: A group confirmed at once survives one withdrawal
- **GIVEN** a group confirmed in a single statement
- **WHEN** the operator withdraws one of its pairs
- **THEN** every channel of the group remains in the same family
- **AND** only the withdrawn pair stops being confirmed

#### Scenario: A group repeats channels
- **GIVEN** a statement naming the same channel more than once
- **WHEN** confirmation is attempted
- **THEN** the command fails and nothing is written

#### Scenario: Confirming an unknown channel
- **GIVEN** a statement naming a channel the inventory does not hold
- **WHEN** confirmation is attempted
- **THEN** the command fails and nothing is written
- **AND** no pair among the other channels named is recorded

#### Scenario: A channel cannot be affiliated with itself
- **WHEN** a pair naming the same channel twice is confirmed
- **THEN** the command fails and nothing is written

#### Scenario: A confirmation can be undone
- **GIVEN** a confirmed pair
- **WHEN** the operator withdraws that confirmation
- **THEN** the pair is no longer confirmed
- **AND** the pair returns to being reviewable

#### Scenario: Withdrawing a pair that still leaves the channels connected
- **GIVEN** a family in which another chain of confirmed pairs connects the two channels
- **WHEN** the pair is withdrawn
- **THEN** the family is unchanged
- **AND** every other confirmed pair is left standing

#### Scenario: Withdrawing the pair that held two parts together
- **GIVEN** a family whose channels are connected only through one confirmed pair
- **WHEN** that pair is withdrawn
- **THEN** the family splits into the two parts that remain connected
- **AND** no confirmed pair other than the withdrawn one is discarded

#### Scenario: Affiliation is confirmable without a prior candidate
- **GIVEN** two channels the operator knows share an author, on which no signal fired
- **WHEN** the operator confirms the pair directly
- **THEN** they belong to one family
- **AND** the pair is stored as confirmed, marked as having come from the operator rather than from a signal
