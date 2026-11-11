# Spec Delta

## MODIFIED Requirements

### Requirement: The Series Come From What Is Already Recorded

Every series SHALL be worked out at scrape time from durable state in the database or from settings, not from counters inside another process, so that a stopped or stuck collector is measured the same as a working one. A series SHALL NOT need a sequential scan of `raw_messages` or `message_metrics`.

#### Scenario: A collector that stopped

- **GIVEN** a collector that last polled two hours ago, outside quiet hours
- **WHEN** a client scrapes
- **THEN** `itgraph_telegram_last_poll_timestamp_seconds` is that moment
- **AND** `itgraph_telegram_quiet_hours` is 0

#### Scenario: The series that are served

- **WHEN** a client scrapes
- **THEN** the body declares each of these

| Series | Type | From |
|---|---|---|
| `itgraph_telegram_last_poll_timestamp_seconds` | gauge | the newest poll recorded |
| `itgraph_telegram_oldest_overdue_seconds` | gauge | the poll queue's oldest due channel, 0 when none |
| `itgraph_telegram_failing_channels` | gauge | channels whose last poll failed |
| `itgraph_telegram_quiet_hours` | gauge | 1 inside the collector's quiet hours, else 0 |
| `itgraph_telegram_last_flood_timestamp_seconds` | gauge | the newest rate limit recorded, 0 when none |
| `itgraph_youtube_quota_spent_units{pool}` | gauge | this quota day's ledger |
| `itgraph_youtube_quota_cap_units{pool}` | gauge | the pool's cap |
| `itgraph_youtube_quota_exhausted{bucket}` | gauge | 1 when this quota day was declared over for that bucket |
| `itgraph_youtube_last_complete_pass_timestamp_seconds` | gauge | the newest poll pass that completed, 0 when none |
| `itgraph_youtube_oldest_payload_age_seconds{kind}` | gauge | the oldest fetch time of each kind of stored API data, absent when there is none |
| `itgraph_alerts_oldest_undelivered_seconds{platform}` | gauge | the oldest alert of that platform's queue not delivered, held ones included, 0 when none |
| `itgraph_alerts_failing{platform}` | gauge | undelivered alerts of that platform's queue past the failure-report attempts |

#### Scenario: Quiet hours

- **GIVEN** a moment inside the collector's quiet hours in Moscow time
- **WHEN** a client scrapes
- **THEN** `itgraph_telegram_quiet_hours` is 1

#### Scenario: The alert series per platform

- **GIVEN** an undelivered Telegram alert raised an hour ago and no undelivered YouTube alert
- **WHEN** a client scrapes
- **THEN** `itgraph_alerts_oldest_undelivered_seconds{platform="telegram"}` is about 3600 and `{platform="youtube"}` is 0

#### Scenario: An excluded YouTube alert

- **GIVEN** a YouTube alert closed as excluded
- **WHEN** a client scrapes
- **THEN** it counts in neither alert series
