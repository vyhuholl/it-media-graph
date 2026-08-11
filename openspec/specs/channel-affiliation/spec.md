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

This requirement governs only the rarity-weighted reading of a token. A token that is also a handle named by a channel carrying it is evidence under the named handle signal, and this signal's maximum MUST NOT be relaxed to admit it.

#### Scenario: Two usernames share a distinctive token

- **GIVEN** two channels whose usernames share a token no other channel carries
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records the shared token

#### Scenario: A common token is not evidence

- **GIVEN** a token carried by more channels than the configured maximum
- **WHEN** detection runs
- **THEN** it produces no candidate on its own

#### Scenario: A common token that is also a named handle

- **GIVEN** a token carried by more channels than this signal's configured maximum
- **AND** named as a handle by a channel carrying it
- **WHEN** detection runs
- **THEN** this signal still produces no candidate for it
- **AND** the candidate another signal forms from it carries that signal's evidence, not this one's

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

#### Scenario: A pair with no channel in scope is not shown
- **GIVEN** a pair in which neither channel has been accepted into scope
- **WHEN** detection runs
- **THEN** it is not shown among the candidates awaiting review
- **AND** it is still computed and stored
- **AND** it can be shown on request

#### Scenario: One channel in scope is enough to be shown
- **GIVEN** a pair in which exactly one channel has been accepted into scope
- **WHEN** detection runs
- **THEN** the pair is shown among the candidates awaiting review

#### Scenario: A channel accepted later needs no recomputation
- **GIVEN** a stored pair that was hidden because neither channel was in scope
- **WHEN** one of the two is accepted into scope
- **THEN** the pair is shown among the candidates awaiting review
- **AND** the signals are not recomputed for it to appear

#### Scenario: What is hidden is reported, not silently dropped
- **GIVEN** a run in which some proposals are not shown
- **WHEN** the run reports its result
- **THEN** the number of pairs proposed and the number awaiting review are both stated

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
- **THEN** the minimum outgoing edges, the concentration threshold, the minimum token length, the maximum number of channels a token may appear on, the maximum number of channels a named handle may be carried by, and the minimum edges each way for mutual density are each settable

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

### Requirement: Named Handle Token Signal

The system SHALL treat a username token as evidence of a shared author when a channel carrying that token names the same token as a handle in its own stored description. Every pair among the channels carrying such a token SHALL be proposed.

The evidence is the signature, not its rarity: the strength of this signal MUST NOT decrease as more channels carry the token. The number of channels a named handle may be carried by SHALL be bounded by its own configurable maximum, which exists to bound the number of pairs one token can produce and MUST NOT be shared with the rarity-weighted token signal.

This signal MUST NOT resolve a handle to a channel. It compares text against the usernames the inventory already holds, so it SHALL accept a handle that could not be a Telegram username, and a handle naming no channel in the inventory is evidence exactly as a handle naming one.

#### Scenario: A signed handle proposes the whole group

- **GIVEN** three channels whose usernames share a token
- **AND** one of the three names that token as a handle in its own description
- **WHEN** detection runs
- **THEN** every pair among the three is proposed
- **AND** the evidence records the handle and how many channels carry it

#### Scenario: A handle named by a channel that does not carry it

- **GIVEN** a token carried by several channels
- **AND** named as a handle only in the description of a channel whose username does not carry it
- **WHEN** detection runs
- **THEN** no candidate is formed on this signal

#### Scenario: A larger group is not weaker evidence

- **GIVEN** one named-handle group of two channels and one of five
- **WHEN** detection runs
- **THEN** the contribution of this signal to a pair from the larger group is no smaller than its contribution to a pair from the smaller

#### Scenario: A handle that could not be a username

- **GIVEN** a description naming a handle whose first character is a digit
- **AND** whose text matches a token the naming channel's own username carries
- **WHEN** detection runs
- **THEN** the group carrying that token is proposed
- **AND** no attempt is made to resolve the handle

#### Scenario: A handle naming nothing in the inventory

- **GIVEN** a named handle matching no username the inventory holds
- **WHEN** detection runs
- **THEN** it is evidence on this signal just the same
- **AND** it is not reported as an unresolvable reference of the description signal

#### Scenario: A handle on too many channels

- **GIVEN** a token carried by more channels than this signal's configured maximum
- **WHEN** detection runs
- **THEN** no candidate is formed on this signal

#### Scenario: A short handle is not evidence

- **GIVEN** a named handle shorter than the configured minimum token length
- **WHEN** detection runs
- **THEN** no candidate is formed on this signal

#### Scenario: No description to read

- **GIVEN** several channels sharing a token, none of which has a stored description
- **WHEN** detection runs
- **THEN** this signal forms no candidate among them
- **AND** they remain eligible for every other signal

#### Scenario: The signal reads only stored data

- **WHEN** this signal runs
- **THEN** no request is made to Telegram
- **AND** no description is fetched to answer it

### Requirement: Handle Groups Are Reviewed Together

The system SHALL present the candidates one named handle proposed as a single group rather than as unrelated pairs, so that a family of several channels costs one review instead of one review per pair.

Grouping is presentation and MUST NOT change what is recorded: a confirmation or a rejection remains a statement about pairs, and a group carries no decision of its own.

#### Scenario: A group is shown as one block

- **GIVEN** five channels proposed by one named handle
- **WHEN** the candidate list is shown
- **THEN** their pairs appear together, under the handle that proposed them
- **AND** the group states how many channels and how many pairs it holds

#### Scenario: A group whose pairs are partly decided

- **GIVEN** a group in which one pair has been confirmed or rejected
- **WHEN** the candidate list is shown
- **THEN** the pairs still awaiting review are shown as a group
- **AND** the decided pair is not shown among them

#### Scenario: A bound does not misreport a group

- **GIVEN** a bound on the list that admits only part of a group
- **WHEN** the list is shown
- **THEN** at most that many candidates are shown
- **AND** the group states how many of its pairs are not shown

#### Scenario: A pair several signals reached

- **GIVEN** a pair proposed by a named handle and by another signal
- **WHEN** the list is shown
- **THEN** it appears once, inside its group
- **AND** its evidence names every signal that fired for it

#### Scenario: Grouping introduces no new decision

- **GIVEN** a group awaiting review
- **WHEN** the operator confirms the channels it names
- **THEN** every pair among them is recorded as confirmed
- **AND** the result is indistinguishable from confirming the same channels named without a group

