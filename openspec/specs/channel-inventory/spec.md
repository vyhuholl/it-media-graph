# channel-inventory Specification

## Purpose
TBD - created by archiving change add-channel-inventory. Update Purpose after archive.
## Requirements
### Requirement: Telegram Session Authentication

The system SHALL connect to Telegram using an existing Telethon session file and MUST NOT attempt an interactive login. The session SHALL be held by at most one process at a time: a command requiring Telegram access SHALL acquire an exclusive lease before connecting, SHALL refuse to run when the lease is held elsewhere, and MUST NOT hold a lease beyond the life of the process that took it.

#### Scenario: Authorized session present
- **GIVEN** a session file at the configured path belonging to an authorized account
- **WHEN** a command requiring Telegram access runs
- **THEN** the client connects and the command proceeds

#### Scenario: Session missing or unauthorized
- **GIVEN** no session file, or one whose account is not authorized
- **WHEN** a command requiring Telegram access runs
- **THEN** the command exits with a non-zero status
- **AND** the error points to the bootstrap instructions in the README
- **AND** no prompt for a phone number, code or password is shown

#### Scenario: A second command refuses rather than sharing the session
- **GIVEN** a process holding the session lease
- **WHEN** another command requiring Telegram access is started
- **THEN** it exits with a non-zero status without connecting
- **AND** the error says the session is in use and names the holder
- **AND** the running process is left undisturbed

#### Scenario: The refusal is immediate
- **GIVEN** a process holding the session lease
- **WHEN** another command requiring Telegram access is started
- **THEN** it does not wait for the lease to become free

#### Scenario: A lease does not outlive its process
- **GIVEN** a process holding the session lease
- **WHEN** it is killed without cleaning up
- **THEN** the lease becomes available to the next command
- **AND** no manual removal of a lock file, record or identifier is required

#### Scenario: Losing the lease stops the holder
- **GIVEN** a long-running process whose lease can no longer be confirmed
- **WHEN** it detects the loss
- **THEN** it stops rather than continuing to use the session

### Requirement: Channel Record

The system SHALL store one record per Telegram channel or chat, keyed by its Telegram id. A record MAY carry the family it belongs to; that field is written only by a confirmed review decision, and it distinguishes no member of the family from any other.

#### Scenario: Identity is recorded
- **WHEN** a channel record is created
- **THEN** the Telegram id is its primary key
- **AND** username, title, and whether the entity is a chat are stored when known
- **AND** the discovery source and a first-seen timestamp are recorded

#### Scenario: A channel known only by id
- **WHEN** a channel is recorded from a reference that carries no username or title
- **THEN** the record is created with its Telegram id alone
- **AND** it is marked as awaiting resolution

#### Scenario: A new record is unreviewed
- **WHEN** a channel record is created
- **THEN** its status is `candidate`
- **AND** kind, rejection reason and review timestamp are empty

#### Scenario: Rejection cannot be reasonless
- **WHEN** a record is written with status `rejected` and no rejection reason
- **THEN** the write is refused at the database level

#### Scenario: A new record belongs to no family
- **WHEN** a channel record is created
- **THEN** it belongs to no family
- **AND** no import, resolution or metadata pass writes its family

#### Scenario: The record names no main channel of its family
- **WHEN** a channel's family is read from its record
- **THEN** the answer identifies the family
- **AND** it does not identify any channel as the family's main one

### Requirement: Subscription Import

The system SHALL provide `itgraph dump-dialogs`, importing every channel and chat
the authorized account is subscribed to.

#### Scenario: First run populates the inventory
- **GIVEN** an empty inventory
- **WHEN** the command runs
- **THEN** every broadcast channel and group in the account's dialog list is inserted
- **AND** each record has discovery source `own_subscriptions` and status `candidate`
- **AND** the number of inserted records is reported

#### Scenario: Private dialogs are not imported
- **GIVEN** the dialog list contains direct messages, legacy group chats, and
  channels without a public username
- **WHEN** the command runs
- **THEN** none of them are inserted into the inventory
- **AND** the number of skipped dialogs is reported, without their titles

#### Scenario: Re-running preserves review work
- **GIVEN** an inventory in which some channels have already been reviewed
- **WHEN** the command runs again
- **THEN** username and title are refreshed from Telegram
- **AND** status, kind, rejection reason and review timestamp are left unchanged
- **AND** the discovery source of existing records is left unchanged

#### Scenario: Unsubscribing does not remove a record
- **GIVEN** a channel in the inventory that is no longer in the dialog list
- **WHEN** the command runs
- **THEN** the record is retained unchanged

### Requirement: Manual Review

The system SHALL provide `itgraph mark`, recording the review outcome for a single channel addressed by its Telegram id or by its username.

#### Scenario: Addressing a channel by username
- **WHEN** the channel is given as a username, with or without a leading `@`
- **THEN** the matching record is reviewed, matching case-insensitively

#### Scenario: A username held by two records
- **GIVEN** two records carry the same username, one of them stale
- **WHEN** that username is given
- **THEN** the command fails, naming both ids, and nothing is written

#### Scenario: Accepting a channel
- **WHEN** a channel is marked as in scope
- **THEN** its status becomes `seed`
- **AND** its kind is set to the given value, defaulting to `personal`
- **AND** its review timestamp is set

#### Scenario: Rejecting a channel
- **WHEN** a channel is rejected with a reason from the rejection enum
- **THEN** its status becomes `rejected` and the reason is stored
- **AND** an optional free-text note is stored alongside the reason
- **AND** its review timestamp is set

#### Scenario: Rejecting without a reason fails
- **WHEN** a channel is rejected and no reason is supplied
- **THEN** the command fails and nothing is written

#### Scenario: Deferring a decision
- **WHEN** a channel is marked as undecided
- **THEN** its status becomes `maybe` and its review timestamp is set

#### Scenario: Reviewing an unknown channel
- **WHEN** the given id or username is not in the inventory
- **THEN** the command fails and nothing is written

#### Scenario: Linked discussion chats are not reviewed independently
- **GIVEN** a chat whose parent channel is recorded
- **WHEN** the review queue is built
- **THEN** the chat is excluded from it
- **AND** no human decision about the chat is required
- **AND** this exemption governs review only: whether the chat's contents are collected is decided separately by each collecting capability

#### Scenario: Unresolved channels are not queued
- **GIVEN** a channel awaiting resolution and therefore having no username or title
- **WHEN** the review queue is built
- **THEN** it is excluded from the queue
- **AND** it enters the queue once resolved

### Requirement: Records Are Never Deleted

The system SHALL retain every discovered channel, including rejected ones, and MUST NOT delete channel records.

#### Scenario: Rejected channels remain queryable
- **WHEN** the inventory is queried without a status filter
- **THEN** rejected channels are included in the result

### Requirement: Inventory Listing

The system SHALL provide `itgraph channels`, showing the inventory so review
progress is visible.

#### Scenario: Listing by status
- **WHEN** the command runs with a status filter
- **THEN** only channels with that status are listed
- **AND** each row shows id, username, title, status and kind

#### Scenario: Progress summary
- **WHEN** the command runs without a filter
- **THEN** a count per status is reported

#### Scenario: Listing one family
- **GIVEN** any channel of a family
- **WHEN** the command runs filtered to that channel's family
- **THEN** every channel belonging to it is listed
- **AND** the same set is listed whichever member of the family was named

#### Scenario: Families are visible in the summary
- **WHEN** the command runs without a filter
- **THEN** it reports how many families are recorded
- **AND** how many channels belong to one
- **AND** a family of one unaffiliated channel is counted in neither

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

### Requirement: Outbound Connection

The system SHALL route every Telegram MTProto connection through the configured proxy when one is configured, and MUST NOT fall back to a direct connection for any reason. A proxy that cannot be reached SHALL fail the command.

Falling back would produce a collector that runs correctly while reaching Telegram from an address the operator believes it is not using — a failure whose symptom is that everything appears to work. The account this protects cannot be replaced by making another one.

#### Scenario: A configured proxy is used

- **GIVEN** a complete proxy configuration
- **WHEN** a command establishes a Telegram connection
- **THEN** the connection is made through that proxy

#### Scenario: An unreachable proxy stops the command

- **GIVEN** a configured proxy that cannot be reached
- **WHEN** a command attempts to connect
- **THEN** the command exits with a non-zero status
- **AND** no connection to Telegram is made by any other route

#### Scenario: No proxy configured means a direct connection

- **GIVEN** no proxy configuration
- **WHEN** a command establishes a Telegram connection
- **THEN** it connects directly
- **AND** nothing about the connection differs from before a proxy was supported

#### Scenario: The route taken is reported

- **WHEN** a Telegram connection is established
- **THEN** the log states whether it went direct or through a proxy
- **AND** where a proxy was used, it names the host and port
- **AND** it never records the proxy password

#### Scenario: Only Telegram's own protocol is proxied

- **GIVEN** a configured proxy
- **WHEN** the alert bot connects to the Bot API
- **THEN** it connects directly
- **AND** the proxy is not required for the bot to run

#### Scenario: An incomplete proxy configuration is refused before anything connects

- **GIVEN** a proxy configuration missing a host, a port, or naming an unsupported type
- **WHEN** settings are loaded
- **THEN** loading fails with an error naming what is wrong
- **AND** no command reaches the point of connecting

#### Scenario: Credentials are optional

- **GIVEN** a proxy configured with a host and port and no credentials
- **WHEN** a command connects
- **THEN** the connection is attempted through that proxy without authentication

