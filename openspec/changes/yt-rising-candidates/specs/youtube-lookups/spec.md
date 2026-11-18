# Spec Delta

## ADDED Requirements

### Requirement: The Scheduled Lookup Pass

The system SHALL provide `itgraph yt rising-lookups`, running both lookup passes unattended — `tg-lookup`'s, then `mention-lookup`'s — each as its own command runs it, and each stopped after the number of requests its setting gives: `youtube_tg_lookup_daily_requests` and `youtube_mention_lookup_daily_requests`, ten each by default. Each run SHALL be recorded with its moments, its counts and how it ended, an empty run included, and SHALL run daily wherever an API key is configured, a run missed while the host was off starting when it is back.

#### Scenario: Both passes run bounded

- **GIVEN** thirty names awaiting a lookup from Telegram links and five from descriptions
- **WHEN** the command runs with limits of ten and ten
- **THEN** the Telegram pass stops after its ten requests, the description pass spends six, and every answer is stored

#### Scenario: Nothing to look up is still a run

- **GIVEN** every name both queues would ask about already answered
- **WHEN** the command runs
- **THEN** no request is made, and the run is recorded with its empty counts

#### Scenario: The manual commands are unchanged

- **GIVEN** the scheduled pass having run this morning
- **WHEN** the operator runs `itgraph yt tg-lookup --limit 50`
- **THEN** it runs as before, with no limit but its own

#### Scenario: A scheduled run is not the operator's

- **GIVEN** the general pool spent for the day
- **WHEN** the scheduled pass starts
- **THEN** it makes no request and is recorded as stopped
- **AND** an operator's lookup command run after it is not refused for the pool's sake

#### Scenario: Scheduled only with a key

- **GIVEN** a host deployed without an API key
- **THEN** no scheduled lookup pass runs there
