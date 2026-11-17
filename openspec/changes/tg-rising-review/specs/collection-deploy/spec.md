# Spec Delta

## ADDED Requirements

### Requirement: Telegram's Rising Selection Runs In Every Environment

The daily selection of Telegram's rising channels SHALL be scheduled wherever the role runs, whether or not the environment collects, every day at 11:00 Moscow time. A run missed while the host was down SHALL start when it is back, within the day. Its pod SHALL reach the database alone and hold no secret but the database's URL.

#### Scenario: A test environment

- **GIVEN** an environment that does not collect
- **WHEN** the role has run
- **THEN** the selection's CronJob exists there

#### Scenario: Production

- **WHEN** the role has run in an environment that collects
- **THEN** the selection is scheduled daily at 11:00, Moscow time

#### Scenario: A run missed

- **GIVEN** a host that was down from 10:00 to 15:00
- **WHEN** it is back
- **THEN** the missed run starts, and computes that day's selection

#### Scenario: What the pod holds

- **WHEN** the role renders the selection's CronJob
- **THEN** its pod has the configuration and the database's URL, no Telegram session, API hash, bot token, proxy password or other secret, and may reach the database and nothing else

### Requirement: The Detection Job Applies Decisions

Every run of the detection job SHALL apply the decisions taken on rising offers, beside derivation and scoring, so that a failure of either does not hold the other back. It SHALL do so in every environment, with the detection job's own configuration and secrets.

#### Scenario: Derivation fails

- **GIVEN** a decision taken on a card, and a detection run whose derivation fails
- **WHEN** that run ends
- **THEN** the decision is applied, and the job is reported failed

#### Scenario: What the pod holds

- **WHEN** the role renders the detection job
- **THEN** the container applying decisions holds the configuration and the database's URL and no other secret, and may reach the database and nothing else
