## MODIFIED Requirements

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
