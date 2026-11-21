# Spec Delta

## ADDED Requirements

### Requirement: Text Embeddings Run In Every Environment

The text embedding job SHALL be scheduled wherever the role runs, whether or not the environment collects: it needs only the database and the image, which carries the model, so a test environment runs it too. It SHALL run daily at 04:00 Moscow time, on its own schedule input.

#### Scenario: A test environment

- **GIVEN** an environment that does not collect and has no YouTube key
- **WHEN** the role has run
- **THEN** the text embedding CronJob exists there, beside the scoring jobs

#### Scenario: What the job's pod holds

- **WHEN** the role renders the embedding job's CronJob
- **THEN** its pod has the configuration and the database's URL, no other secret, and may reach the database and nothing else

#### Scenario: A missed run catches up

- **GIVEN** a host that was down overnight
- **WHEN** it is back
- **THEN** the missed embedding run starts, and embeds what arrived while it was off
