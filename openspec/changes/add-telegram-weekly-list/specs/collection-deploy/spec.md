# Spec Delta

## ADDED Requirements

### Requirement: Telegram's Weekly List Runs In Every Environment

Telegram's weekly list SHALL be scheduled wherever the role runs, whether or not the environment collects, on Mondays at 10:00 Moscow time. A run missed while the host was down SHALL start when it is back, within six days. Its pod SHALL reach the database alone and hold no secret but the database's URL.

#### Scenario: A test environment

- **GIVEN** an environment that does not collect
- **WHEN** the role has run
- **THEN** Telegram's weekly list CronJob exists there

#### Scenario: Production

- **WHEN** the role has run in an environment that collects
- **THEN** Telegram's weekly list is scheduled on Mondays at 10:00, Moscow time

#### Scenario: A run missed

- **GIVEN** a host that was down from Sunday to Wednesday
- **WHEN** it is back on Wednesday
- **THEN** the missed run starts, and computes the week that ended on Monday

#### Scenario: What the pod holds

- **WHEN** the role renders Telegram's weekly list CronJob
- **THEN** its pod has the configuration and the database's URL, no Telegram session, API hash, bot token, proxy password or other secret, and may reach the database and nothing else
