## ADDED Requirements

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

## MODIFIED Requirements

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
