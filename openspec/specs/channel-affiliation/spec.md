# channel-affiliation Specification

## Purpose
TBD - created by archiving change detect-affiliated-channels. Update Purpose after archive.
## Requirements
### Requirement: Detection Reads Only Collected Data

The system SHALL compute affiliation signals from the inventory, the derived edges and the stored channel payloads alone. It MUST NOT make any network request, and MUST NOT modify the raw layer.

#### Scenario: No request is made
- **WHEN** affiliation detection runs
- **THEN** no request is made to Telegram
- **AND** no username is resolved and no description is fetched

#### Scenario: Raw data is untouched
- **WHEN** affiliation detection runs
- **THEN** no stored payload is modified or deleted

#### Scenario: A channel with no stored description still participates
- **GIVEN** a channel whose extended information has never been fetched
- **WHEN** detection runs
- **THEN** it is scored on the signals its data supports
- **AND** its absence of a description is not treated as absence of affiliation

### Requirement: Detection Is Re-runnable

The system SHALL produce the same candidates from the same data and parameters, however many times it runs, and MUST NOT discard a review decision by re-running.

#### Scenario: Re-running writes no duplicate
- **GIVEN** a completed detection run
- **WHEN** detection runs again over unchanged data with the same parameters
- **THEN** the same pairs are proposed
- **AND** no pair is recorded twice

#### Scenario: Evidence is refreshed, decisions are not
- **GIVEN** a pair whose stored evidence predates a later derivation run
- **WHEN** detection runs again
- **THEN** the pair's evidence and score are updated to what the current data shows
- **AND** any confirmation or rejection already recorded for it is left unchanged

#### Scenario: Changing a threshold does not rewrite a decision
- **GIVEN** confirmed and rejected pairs from an earlier run
- **WHEN** detection runs again with different thresholds
- **THEN** those decisions are retained
- **AND** the candidate list reflects the new thresholds

### Requirement: Description Reference Signal

The system SHALL treat a channel's description naming another channel as evidence that the two share an author. A reference found in one direction is evidence on its own; a reference found in both directions SHALL count as stronger evidence than a single one.

#### Scenario: A description names another channel
- **GIVEN** a stored description containing a `t.me` link or an `@mention` naming another channel in the inventory
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records which channel's description named which

#### Scenario: A reference in both directions weighs more
- **GIVEN** two channels whose descriptions each name the other
- **WHEN** detection runs
- **THEN** the pair scores above an otherwise identical pair referenced in one direction only

#### Scenario: A missing description is not a missing reference
- **GIVEN** a channel whose description names another channel that has no stored description
- **WHEN** detection runs
- **THEN** the pair is proposed on the one-directional reference
- **AND** the pair is not penalised for the reference that could not be checked

#### Scenario: A named channel outside the inventory
- **GIVEN** a description naming a username the inventory does not hold
- **WHEN** detection runs
- **THEN** no candidate is formed for it
- **AND** it is counted in what the run reports as unresolvable references

#### Scenario: Links that name no channel are ignored
- **GIVEN** a description containing an invite link, a sticker pack link, or a non-Telegram link
- **WHEN** detection runs
- **THEN** none of them produces a candidate

#### Scenario: A description naming its own channel
- **GIVEN** a description containing a link to the channel it belongs to
- **WHEN** detection runs
- **THEN** no candidate is formed

### Requirement: Shared Username Token Signal

The system SHALL treat a token shared between two usernames as evidence of a shared author, weighted by how rare that token is across the inventory. A token carried by many channels is a subject, not an author, and MUST NOT weigh as much as a rare one.

#### Scenario: Two usernames share a distinctive token
- **GIVEN** two channels whose usernames share a token no other channel carries
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records the shared token

#### Scenario: A common token is not evidence
- **GIVEN** a token carried by more channels than the configured maximum
- **WHEN** detection runs
- **THEN** it produces no candidate on its own

#### Scenario: A short token is not evidence
- **GIVEN** two usernames sharing a token shorter than the configured minimum length
- **WHEN** detection runs
- **THEN** it produces no candidate on its own

#### Scenario: A channel without a username
- **GIVEN** a channel whose username is unknown
- **WHEN** detection runs
- **THEN** it produces and receives no candidate on this signal
- **AND** it remains eligible for every other signal

### Requirement: Outgoing Concentration Signal

The system SHALL treat a channel sending a large share of its outgoing references to a single target as evidence that the two share an author. The share MUST be computed only over channels having at least the configured number of outgoing edges, so that a share taken over a handful of references cannot reach the threshold.

#### Scenario: A concentrated channel proposes its target
- **GIVEN** a channel whose outgoing edges meet the configured minimum
- **AND** whose share of them going to one target meets the configured threshold
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records the observed share and the number of edges it was computed over

#### Scenario: Too few edges to measure
- **GIVEN** a channel with fewer outgoing edges than the configured minimum
- **WHEN** detection runs
- **THEN** no candidate is formed from its concentration, whatever the share

#### Scenario: The target need not be a seed
- **GIVEN** a concentrated channel whose target is in the inventory but not a seed
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the target's status is shown with the candidate

### Requirement: Mutual Density Signal

The system SHALL treat two channels referencing each other repeatedly as evidence of a shared author, requiring at least the configured number of edges in each direction.

#### Scenario: A densely mutual pair is proposed
- **GIVEN** two channels with at least the configured number of edges in each direction
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records the edge count each way

#### Scenario: A one-directional relationship is not mutual
- **GIVEN** two channels where one references the other many times and receives nothing back
- **WHEN** detection runs
- **THEN** no candidate is formed on this signal

### Requirement: Candidates Are Ranked by Combined Evidence

The system SHALL propose a pair when **any** signal fires, and rank the proposals by their combined weighted score, strongest first. It MUST NOT require two or more signals before proposing a pair.

#### Scenario: One signal is enough to propose
- **GIVEN** a pair on which exactly one signal fires
- **WHEN** detection runs
- **THEN** the pair appears in the candidate list

#### Scenario: Corroborated pairs rank higher
- **GIVEN** one pair on which several signals fire and one on which a single signal fires
- **WHEN** detection runs
- **THEN** the corroborated pair ranks above the other

#### Scenario: A pair is proposed once
- **GIVEN** two channels for which signals fire in both directions
- **WHEN** detection runs
- **THEN** they appear as one candidate pair, not two
- **AND** the pair is stored in an order that does not depend on which signal fired first

#### Scenario: The list can be bounded
- **WHEN** detection runs with a limit
- **THEN** at most that many candidates are shown
- **AND** they are the highest-scoring ones

#### Scenario: A channel is never its own candidate
- **WHEN** detection runs
- **THEN** no pair naming the same channel twice is proposed

#### Scenario: A discussion chat is not an affiliation candidate
- **GIVEN** a chat already recorded as another channel's discussion chat
- **WHEN** detection runs
- **THEN** no candidate pairing it with its parent channel is proposed

#### Scenario: A settled pair is not proposed again
- **GIVEN** a pair already confirmed or rejected by the operator
- **WHEN** detection runs
- **THEN** it is not shown among the candidates awaiting review
- **AND** the decision remains inspectable

#### Scenario: Two channels already in one family
- **GIVEN** two channels already recorded as belonging to the same family
- **WHEN** detection runs
- **THEN** no candidate pairing them is shown for review

### Requirement: Candidate Evidence Is Stored

The system SHALL store, with every proposed pair, the per-signal evidence that produced it and the score it received, so a proposal can be read back and checked without re-running detection.

#### Scenario: Evidence outlives the run
- **GIVEN** a completed detection run
- **WHEN** the candidate list is read afterwards
- **THEN** each pair carries which signals fired
- **AND** each carries the values those signals were computed from
- **AND** none of it requires re-reading the raw layer to display

#### Scenario: Evidence names the parameters it was computed under
- **WHEN** a pair is recorded
- **THEN** the thresholds the run used are recoverable for it
- **AND** a later run under different thresholds is distinguishable from it

### Requirement: Detection Never Records A Family

The system MUST NOT write a family link as a result of computing signals. Recording that two channels share an author SHALL require an explicit human confirmation naming the pair.

#### Scenario: A run writes no family link
- **GIVEN** an inventory in which no family is recorded
- **WHEN** detection runs, whatever the scores
- **THEN** no channel's family pointer is written
- **AND** the change is limited to candidates and their evidence

#### Scenario: The highest-scoring pair is still only a proposal
- **GIVEN** a pair on which every signal fires
- **WHEN** detection runs
- **THEN** it is presented for review like any other candidate

### Requirement: Family Confirmation

The system SHALL provide a command that records a confirmed affiliation between two channels, naming which of them is canonical for the family. It SHALL also record a rejection, so a proposal declined once is not proposed again.

#### Scenario: Confirming a pair
- **GIVEN** a candidate pair and a choice of which channel is canonical
- **WHEN** the operator confirms it
- **THEN** the other channel's family pointer names the canonical channel
- **AND** the candidate is marked confirmed, with when it was decided

#### Scenario: Rejecting a pair
- **WHEN** the operator rejects a candidate pair
- **THEN** no family pointer is written for either channel
- **AND** the candidate is marked rejected, with when it was decided
- **AND** an optional free-text note is stored with the rejection

#### Scenario: Confirming a pair whose sides are in different families
- **GIVEN** two channels each already belonging to a different family
- **WHEN** the operator confirms the pair
- **THEN** the command fails and nothing is written
- **AND** the two families are named in the error

#### Scenario: Confirming an unknown channel
- **GIVEN** a pair naming a channel the inventory does not hold
- **WHEN** confirmation is attempted
- **THEN** the command fails and nothing is written

#### Scenario: A confirmation can be undone
- **GIVEN** a channel whose family pointer was written by a confirmation
- **WHEN** the operator withdraws that confirmation
- **THEN** the channel's family pointer is cleared
- **AND** the pair returns to being reviewable

#### Scenario: Affiliation is confirmable without a prior candidate
- **GIVEN** two channels the operator knows share an author, on which no signal fired
- **WHEN** the operator confirms the pair directly
- **THEN** the family link is recorded
- **AND** the pair is stored as confirmed, marked as having come from the operator rather than from a signal

### Requirement: Family Shape

A family SHALL be identified by its canonical channel. Every non-canonical member names that channel, and the canonical channel names none — so the family of any channel is the channel its pointer names, or itself when it names none.

#### Scenario: Members name the canonical channel
- **GIVEN** a family of three channels
- **WHEN** the inventory is queried
- **THEN** the two non-canonical members name the canonical channel
- **AND** the canonical channel's own pointer is empty

#### Scenario: A family is one level deep
- **WHEN** a family pointer is written naming a channel that is itself a member of another family
- **THEN** the write is refused
- **AND** the pointer never has to be followed more than once to reach a canonical channel

#### Scenario: A channel cannot be its own family pointer
- **WHEN** a channel's family pointer is written naming that same channel
- **THEN** the write is refused at the database level

#### Scenario: An unaffiliated channel is its own family
- **GIVEN** a channel with no family recorded
- **WHEN** its family is asked for
- **THEN** the answer is the channel itself

#### Scenario: Changing which channel is canonical
- **GIVEN** a family whose canonical channel the operator wants changed
- **WHEN** another member is made canonical
- **THEN** every member of the family names the new canonical channel
- **AND** the new canonical channel's own pointer is empty
- **AND** no member is left naming the former one

### Requirement: Observed Edges Are Not Modified

The system MUST NOT delete or alter edges between channels of one family. A repost between two of an author's channels is a real observation, and excluding it belongs to analysis.

#### Scenario: Confirming a family leaves the edges in place
- **GIVEN** two channels with edges between them
- **WHEN** they are confirmed as one family
- **THEN** every edge between them is retained unchanged

#### Scenario: The family is queryable for exclusion
- **GIVEN** a recorded family
- **WHEN** the edges are read for analysis
- **THEN** each edge's endpoints can be resolved to their families
- **AND** an edge whose endpoints share a family is distinguishable from one whose endpoints do not

### Requirement: Signal Coverage Is Reported

The system SHALL report how much of the inventory each signal could speak about, so a short candidate list is not read as a small problem.

#### Scenario: Description coverage is stated
- **WHEN** detection runs
- **THEN** it reports how many of the channels considered have a stored description
- **AND** how many do not

#### Scenario: A signal that could not run says so
- **GIVEN** a signal for which no channel holds the data it needs
- **WHEN** detection runs
- **THEN** the run reports that the signal produced nothing for lack of data
- **AND** does not report it as having found nothing

### Requirement: Thresholds And Weights Are Parameters

Every threshold and weight governing detection SHALL be settable per run, and MUST NOT be fixed in the code. A run SHALL report the values it used.

#### Scenario: Thresholds are settable
- **WHEN** detection runs
- **THEN** the minimum outgoing edges, the concentration threshold, the minimum token length, the maximum number of channels a token may appear on, and the minimum edges each way for mutual density are each settable

#### Scenario: Weights are settable
- **WHEN** detection runs
- **THEN** the weight of each signal in the combined score is settable

#### Scenario: Defaults are usable
- **WHEN** detection runs with no parameters given
- **THEN** it runs on documented defaults
- **AND** reports the values it used

#### Scenario: A parameter outside its valid range is refused
- **GIVEN** a concentration threshold outside the range a share can take, or a non-positive minimum
- **WHEN** detection runs
- **THEN** the command fails, naming the parameter
- **AND** nothing is written

