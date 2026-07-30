## ADDED Requirements

### Requirement: Manual Addition

The system SHALL provide `itgraph add`, creating inventory records for channels named by public username. The command MUST resolve each username by public lookup and MUST NOT join, subscribe to, or read the dialog list of any account. A run SHALL be bounded by the number of requests it makes, resumable from the same input, and MUST NOT spend a request on a username the inventory already holds.

#### Scenario: A channel is added by username

- **GIVEN** a public username naming a channel not in the inventory
- **WHEN** the command runs
- **THEN** the username is resolved by public lookup
- **AND** a record is created with its Telegram id, username and title
- **AND** its discovery source is `manual`
- **AND** it is marked as resolved
- **AND** its status is `candidate`

#### Scenario: Nothing is joined or subscribed to

- **WHEN** the command runs
- **THEN** no request to join or subscribe to a channel is made
- **AND** no dialog list is read
- **AND** no record is created for any channel other than those named

#### Scenario: A username already in the inventory costs no request

- **GIVEN** a username matching a channel already in the inventory, matched case-insensitively
- **WHEN** the command runs
- **THEN** no request is made for it
- **AND** it is reported as already known
- **AND** its record is left unchanged

#### Scenario: Usernames may be given in a file

- **GIVEN** a file holding one username per line
- **WHEN** the command runs against that file
- **THEN** each username is added as if given as an argument
- **AND** blank lines and lines beginning with `#` are ignored

#### Scenario: A username is accepted in the forms it gets pasted in

- **GIVEN** entries written as a bare username, with a leading `@`, or as a `t.me` link
- **WHEN** the command runs
- **THEN** each is understood as the same username
- **AND** two entries differing only in form or letter case cost one request between them

#### Scenario: An invite link is refused before anything is spent

- **GIVEN** an entry that is an invite link rather than a public username
- **WHEN** the command runs
- **THEN** the command fails, naming the offending entry
- **AND** no request is made for any entry

#### Scenario: A run is bounded by requests, not by entries

- **GIVEN** an input holding more usernames than the given limit, some of them already in the inventory
- **WHEN** the command runs with that limit
- **THEN** the number of lookups does not exceed the limit
- **AND** usernames already in the inventory do not count towards it

#### Scenario: A run resumes from the same input

- **GIVEN** a previous run that added part of an input before stopping
- **WHEN** the command runs again against the same input
- **THEN** the channels already added cost no request
- **AND** the remaining usernames are resolved

#### Scenario: Reviewing while adding

- **GIVEN** usernames given as arguments and a review to apply
- **WHEN** the command runs asking for them to be marked in scope
- **THEN** each record created by this run has status `seed`
- **AND** its kind is set to the given value
- **AND** its review timestamp is set

#### Scenario: An unreviewed list cannot be reviewed unseen

- **WHEN** the command is asked to apply a review together with a file of usernames
- **THEN** the command fails
- **AND** no request is made and nothing is written

#### Scenario: An existing review is never overwritten

- **GIVEN** a channel in the inventory that has already been reviewed
- **WHEN** the command runs naming it, with or without a review to apply
- **THEN** its status, kind, rejection reason and review timestamp are left unchanged
- **AND** its discovery source and first-seen timestamp are left unchanged

#### Scenario: A username that names no channel creates nothing

- **GIVEN** a username that resolves to a user or a bot rather than a channel
- **WHEN** the command runs
- **THEN** no record is created for it
- **AND** it is reported as not a channel

#### Scenario: A username that cannot be resolved creates nothing

- **GIVEN** a username that is unoccupied, or names an entity the account cannot reach
- **WHEN** the command runs
- **THEN** no record is created for it
- **AND** it is reported as failed, with the reason
- **AND** the run continues with the remaining usernames

#### Scenario: Failures can be written back out as the next run's input

- **GIVEN** a run in which some usernames failed
- **WHEN** the command is asked to write its failures to a path
- **THEN** the file holds those usernames in the form the command reads
- **AND** each carries its reason as a comment

#### Scenario: A run with no failures writes no failure file

- **GIVEN** a run in which every username was added or already known
- **WHEN** the command is asked to write its failures to a path
- **THEN** no file is written

#### Scenario: A pending mention the addition makes redundant is cleared

- **GIVEN** a username sitting in the mention queue awaiting resolution
- **WHEN** the command adds it as a channel
- **THEN** its pending mention is removed
- **AND** no later resolution run requests it

#### Scenario: Addition obeys collection limits

- **WHEN** the command runs
- **THEN** requests are paced and made one at a time
- **AND** the gap before each request is drawn anew, the same way the collector draws it
- **AND** a FloodWait is waited out rather than circumvented

#### Scenario: A long FloodWait halts the run

- **WHEN** Telegram returns a FloodWait longer than the configured halt threshold
- **THEN** the run stops instead of sleeping through it
- **AND** the channels added before the halt are retained and reported
- **AND** the time after which work may resume is reported

#### Scenario: A rate limit is recorded against the command that caused it

- **WHEN** a rate limit stops or delays the run
- **THEN** the event is recorded with the method that was limited
- **AND** it is attributed to the add command rather than to another

#### Scenario: A recent limit on the same method is reported before the run

- **GIVEN** a recorded rate limit on the username lookup method within the last day
- **WHEN** the command runs
- **THEN** the operator is told what was limited, when, and for how long
- **AND** the run proceeds
