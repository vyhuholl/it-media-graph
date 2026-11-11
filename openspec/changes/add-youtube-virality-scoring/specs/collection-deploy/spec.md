## ADDED Requirements

### Requirement: YouTube Scoring Runs In Every Environment

The YouTube baseline refresh and scoring pass SHALL be scheduled wherever the role runs, whether or not the environment has a YouTube key, the scoring every fifteen minutes after the poll loop's pass and the refresh daily after the refresh of YouTube data.

#### Scenario: A test environment

- **GIVEN** an environment that does not collect and has no YouTube key
- **WHEN** the role has run
- **THEN** the YouTube scoring and refresh CronJobs exist there, and the YouTube collection CronJobs do not

#### Scenario: Production

- **GIVEN** an environment with a YouTube key
- **WHEN** the role has run
- **THEN** the YouTube scoring pass is scheduled at minutes 6, 21, 36 and 51 of every hour and the YouTube baseline refresh at 05:30, Moscow time

## MODIFIED Requirements

### Requirement: Each Workload Holds Only Its Own Secrets

Each workload's pods SHALL receive only the configuration that workload reads. A changed value SHALL restart only the long-running workloads that read it.

#### Scenario: The bot's pod

- **WHEN** the bot's pod is rendered
- **THEN** it holds its own database role's URL, the bot's token, the Telegram chat's id and, where given, the YouTube chat's id and its two topics' ids
- **AND** no superuser URL, Telegram API credential, proxy password, B2 key or YouTube key

#### Scenario: The analysis jobs

- **WHEN** the detection and baseline jobs, and the YouTube scoring and baseline jobs, are rendered
- **THEN** they hold the database's URL and no other secret

#### Scenario: The collector

- **WHEN** the collector's pod is rendered
- **THEN** it holds the database's URL, the Telegram API credentials and the proxy's settings
- **AND** no bot token, B2 key, YouTube key or alert chat id

#### Scenario: A new bot token

- **WHEN** the bot's token changes and the role runs
- **THEN** the bot restarts and the collector does not

#### Scenario: A new YouTube topic

- **WHEN** a YouTube alert topic's id changes and the role runs
- **THEN** the bot restarts and no other workload does

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

### Requirement: Egress Is Denied Unless Named

Every pod's outbound traffic SHALL be denied except what its workload needs. The collector SHALL reach DNS, the database and the proxy, and nothing else. Passes that make no request outside the cluster SHALL reach DNS and the database alone. Of the workloads, only the proxy, the bot, the YouTube collection jobs and the backup SHALL reach the internet; the YouTube scoring and baseline jobs SHALL NOT.

#### Scenario: The collector going direct

- **GIVEN** a pod with the collector's labels and no proxy settings
- **WHEN** it opens a connection to an address on the internet
- **THEN** the connection does not leave the namespace

#### Scenario: The collector through the proxy

- **WHEN** a pod with the collector's labels connects to the proxy
- **THEN** the connection is accepted

#### Scenario: An analysis job

- **WHEN** a detection or baseline pod, or a YouTube scoring or baseline pod, opens a connection to an address on the internet
- **THEN** the connection does not leave the namespace

#### Scenario: Short-lived pods that may leave

- **WHEN** an operator's console Job that asked for neither the session nor the backups, or the role's pod reading the address seen directly, connects to a public address
- **THEN** the connection is accepted, for as long as that pod lasts

#### Scenario: The database

- **WHEN** the database's pod opens a connection anywhere
- **THEN** the connection does not leave the pod
