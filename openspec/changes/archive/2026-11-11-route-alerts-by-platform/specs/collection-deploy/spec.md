# Spec Delta

## MODIFIED Requirements

### Requirement: A Deploy Is A Role Run With Its Inputs

A deploy SHALL be a run of the role in this repository, by an operator, at a release pinned by the repository that owns the host. Everything about where it runs SHALL be an input: the role SHALL name no host and no repository. It SHALL check its inputs before it changes anything.

#### Scenario: A missing or malformed input

- **GIVEN** an input missing, or an image not named as `repo:tag@sha256:…`
- **WHEN** the role runs
- **THEN** it fails naming the input, before any call that changes the cluster

#### Scenario: A YouTube chat without both its topics

- **GIVEN** a YouTube alert chat given without its English or its Russian topic, or a topic given without the chat
- **WHEN** the role runs
- **THEN** it fails naming the inputs, before any call that changes the cluster

#### Scenario: No YouTube chat

- **GIVEN** none of the YouTube alert chat and its topics given
- **WHEN** the role runs
- **THEN** it deploys, and the bot's pod holds no YouTube chat or topic

#### Scenario: Nothing deploys by itself

- **WHEN** a commit is pushed to `master` or a tag is pushed
- **THEN** nothing in any cluster changes

#### Scenario: A second run changes nothing

- **GIVEN** a deploy that has just finished
- **WHEN** the role runs again with the same inputs
- **THEN** no workload is restarted, and every apply reports no change

#### Scenario: A check

- **WHEN** the role runs in check mode
- **THEN** it shows the changes it would make and applies none

### Requirement: Each Workload Holds Only Its Own Secrets

Each workload's pods SHALL receive only the configuration that workload reads. A changed value SHALL restart only the long-running workloads that read it.

#### Scenario: The bot's pod

- **WHEN** the bot's pod is rendered
- **THEN** it holds its own database role's URL, the bot's token, the Telegram chat's id and, where given, the YouTube chat's id and its two topics' ids
- **AND** no superuser URL, Telegram API credential, proxy password, B2 key or YouTube key

#### Scenario: The analysis jobs

- **WHEN** the detection and baseline jobs are rendered
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
