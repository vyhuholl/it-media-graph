## MODIFIED Requirements

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
