# forward-graph Specification

## Purpose
TBD - created by archiving change add-forward-graph. Update Purpose after archive.
## Requirements
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

The system SHALL record an edge for every message forwarded from one channel into another, identifying the referenced message wherever the payload names it.

#### Scenario: Forward from a channel
- **GIVEN** a stored message forwarded from another channel
- **WHEN** derivation runs
- **THEN** an edge of kind `forward` is recorded from the referencing channel to the referenced one
- **AND** the edge carries the referencing message's id and publication date
- **AND** the edge carries the referenced message's id and its original publication date

#### Scenario: Forward naming no original message
- **GIVEN** a stored message forwarded from a channel whose payload does not name the original message
- **WHEN** derivation runs
- **THEN** the edge is recorded with its referenced-message fields left empty
- **AND** the edge is not discarded

#### Scenario: Forwarded album
- **GIVEN** several stored messages forming one forwarded album
- **WHEN** derivation runs
- **THEN** each message produces its own edge
- **AND** every one of those edges carries the same group identifier
- **AND** derivation does not merge them

#### Scenario: Forward from an individual
- **GIVEN** a stored message forwarded from a user rather than a channel
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Forward with a hidden origin
- **GIVEN** a stored message whose forward origin is withheld by its author's privacy settings
- **WHEN** derivation runs
- **THEN** no edge is recorded

### Requirement: Mention Edges

The system SHALL record an edge for every reference to a channel by username or by `t.me` link in a message, identifying the referenced message where the link names one.

#### Scenario: Username mention
- **GIVEN** a stored message containing an `@username` mention entity
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to the referenced channel
- **AND** its referenced-message fields are left empty

#### Scenario: Link to a channel
- **GIVEN** a stored message containing a `t.me` link to a channel
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to that channel
- **AND** its referenced-message fields are left empty

#### Scenario: Link to a single message
- **GIVEN** a `t.me` link pointing at one message within a channel
- **WHEN** derivation runs
- **THEN** an edge of kind `mention` is recorded to the channel
- **AND** the edge carries the referenced message's id

#### Scenario: References that are not channels
- **GIVEN** a mention or link that addresses a user, a bot, an invite, or a non-Telegram destination
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Links reserved for an action rather than a channel
- **GIVEN** a `t.me` link whose first path segment is one Telegram reserves
  for an action — adding a folder of channels, a sticker pack, an emoji or
  theme pack, setting a language, sharing, a proxy, a login, an invoice, a
  gift code, a contact, a boost
- **WHEN** derivation runs
- **THEN** no edge is recorded
- **AND** no channel named by that reserved word enters the inventory or the
  pending mentions, the word being a plausible username that would never resolve
- **AND** this holds whether the link carries its payload in the path
  (`t.me/addlist/<slug>`) or in the query string (`t.me/addlist?slug=<slug>`)

#### Scenario: The same post referenced twice in one message
- **GIVEN** a stored message referencing the same post of the same channel more than once
- **WHEN** derivation runs
- **THEN** one edge is recorded for that reference

#### Scenario: Different posts of one channel referenced in a single message
- **GIVEN** a stored message linking to two different posts of the same channel
- **WHEN** derivation runs
- **THEN** an edge is recorded for each referenced post

#### Scenario: A channel referenced both by name and by post link
- **GIVEN** a stored message that both mentions a channel by username and links to one of its posts
- **WHEN** derivation runs
- **THEN** two edges are recorded: one naming no post, one naming that post

### Requirement: Edges Record Observations

The system SHALL store one edge per observed reference, and MUST NOT store aggregated counts, weights or measures derived from its own fields.

#### Scenario: Repeated references over time
- **GIVEN** one channel referencing another in several messages
- **WHEN** derivation runs
- **THEN** each referencing message produces its own edge
- **AND** each edge carries the publication date of its message

#### Scenario: No precomputed measures
- **WHEN** an edge is stored
- **THEN** no weight, decay factor or aggregate count is stored with it
- **AND** no elapsed time between the referenced and referencing publication dates is stored, that interval being derivable from the two dates

#### Scenario: Grouping is recorded, not applied
- **WHEN** an edge comes from a message belonging to a group
- **THEN** the group identifier is stored on the edge
- **AND** derivation treats the edge no differently for belonging to a group

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

### Requirement: Reference Resolution

The system SHALL provide `itgraph resolve`, obtaining username and title for channels that entered the inventory by reference. The mention queue SHALL be worked in order of how many distinct channels mention each username, most first, so that a bounded run spends the daily quota on the references carrying the most independent evidence. A run MAY be narrowed to a single channel named by its Telegram id, in which case that channel is the whole run; the named channel SHALL still be subject to the queue's own rules, and a channel outside the queue SHALL be refused without a request being made.

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

#### Scenario: The most-mentioned username is resolved first

- **GIVEN** a pending username mentioned by three channels and another mentioned by one
- **WHEN** a resolution run bounded to one request works the mention queue
- **THEN** the username mentioned by three channels is the one resolved

#### Scenario: Equal evidence falls back to arrival order

- **GIVEN** two pending usernames with the same number of distinct sources
- **WHEN** resolution runs
- **THEN** the one seen first is resolved first
- **AND** a bounded run covers the same usernames in the same order when repeated against unchanged data

#### Scenario: A username with no recorded sources is worked last

- **GIVEN** a pending username with no recorded sources and another with one
- **WHEN** resolution runs
- **THEN** the one with a source is resolved first

#### Scenario: A username whose channel already exists is not requested

- **GIVEN** a pending username that matches a channel already in the inventory
- **WHEN** resolution runs
- **THEN** no request is made for it
- **AND** the daily quota is left for usernames that name channels not yet known

#### Scenario: Resolving by id clears the pending mention it makes redundant

- **GIVEN** a channel discovered by forward, and a pending username naming that same channel
- **WHEN** the channel resolves by id and its username is stored
- **THEN** the pending row for that username is removed
- **AND** no later run requests it

#### Scenario: The queue can be bounded by evidence

- **GIVEN** pending usernames mentioned by one, two and three channels
- **WHEN** resolution runs with a minimum of two sources
- **THEN** only the usernames mentioned by at least two channels are requested
- **AND** the rest stay pending for a later run

#### Scenario: An unfilled sources record is reported, not hidden

- **GIVEN** a non-empty mention queue for which no sources have been recorded
- **WHEN** resolution runs
- **THEN** the operator is told that derivation has not recorded sources yet and the queue is therefore in arrival order
- **AND** the run proceeds

#### Scenario: Resolution obeys collection limits

- **WHEN** resolution runs
- **THEN** requests are paced and made one at a time
- **AND** the gap before each request is drawn anew, the same way the collector draws it
- **AND** a FloodWait is waited out rather than circumvented
- **AND** a channel limit may bound the run

#### Scenario: A long FloodWait halts resolution

- **WHEN** Telegram returns a FloodWait longer than the configured halt threshold
- **THEN** resolution stops instead of sleeping through it
- **AND** neither queue is worked further
- **AND** what was resolved before the halt is reported and retained

#### Scenario: Derivation needs no network

- **WHEN** derivation runs
- **THEN** no request is made to Telegram

#### Scenario: A run can be narrowed to one named channel

- **GIVEN** several channels awaiting resolution and a non-empty mention queue
- **WHEN** resolution runs naming the Telegram id of one of those channels
- **THEN** exactly one request is made, for that channel
- **AND** no other channel awaiting resolution is requested
- **AND** no pending username is requested
- **AND** the outcome is recorded and reported the way an unnamed run records it

#### Scenario: A named id the inventory does not hold is refused

- **GIVEN** a Telegram id that matches no channel record
- **WHEN** resolution runs naming that id
- **THEN** the command exits with a non-zero status
- **AND** no request is made to Telegram
- **AND** no channel record is created

#### Scenario: A named channel that is already resolved is refused

- **GIVEN** a channel whose username and title are already stored
- **WHEN** resolution runs naming its id
- **THEN** the command exits with a non-zero status
- **AND** no request is made to Telegram
- **AND** the stored identity is left untouched

#### Scenario: A named channel that failed before is retried only when asked

- **GIVEN** a channel awaiting resolution that a previous run failed on
- **WHEN** resolution runs naming its id without being asked to retry past failures
- **THEN** the command exits with a non-zero status
- **AND** no request is made to Telegram
- **AND** the message says the channel failed before and how to retry it

#### Scenario: A named channel is retried when past failures are asked for

- **GIVEN** a channel awaiting resolution that a previous run failed on
- **WHEN** resolution runs naming its id and asking for past failures to be retried
- **THEN** exactly one request is made, for that channel

#### Scenario: Queue-shaping options are refused with a named channel

- **GIVEN** a request to resolve one named channel
- **WHEN** the run is also given a request limit or a minimum source count
- **THEN** the command exits with a non-zero status
- **AND** no request is made to Telegram

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

### Requirement: No Self-References

The system SHALL NOT record an edge whose source and target are the same channel, however the reference is expressed.

#### Scenario: Self-forward
- **GIVEN** a stored message a channel forwarded from itself
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Self-mention by username
- **GIVEN** a stored message in which a channel mentions its own `@username`
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Link to the channel's own page
- **GIVEN** a stored message containing a `t.me` link to the channel that published it
- **WHEN** derivation runs
- **THEN** no edge is recorded

#### Scenario: Link to the channel's own post
- **GIVEN** a stored message linking to a post of the channel that published it
- **WHEN** derivation runs
- **THEN** no edge is recorded
- **AND** this holds for the public `t.me/name/123` form and for the internal
  `t.me/c/<id>/<msg>` form alike

#### Scenario: A self-reference does not suppress the rest of the message
- **GIVEN** a stored message referencing both its own channel and a different one
- **WHEN** derivation runs
- **THEN** an edge is recorded for the other channel
- **AND** no edge is recorded for the publishing channel

### Requirement: Pending Mentions Record Their Sources

The system SHALL record which channels mention each pending username, as a set of distinct source channels rather than a count, and MUST keep that record re-derivable: a derivation pass over unchanged raw messages writes no new source.

#### Scenario: A pending mention records the channel that made it

- **GIVEN** an in-scope channel mentioning a username not yet known to the inventory
- **WHEN** derivation runs
- **THEN** the username is queued for resolution
- **AND** that channel is recorded as a source of the mention

#### Scenario: Two channels mentioning the same username are two sources

- **GIVEN** two different in-scope channels each mentioning the same unknown username
- **WHEN** derivation runs
- **THEN** the username has two recorded sources

#### Scenario: One channel mentioning a username repeatedly is one source

- **GIVEN** an in-scope channel mentioning the same unknown username in several messages
- **WHEN** derivation runs
- **THEN** the username has one recorded source

#### Scenario: Re-derivation adds nothing

- **GIVEN** a derivation pass has already recorded the sources of a pending mention
- **WHEN** derivation runs again over the same raw messages
- **THEN** no source row is added
- **AND** the recorded sources are unchanged

#### Scenario: Sources do not outlive the mention they describe

- **GIVEN** a pending username with recorded sources
- **WHEN** the username resolves into a channel
- **THEN** its recorded sources are removed with it

#### Scenario: A rebuild clears the sources with the queue

- **WHEN** derivation is asked to rebuild from scratch
- **THEN** the recorded sources are emptied along with the edges and the pending mentions
- **AND** the pass that follows records them again from the raw layer

