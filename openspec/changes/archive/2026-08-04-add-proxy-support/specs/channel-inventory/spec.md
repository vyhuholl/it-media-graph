## ADDED Requirements

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
