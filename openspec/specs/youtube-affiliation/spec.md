# youtube-affiliation Specification

## Purpose
Recognising that several YouTube channels share an author or an organisation: signals read from stored payloads propose ranked candidate pairs and token groups with their evidence, and only the operator's decision makes a family. Detection never records a family and never changes a derived reading.

## Requirements

### Requirement: Detection Reads Only Stored Payloads

The system SHALL compute affiliation signals from the channel inventory and the stored channel payloads alone. It MUST NOT make any request to YouTube, MUST NOT spend quota, and MUST NOT modify or delete a stored payload.

#### Scenario: No request is made
- **WHEN** affiliation detection runs
- **THEN** no request is made to YouTube and no API key is needed

#### Scenario: Stored data is untouched
- **WHEN** affiliation detection runs
- **THEN** no payload in any `yt_raw_*` table is modified or deleted

#### Scenario: A channel with no stored payload still participates
- **GIVEN** a channel whose payload has been removed or was never fetched
- **WHEN** detection runs
- **THEN** it contributes no title tokens and no description
- **AND** it can still be paired by a reference that names its channel id

### Requirement: Detection Is Re-runnable

The system SHALL produce the same candidates from the same data and parameters, however many times it runs, and MUST NOT discard a review decision by re-running.

#### Scenario: Re-running writes no duplicate
- **GIVEN** a completed detection run
- **WHEN** detection runs again over unchanged data with the same parameters
- **THEN** the same pairs are proposed
- **AND** no pair is recorded twice

#### Scenario: Evidence is refreshed, decisions are not
- **GIVEN** a pair whose stored evidence predates a later payload refresh
- **WHEN** detection runs again
- **THEN** the pair's evidence and score are updated to what the current payloads show
- **AND** any confirmation or rejection already recorded for it is left unchanged

#### Scenario: Changing a threshold does not rewrite a decision
- **GIVEN** confirmed and rejected pairs from an earlier run
- **WHEN** detection runs again with different thresholds
- **THEN** those decisions are retained
- **AND** the candidate list reflects the new thresholds

### Requirement: Shared Title Token Signal

The system SHALL treat a token shared between two channel titles as evidence of a shared author or organisation, weighted by how rare that token is across the inventory. A token carried by many channels is a subject, not an author, and MUST NOT weigh as much as a rare one. Tokens SHALL be read from the title only, lowercased and stripped of punctuation; the custom URL SHALL NOT be read for tokens.

#### Scenario: Two titles share a distinctive token
- **GIVEN** two channels whose titles share a token no other channel carries
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records the shared token

#### Scenario: A common token is not evidence
- **GIVEN** a token carried by more channels than the configured maximum
- **WHEN** detection runs
- **THEN** it produces no candidate on this signal

#### Scenario: A short token is not evidence
- **GIVEN** two titles sharing a token shorter than the configured minimum length
- **WHEN** detection runs
- **THEN** it produces no candidate on this signal

#### Scenario: A handle is not a token source
- **GIVEN** two channels whose custom URLs share a spelling their titles do not
- **WHEN** detection runs
- **THEN** no candidate is formed from the custom URLs

#### Scenario: A token in a non-Latin script counts
- **GIVEN** two channels whose Cyrillic titles share a token rare enough to mean something
- **WHEN** detection runs
- **THEN** the pair is proposed on that token

### Requirement: Description Reference Signal

The system SHALL treat a channel's description naming another channel in the inventory as evidence that the two share an author or an organisation: a link to a channel by handle or by id, or a bare `@handle` an inventory channel carries. A reference found in one direction is evidence on its own; a reference found in both directions SHALL count as stronger evidence.

#### Scenario: A description names another channel
- **GIVEN** a stored description containing a link to, or a bare mention of, another channel in the inventory
- **WHEN** detection runs
- **THEN** the pair is proposed
- **AND** the evidence records which channel's description named which

#### Scenario: A reference in both directions weighs more
- **GIVEN** two channels whose descriptions each name the other
- **WHEN** detection runs
- **THEN** the pair scores above an otherwise identical pair referenced in one direction only

#### Scenario: A description naming its own channel
- **GIVEN** a description containing a link to the channel it belongs to
- **WHEN** detection runs
- **THEN** no candidate is formed

#### Scenario: A named channel outside the inventory
- **GIVEN** a description naming a handle or channel id the inventory does not hold
- **WHEN** detection runs
- **THEN** no candidate is formed for it
- **AND** it is counted in what the run reports as unresolvable references

#### Scenario: An ambiguous handle names no channel
- **GIVEN** a description naming a handle that two inventory channels carry
- **WHEN** detection runs
- **THEN** no candidate is formed from it on this signal

#### Scenario: Links that name no channel are ignored
- **GIVEN** a description containing a custom URL, a link through a shortener, or a link that is not to YouTube
- **WHEN** detection runs
- **THEN** none of them produces a candidate

### Requirement: Reference-Corroborated Token Groups

The system SHALL treat a token as naming one family when a channel carrying it explicitly references, in its stored description, another inventory channel that also carries it. Every channel carrying the token SHALL then be proposed as one group, whatever the token's frequency — an organisation's product channels and a franchise's local chapters are families, not subjects.

#### Scenario: A corroborated token proposes its whole group
- **GIVEN** twenty channels whose titles share a token
- **AND** one of them references another carrier of that token in its description
- **WHEN** detection runs
- **THEN** every pair among the carriers is proposed as one group
- **AND** the evidence records the token and how many channels carry it

#### Scenario: A reference to a non-carrier corroborates nothing
- **GIVEN** a token carried by several channels
- **AND** a carrier's description referencing a channel whose title does not carry it
- **WHEN** detection runs
- **THEN** the group is not proposed by this signal

#### Scenario: An unresolvable reference corroborates nothing
- **GIVEN** a token carried by several channels
- **AND** a carrier's description naming a channel outside the inventory
- **WHEN** detection runs
- **THEN** the group is not proposed by this signal
- **AND** the reference is counted as unresolvable

#### Scenario: A group whose carriers alone are too many
- **GIVEN** a corroborated token carried by more channels than this signal's configured maximum
- **WHEN** detection runs
- **THEN** no candidate is formed from it

### Requirement: A Corroborated Token Is Not Weighed By Its Rarity

The evidence is the explicit reference, not the token's frequency: the strength of a corroborated group SHALL NOT decrease as more channels carry the token. The number of carriers SHALL be bounded by its own configurable maximum, which exists to bound the number of pairs one token can produce and MUST NOT be shared with the rarity-weighted title token signal.

#### Scenario: A larger group is not weaker evidence
- **GIVEN** one corroborated group of three channels and one of twenty
- **WHEN** detection runs
- **THEN** the contribution of this signal to a pair from the larger group is no smaller than its contribution to a pair from the smaller

#### Scenario: One observation counts once
- **GIVEN** a token both corroborated by a reference and rare enough for the title token signal
- **WHEN** detection runs
- **THEN** the corroborated group carries this signal's evidence only
- **AND** the pair is not also scored as a rare shared title token

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

#### Scenario: The stored order is byte order, whatever the database locale
- **GIVEN** two ids whose code-point order and the database locale's collation order disagree — a lowercase letter before an uppercase one at the first differing character
- **WHEN** detection stores the pair in the order the code points give
- **THEN** the pair is stored, not refused by the constraint that guards the order

#### Scenario: The list can be bounded
- **WHEN** detection runs with a limit
- **THEN** at most that many candidates are shown
- **AND** they are the highest-scoring ones

#### Scenario: A channel is never its own candidate
- **WHEN** detection runs
- **THEN** no pair naming the same channel twice is proposed

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
- **GIVEN** a pair in which neither channel has status `seed`
- **WHEN** detection runs
- **THEN** it is not shown among the candidates awaiting review
- **AND** it is still computed and stored
- **AND** it can be shown on request

#### Scenario: One channel in scope is enough to be shown
- **GIVEN** a pair in which exactly one channel has status `seed`
- **WHEN** detection runs
- **THEN** the pair is shown among the candidates awaiting review

#### Scenario: A channel accepted later needs no recomputation
- **GIVEN** a stored pair that was hidden because neither channel was a seed
- **WHEN** one of the two becomes a seed
- **THEN** the pair is shown among the candidates awaiting review
- **AND** the signals are not recomputed for it to appear

#### Scenario: What is hidden is reported, not silently dropped
- **GIVEN** a run in which some proposals are not shown
- **WHEN** the run reports its result
- **THEN** the number of pairs proposed and the number awaiting review are both stated

### Requirement: Groups Are Reviewed Together

The system SHALL present the candidates one corroborated token proposed as a single group rather than as unrelated pairs, so that an organisation's channels cost one review instead of one review per pair. Grouping is presentation and MUST NOT change what is recorded: a confirmation or a rejection remains a statement about pairs, and a group carries no decision of its own.

#### Scenario: A group is shown as one block
- **GIVEN** twenty channels proposed by one corroborated token
- **WHEN** the candidate list is shown
- **THEN** their pairs appear together, under the token that proposed them
- **AND** the group states how many channels and how many pairs it holds
- **AND** the line that would confirm them all is shown with it

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
- **GIVEN** a pair proposed by a corroborated token and by another signal
- **WHEN** the list is shown
- **THEN** it appears once, inside its group
- **AND** its evidence names every signal that fired for it

### Requirement: Candidate Evidence Is Stored

The system SHALL store, with every proposed pair, the per-signal evidence that produced it and the score it received, so a proposal can be read back and checked without re-running detection and without re-reading the payloads. Titles, handles and description text SHALL NOT be copied into the stored evidence; what YouTube said stays in the raw layer.

#### Scenario: Evidence outlives the run
- **GIVEN** a completed detection run
- **WHEN** the candidate list is read afterwards
- **THEN** each pair carries which signals fired
- **AND** each carries the values those signals were computed from
- **AND** none of it requires re-reading the payloads to display

#### Scenario: Evidence names the parameters it was computed under
- **WHEN** a pair is recorded
- **THEN** the thresholds the run used are recoverable for it
- **AND** a later run under different thresholds is distinguishable from it

### Requirement: Detection Never Records A Family

The system MUST NOT write a family link as a result of computing signals. Recording that two channels share an author or an organisation SHALL require an explicit human confirmation naming the pair.

#### Scenario: A run writes no family link
- **GIVEN** an inventory in which no family is recorded
- **WHEN** detection runs, whatever the scores
- **THEN** no channel's family membership is written
- **AND** the change is limited to candidates and their evidence

### Requirement: Family Confirmation

The system SHALL provide a command that records a confirmed affiliation between two or more channels. It SHALL also record a rejection, so a proposal declined once is not proposed again, and SHALL record a token rejection, declining every pair a corroborated token proposed. Confirmation MUST NOT ask which channel is canonical, and MUST NOT depend on the order in which pairs are confirmed.

#### Scenario: Confirming a group
- **WHEN** the operator confirms several channels at once
- **THEN** every pair among them is recorded as confirmed
- **AND** all of them belong to one family
- **AND** no channel is named as the family's main one

#### Scenario: Confirming a pair that bridges two families
- **GIVEN** two channels each already belonging to a different family
- **WHEN** the operator confirms the pair
- **THEN** the two families become one
- **AND** every channel of both belongs to it

#### Scenario: Rejecting a pair
- **WHEN** the operator rejects a candidate pair
- **THEN** neither channel's family membership changes
- **AND** the candidate is marked rejected, with when it was decided
- **AND** an optional free-text note is stored with the rejection

#### Scenario: Rejecting a whole token
- **GIVEN** a corroborated token whose group holds pending pairs
- **WHEN** the operator rejects the token
- **THEN** every pair the token proposed that still awaits review is recorded as rejected
- **AND** the rejection's note names the token
- **AND** every already-decided pair is left exactly as it was

#### Scenario: A rejected token does not veto new carriers
- **GIVEN** a token whose pairs were rejected
- **AND** a channel that later starts carrying the token
- **WHEN** detection runs again
- **THEN** the pairs between the new carrier and the others are proposed as new questions

#### Scenario: A confirmation can be undone
- **GIVEN** a confirmed pair
- **WHEN** the operator withdraws that confirmation
- **THEN** the pair is no longer confirmed
- **AND** the pair returns to being reviewable

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

#### Scenario: Confirming an unknown channel
- **GIVEN** a statement naming a channel the inventory does not hold
- **WHEN** confirmation is attempted
- **THEN** the command fails and nothing is written

### Requirement: Family Shape

A family SHALL be a set of channels, of any size, with no member privileged over another. It is the transitive closure of the confirmed pairs among its channels: two channels belong to the same family exactly when a chain of confirmed pairs connects them. A channel with no confirmed pair is a family of one.

#### Scenario: A family is derivable from the confirmed pairs alone
- **GIVEN** the recorded confirmations and rejections
- **WHEN** family membership is computed from them
- **THEN** it agrees with what the system reports
- **AND** no separately maintained record can disagree with the pairs

#### Scenario: An unaffiliated channel is its own family
- **GIVEN** a channel with no confirmed pair
- **WHEN** its family is asked for
- **THEN** the answer is the channel itself

#### Scenario: Confirming the same channels in a different order gives the same family
- **GIVEN** a set of pairs among several channels
- **WHEN** they are confirmed in any order
- **THEN** the resulting families are the same

### Requirement: Confirmed Families Change No Derived Reading

The system MUST NOT delete, hide or alter any payload, reference or pending target because the channels involved were confirmed as one family. What the payloads say stays said; excluding family-internal references belongs to analysis.

#### Scenario: References are left in place
- **GIVEN** two channels with a reference between them
- **WHEN** they are confirmed as one family
- **THEN** the reference is still read from the payloads exactly as before

#### Scenario: The family is queryable for exclusion
- **GIVEN** a recorded family
- **WHEN** references are read for analysis
- **THEN** each reference's endpoints can be resolved to their families
- **AND** a reference whose endpoints share a family is distinguishable from one whose endpoints do not

### Requirement: Signal Coverage Is Reported

The system SHALL report how much of the inventory each signal could speak about, so a short candidate list is not read as a small problem.

#### Scenario: Coverage is stated
- **WHEN** detection runs
- **THEN** it reports how many of the channels considered have a stored payload
- **AND** how many have a stored description
- **AND** how many unresolvable references the descriptions named

#### Scenario: A signal that could not run says so
- **GIVEN** a signal for which no channel holds the data it needs
- **WHEN** detection runs
- **THEN** the run reports that the signal produced nothing for lack of data
- **AND** does not report it as having found nothing

### Requirement: Thresholds And Weights Are Parameters

Every threshold and weight governing detection SHALL be settable per run, and MUST NOT be fixed in the code. A run SHALL report the values it used.

#### Scenario: Thresholds are settable
- **WHEN** detection runs
- **THEN** the minimum token length, the maximum number of channels a title token may appear on, and the maximum number of channels a corroborated token may be carried by are each settable

#### Scenario: Weights are settable
- **WHEN** detection runs
- **THEN** the weight of each signal in the combined score is settable

#### Scenario: Defaults are usable
- **WHEN** detection runs with no parameters given
- **THEN** it runs on documented defaults
- **AND** reports the values it used

#### Scenario: A parameter outside its valid range is refused
- **GIVEN** a non-positive minimum, or a maximum below two
- **WHEN** detection runs
- **THEN** the command fails, naming the parameter
- **AND** nothing is written

### Requirement: The Report Is An Operator Command

The report names titles and handles read from the API, so it SHALL be a command the operator runs by hand. No scheduled job SHALL run it, and log records written while detecting SHALL identify channels by id only.

#### Scenario: Not scheduled
- **WHEN** the detection command exists
- **THEN** no scheduled job runs it and its output reaches no workload's logs

#### Scenario: Logs name ids only
- **WHEN** detection runs
- **THEN** every log record it writes names channels by id
- **AND** carries no title, handle, description or link read from the API
