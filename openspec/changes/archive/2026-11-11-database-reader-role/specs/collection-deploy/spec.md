# Spec Delta

## MODIFIED Requirements

### Requirement: Each Workload Holds Only Its Own Secrets

Each workload's pods SHALL receive only the configuration that workload reads. A changed value SHALL restart only the long-running workloads that read it.

#### Scenario: The bot's pod

- **WHEN** the bot's pod is rendered
- **THEN** it holds its own database role's URL, the bot's token and the chat id
- **AND** no superuser URL, Telegram API credential, proxy password, B2 key or YouTube key

#### Scenario: The analysis jobs

- **WHEN** the detection and baseline jobs are rendered
- **THEN** they hold the database's URL and no other secret

#### Scenario: The collector

- **WHEN** the collector's pod is rendered
- **THEN** it holds the database's URL, the Telegram API credentials and the proxy's settings
- **AND** no bot token, B2 key or YouTube key

#### Scenario: A new bot token

- **WHEN** the bot's token changes and the role runs
- **THEN** the bot restarts and the collector does not

#### Scenario: A new password for the bot's own role

- **WHEN** the password of the bot's database role changes and the role runs
- **THEN** the bot restarts and the collector does not

#### Scenario: The reader's password

- **WHEN** any pod, Job or Secret's data is rendered
- **THEN** none holds the reader's password

#### Scenario: A new password for the reader

- **WHEN** the reader's password is set, changed or taken away and the role runs
- **THEN** no workload restarts, and the run reports none restarted

#### Scenario: A process that needs no Telegram credentials

- **GIVEN** a process with no Telegram API credentials configured
- **WHEN** it starts and never builds a Telegram client
- **THEN** its settings load

#### Scenario: A Telegram command without them

- **GIVEN** no Telegram API credentials configured
- **WHEN** a command builds a Telegram client
- **THEN** it exits non-zero naming what is missing, before any connection

### Requirement: Operators Reach The Database At A Fixed Address

The database SHALL also be served at a fixed ClusterIP, given as an input, where an operator's SSH tunnel to their own account ends. No database port SHALL be open to the internet.

#### Scenario: A tunnel from an operator's own account

- **GIVEN** an operator's account on the host
- **WHEN** they connect through an SSH tunnel to the fixed address with a database role's password
- **THEN** the connection is accepted

#### Scenario: A tunnel as the reader

- **GIVEN** a deploy given the reader's password
- **WHEN** an operator connects through the tunnel to the fixed address as `itgraph_reader` with that password
- **THEN** the connection is accepted, and the session reads the database and is refused any write

#### Scenario: No credential

- **WHEN** something connects to the fixed address with no valid password
- **THEN** the database refuses it
