# collection-monitoring Specification

## Purpose
What the project tells the shared monitoring: which series it serves, where they come from, on which port, who may read them, and that they carry no API data. The alerts and the dashboard built on them belong to the infrastructure repository.

## Requirements

### Requirement: The Series Are Served By A Process Of Their Own

The system SHALL serve its series from one read-only process at `GET /metrics` on port 9090, in Prometheus's text format. That port SHALL serve no other path or method. A scrape SHALL change nothing and make no request to Telegram, YouTube or B2. Neither the collector nor the bot SHALL open a listening port.

#### Scenario: A scrape

- **GIVEN** the exporter running against a database with rows in every table it reads
- **WHEN** a client sends `GET /metrics` to port 9090
- **THEN** the response is 200 with Prometheus's text content type, and declares each series with its type
- **AND** no row in the database has changed

#### Scenario: Another path or method

- **WHEN** a client sends `GET /` or `POST /metrics` to port 9090
- **THEN** the response is 404 or 405, and no query is made

#### Scenario: The database does not answer

- **GIVEN** a database that refuses connections
- **WHEN** a client scrapes
- **THEN** the response is 503, so that the shared alert on a source of metrics down fires
- **AND** the process keeps running

#### Scenario: Nothing else listens

- **WHEN** the collector and the bot are running
- **THEN** neither has a listening socket

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

### Requirement: Metrics Carry No API Data

No series SHALL carry a title, handle, description, channel id, video id or statistic read from YouTube, and none SHALL aggregate statistics across channels. What the exporter reads of API data SHALL be limited to when each row was fetched, enforced by the database role it connects as. Of a YouTube alert it SHALL read only its moments and delivery state.

#### Scenario: The role cannot read a title

- **GIVEN** the exporter's database role
- **WHEN** it selects `payload` from any `yt_raw_*` table
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what an alert is about

- **GIVEN** the exporter's database role
- **WHEN** it selects the video, the reading or the score of a YouTube alert
- **THEN** the database refuses with a permission error

#### Scenario: The role can write nothing

- **WHEN** the exporter's role attempts an insert, update or delete on any table
- **THEN** the database refuses

#### Scenario: A label names no channel

- **WHEN** a client scrapes
- **THEN** no label value in the body names a Telegram or YouTube channel, a video or a person

### Requirement: Only The Monitoring's Scraper Reads The Series

The metrics port SHALL admit only pods with the labels given as an input, in the namespace given as an input. No route and no public address SHALL lead to it.

#### Scenario: The scraper

- **GIVEN** a pod with the scraper's labels in the scraper's namespace
- **WHEN** it connects to the metrics port
- **THEN** the connection is accepted

#### Scenario: Anything else

- **WHEN** a pod without those labels, in any namespace, connects to the metrics port
- **THEN** the connection is refused or dropped

### Requirement: A Test Environment Serves The Same Series

The exporter SHALL run wherever the role runs, so that its series, its role's grants and its network policy are tried in a test environment before production, on restored data.

#### Scenario: Restored data with no collector

- **GIVEN** a test environment on a restored copy, with the collector at zero replicas and no YouTube key
- **WHEN** a client scrapes
- **THEN** the response is 200
- **AND** no `itgraph_youtube_oldest_payload_age_seconds` series is present, since a dump carries no API data
