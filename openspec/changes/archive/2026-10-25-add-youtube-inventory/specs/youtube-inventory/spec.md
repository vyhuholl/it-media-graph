## Purpose

The registry of YouTube channels and the review each has had: which channels are in scope, what each one is, and in which language it speaks. Every later YouTube capability keys off a channel record and its status.

## ADDED Requirements

### Requirement: YouTube Channel Record

The system SHALL store one record per YouTube channel, keyed by its channel id. The record SHALL hold the operator's review and the channel's provenance, and MUST NOT hold data read from the API.

#### Scenario: A new record is unreviewed

- **WHEN** a channel record is created
- **THEN** its status is `candidate`
- **AND** kind, language, rejection reason and review timestamp are empty
- **AND** its discovery source and first-seen timestamp are recorded

#### Scenario: A rejection cannot be reasonless

- **WHEN** a record is written with status `rejected` and no rejection reason
- **THEN** the write is refused at the database level

#### Scenario: A seed cannot lack a kind or a language

- **WHEN** a record is written with status `seed` and no kind, or no language
- **THEN** the write is refused at the database level

#### Scenario: The record holds no API data

- **WHEN** a channel record is read
- **THEN** it carries no title, handle, description or statistic

### Requirement: Review Values

The system SHALL accept exactly these review values for YouTube. Status: `candidate`, `seed`, `maybe`, `rejected`. Kind: `personal`, `company`, `media`, `event`, `education`. Rejection reason: `not_it`, `adjacent`, `crypto`, `infobiz`, `ads`, `content_farm`, `consumer_tech`. Language: `en`, `ru`, `other`. Discovery source: `manual`.

#### Scenario: A value outside the set is refused

- **WHEN** a review names a kind, reason or language outside its set
- **THEN** the review fails, naming the value and the accepted ones
- **AND** nothing is written

#### Scenario: Telegram's values are unaffected

- **WHEN** a Telegram channel is reviewed
- **THEN** it accepts the Telegram kinds and rejection reasons as before
- **AND** none of the YouTube-only values

### Requirement: Records Are Never Deleted

The system SHALL retain every YouTube channel record, including rejected channels and channels YouTube no longer returns, and MUST NOT delete one.

#### Scenario: Rejected and vanished channels remain

- **WHEN** the inventory is listed without a status filter
- **THEN** rejected channels and channels YouTube no longer returns are included

### Requirement: Seed Import

The system SHALL provide `itgraph yt add`, creating records for channels named in operator-supplied entries, given as arguments, in a file, or on standard input. Each entry SHALL be resolved through the API to a channel id before a record is created. The command MUST NOT apply a review.

#### Scenario: A handle is added

- **GIVEN** a handle naming a channel not in the inventory
- **WHEN** the command runs
- **THEN** the handle is resolved to a channel id
- **AND** a record is created with status `candidate` and discovery source `manual`
- **AND** the channel's API data is stored as of this moment

#### Scenario: Entries may come from a file or standard input

- **GIVEN** a file, or standard input, holding one entry per line
- **WHEN** the command runs against it
- **THEN** each entry is treated as if given as an argument
- **AND** blank lines and lines beginning with `#` are ignored
- **AND** a `#` comment following an entry after whitespace is ignored, so the failures file reads back as input

#### Scenario: An existing review is never overwritten

- **GIVEN** a channel already in the inventory
- **WHEN** an entry resolving to it is imported
- **THEN** its status, kind, language, rejection reason, review timestamp, discovery source and first-seen timestamp are unchanged

#### Scenario: Two entries naming one channel make one record

- **GIVEN** two entries in different forms that resolve to the same channel
- **WHEN** the command runs
- **THEN** one record exists for it
- **AND** the second entry is reported as naming the same channel as the first

### Requirement: Entry Forms

The system SHALL accept an entry as a handle with or without `@`, a `youtube.com/@handle` link, a `youtube.com/channel/<id>` link, a bare channel id, or a `youtube.com/user/<name>` link. Every other form SHALL be refused, with every bad entry reported together, before any request is made.

#### Scenario: A link is accepted in the forms it gets pasted in

- **GIVEN** links with or without a scheme, with `www.` or `m.`, with a trailing path such as `/videos`, or with a query string
- **WHEN** the command runs
- **THEN** each is understood as the channel it names

#### Scenario: Entries differing only in form or letter case cost one lookup

- **GIVEN** `@Name`, `name` and `youtube.com/@NAME` in one input
- **WHEN** the command runs
- **THEN** one lookup is made between them

#### Scenario: A form the API cannot resolve is refused before anything is spent

- **GIVEN** an input containing a `youtube.com/c/<name>` link, a `youtube.com/<name>` link, a video, Shorts, live or playlist link, or a link to another site
- **WHEN** the command runs
- **THEN** it fails, naming every such entry with its line number and what to supply instead
- **AND** no request is made for any entry
- **AND** nothing is written

### Requirement: Import Spending

The system SHALL bound an import by the number of requests it makes, MUST NOT spend a request on an entry the inventory already answers, and SHALL be resumable from the same input. Channel ids given directly SHALL be looked up together, up to fifty per request.

#### Scenario: An entry already in the inventory costs no request

- **GIVEN** a channel id already recorded, or a handle carried by a stored payload, matched case-insensitively
- **WHEN** it is imported
- **THEN** no request is made for it
- **AND** it is reported as already known

#### Scenario: A run is bounded by requests, not by entries

- **GIVEN** more entries than the given limit, some already known
- **WHEN** the command runs with that limit
- **THEN** the number of requests does not exceed the limit
- **AND** already-known entries do not count towards it

#### Scenario: A run resumes from the same input

- **GIVEN** a previous run that stopped part way through an input
- **WHEN** the command runs again against the same input
- **THEN** the channels already added cost no request

#### Scenario: Channel ids share requests

- **GIVEN** an input of a hundred channel ids, none known
- **WHEN** the command runs
- **THEN** two requests are made

### Requirement: Import Failures

The system SHALL report an entry the API cannot resolve, create no record for it, and continue with the rest. Failures SHALL be writable as the next run's input.

#### Scenario: An entry naming no channel creates nothing

- **GIVEN** a handle, legacy name or channel id YouTube returns no channel for
- **WHEN** the command runs
- **THEN** no record is created for it
- **AND** it is reported as not found
- **AND** the run continues with the remaining entries

#### Scenario: Failures can be written back out as input

- **GIVEN** a run in which some entries were not found
- **WHEN** the command is asked to write failures to a path
- **THEN** the file holds those entries as given, one per line, each with its reason as a comment

#### Scenario: Failures can go to standard output

- **WHEN** the command is asked to write failures to standard output
- **THEN** standard output holds only the failures
- **AND** the run's report goes to standard error

#### Scenario: A run with no failures writes no failure file

- **GIVEN** a run in which every entry was added or already known
- **WHEN** the command is asked to write failures to a path
- **THEN** no file is written

### Requirement: Review File Export

The system SHALL provide `itgraph yt review-export`, writing a tab-separated review file of the channels awaiting review to standard output or a path. Each row SHALL hold the channel id, the editable review columns filled with the current values, and read-only columns from the channel's API data. A header SHALL state the export moment and the accepted values.

#### Scenario: Unreviewed channels are exported by default

- **WHEN** the command runs without a status filter
- **THEN** every channel with no review is exported, and no other

#### Scenario: Undecided channels can be exported for another look

- **WHEN** the command runs filtered to `maybe`
- **THEN** those channels are exported with their current kind, language and note filled in

#### Scenario: The file says what may be written in it

- **WHEN** a file is exported
- **THEN** its header states the export moment in UTC
- **AND** lists the accepted decisions, kinds, languages and rejection reasons
- **AND** points to the review guidelines in the README

#### Scenario: The file gives what a reviewer needs to recognise a channel

- **WHEN** a channel with stored API data is exported
- **THEN** its row holds the handle, title, subscriber count, the language and country the channel declares, and a link to the channel
- **AND** a channel without stored API data is exported with its id and link only

#### Scenario: Exporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made

### Requirement: Review File Apply

The system SHALL provide `itgraph yt review-apply`, reading a review file from a path or standard input and applying its decisions in one transaction. The whole file SHALL be validated first: if any row is invalid, every error is reported with its line number and nothing is written.

#### Scenario: Decisions are applied

- **GIVEN** a valid file with decisions `seed`, `maybe` and `reject` in its rows
- **WHEN** the command runs
- **THEN** each channel's status becomes `seed`, `maybe` or `rejected` respectively
- **AND** its kind, language and rejection reason are set from the row
- **AND** the row's note is stored as the rejection note on a rejection and as the kind note otherwise
- **AND** its review timestamp is set

#### Scenario: A row left blank is skipped

- **GIVEN** a row whose decision is empty
- **WHEN** the command runs
- **THEN** that channel's record is unchanged

#### Scenario: Every mistake is reported at once and nothing is written

- **GIVEN** a file with a seed lacking a kind, a seed lacking a language, a rejection lacking a reason, a misspelt kind, an unknown channel id and a channel listed twice
- **WHEN** the command runs
- **THEN** it fails, naming every one of them with its line number
- **AND** no record is changed, including the rows without mistakes

#### Scenario: A label without a decision is a mistake, not a blank row

- **GIVEN** a row with a kind, a language, a reason or a note filled in and no decision
- **WHEN** the command runs
- **THEN** the row is reported as a decision forgotten, with its line number
- **AND** nothing is written, as for any other mistake

#### Scenario: A reason belongs only to a rejection

- **GIVEN** a row deciding `seed` or `maybe` with a reason filled in
- **WHEN** the command runs
- **THEN** the row is reported as a mistake, with its line number
- **AND** nothing is written

#### Scenario: A row changed since the export is a conflict

- **GIVEN** a channel whose review was changed after the file's export moment
- **WHEN** a row deciding something different about it is applied
- **THEN** that row is not written and is reported as a conflict
- **AND** the other rows are applied

#### Scenario: Applying a file twice changes nothing the second time

- **GIVEN** a file that has already been applied
- **WHEN** it is applied again
- **THEN** every row is reported as unchanged
- **AND** no review timestamp moves

#### Scenario: Read-only columns are not applied

- **GIVEN** a row whose title or handle column was edited
- **WHEN** the command runs
- **THEN** the edit has no effect

#### Scenario: A file without its header is refused

- **GIVEN** a file whose header has been removed
- **WHEN** the command runs
- **THEN** it fails without writing, saying the export moment cannot be read

#### Scenario: Applying costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made

### Requirement: Single Review

The system SHALL provide `itgraph yt mark`, recording one review decision for a channel addressed by its channel id or its handle. It is the only way to change a decision already made.

#### Scenario: Accepting a channel

- **WHEN** a channel is marked as in scope with a kind and a language
- **THEN** its status becomes `seed`, with that kind and language
- **AND** its review timestamp is set

#### Scenario: Accepting without a kind or a language fails

- **WHEN** a channel is marked as in scope and the kind or the language is missing
- **THEN** the command fails and nothing is written

#### Scenario: Rejecting a channel

- **WHEN** a channel is rejected with a reason and an optional note
- **THEN** its status becomes `rejected` with that reason and note
- **AND** its review timestamp is set

#### Scenario: Deferring a decision

- **WHEN** a channel is marked as undecided
- **THEN** its status becomes `maybe` and its review timestamp is set

#### Scenario: A label left out keeps its value

- **GIVEN** a channel with a kind, a language and a kind note
- **WHEN** it is marked as undecided, or rejected, without giving them
- **THEN** its kind and language are unchanged
- **AND** when it is marked as undecided, its kind note is unchanged
- **AND** when it is rejected without a note, its rejection note is empty

#### Scenario: A decision can be changed

- **GIVEN** a channel already reviewed
- **WHEN** it is marked with a different decision
- **THEN** the new decision replaces the old one

#### Scenario: Addressing a channel by handle

- **WHEN** the channel is given as a handle, with or without `@`
- **THEN** the record whose stored API data carries that handle is reviewed, matched case-insensitively
- **AND** no request to YouTube is made

#### Scenario: A handle carried by two records

- **GIVEN** two records whose stored API data carry the same handle
- **WHEN** that handle is given
- **THEN** the command fails, naming both channel ids, and nothing is written

#### Scenario: Reviewing an unknown channel

- **WHEN** the given id or handle matches no record
- **THEN** the command fails and nothing is written

### Requirement: Inventory Listing

The system SHALL provide `itgraph yt channels`, showing the YouTube inventory so review progress and the state of its API data are visible. It makes no request.

#### Scenario: Listing by status

- **WHEN** the command runs with a status filter
- **THEN** only channels with that status are listed
- **AND** each row shows the channel id, handle, title, status, kind and language

#### Scenario: A channel without API data is still listed

- **GIVEN** a channel whose API data has expired, or that YouTube no longer returns
- **WHEN** it is listed
- **THEN** its row shows its id and says which of the two it is, in place of handle and title

#### Scenario: Progress summary

- **WHEN** the command runs without a filter
- **THEN** a count per status is reported
- **AND** a count of seeds per language
- **AND** how many channels have current API data, expired API data, or are no longer returned

### Requirement: Remote Operation

The system SHALL provide `scripts/on-host.sh`, running an `itgraph` command on the collection host named in the deploy inventory, over SSH, with standard input, output and error passed through and the command's exit status returned.

#### Scenario: A review round trip from the operator's machine

- **GIVEN** the collection host in the deploy inventory
- **WHEN** the operator runs `review-export` through the script, redirecting output to a local file, edits it, and runs `review-apply -` through the script with that file on standard input
- **THEN** the export reads, and the apply writes, the database on the host

#### Scenario: Arguments arrive intact

- **WHEN** an argument containing spaces or quotes is passed through the script
- **THEN** the command on the host receives it unchanged

#### Scenario: Failure is reported as failure

- **WHEN** the command on the host exits with a non-zero status
- **THEN** the script exits with the same status

#### Scenario: No host configured

- **GIVEN** no deploy inventory naming the collection host
- **WHEN** the script runs
- **THEN** it fails without connecting, pointing to the inventory example
