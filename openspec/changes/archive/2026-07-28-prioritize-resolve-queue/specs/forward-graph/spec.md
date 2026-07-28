## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Reference Resolution

The system SHALL provide `itgraph resolve`, obtaining username and title for channels that entered the inventory by reference. The mention queue SHALL be worked in order of how many distinct channels mention each username, most first, so that a bounded run spends the daily quota on the references carrying the most independent evidence.

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
