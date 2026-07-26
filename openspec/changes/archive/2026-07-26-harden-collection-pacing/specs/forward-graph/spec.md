## MODIFIED Requirements

### Requirement: Reference Resolution

The system SHALL provide `itgraph resolve`, obtaining username and title for channels that entered the inventory by reference.

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
