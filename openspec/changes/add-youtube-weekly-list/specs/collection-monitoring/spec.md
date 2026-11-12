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
| `itgraph_youtube_oldest_payload_age_seconds{kind}` | gauge | how long ago the oldest stored channel payload was read, `kind="channel"` only; absent when there is none |
| `itgraph_alerts_oldest_undelivered_seconds{platform}` | gauge | the oldest alert of that platform's queue not delivered, held ones included, 0 when none |
| `itgraph_alerts_failing{platform}` | gauge | undelivered alerts of that platform's queue past the failure-report attempts |
| `itgraph_baselines_completed_timestamp_seconds{platform}` | gauge | that platform's newest completed baseline run, 0 when none |
| `itgraph_baselines_channels{platform,state}` | gauge | that run's channels in scope (`state="in_scope"`) and with a baseline (`state="with_baseline"`), 0 when there is no run |
| `itgraph_youtube_weekly_list_computed_timestamp_seconds{route}` | gauge | when the newest weekly list of that language was computed, 0 when none |
| `itgraph_youtube_weekly_list_delivered_timestamp_seconds{route}` | gauge | when the newest delivered weekly list of that language went out, 0 when none |

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

#### Scenario: Baselines that stopped being refreshed

- **GIVEN** a newest completed YouTube baseline run three days old, and a Telegram one from this morning
- **WHEN** a client scrapes
- **THEN** `itgraph_baselines_completed_timestamp_seconds{platform="youtube"}` is the moment of the three-day-old run, and `{platform="telegram"}` this morning's

#### Scenario: A run in progress

- **GIVEN** a YouTube baseline run started and not completed, after a completed one
- **WHEN** a client scrapes
- **THEN** both baseline series of YouTube describe the completed run

#### Scenario: A list computed and not sent

- **GIVEN** this week's Russian list computed and undelivered, and last week's delivered
- **WHEN** a client scrapes
- **THEN** `itgraph_youtube_weekly_list_computed_timestamp_seconds{route="ru"}` is this week's moment and `itgraph_youtube_weekly_list_delivered_timestamp_seconds{route="ru"}` last week's

#### Scenario: No list yet

- **GIVEN** no weekly list of either language
- **WHEN** a client scrapes
- **THEN** both weekly list series are 0 for `route="en"` and `route="ru"`

### Requirement: Metrics Carry Nothing YouTube Said

No series SHALL carry a title, handle, description, channel id, video id or statistic read from YouTube. What the exporter reads of YouTube's tables SHALL be limited to when each row was fetched, enforced by the database role it connects as. Of a YouTube alert it SHALL read only its moments and delivery state; of a baseline run of either platform, only when it completed and how many channels it counted; of a weekly list, only its language and its moments, and nothing of its entries.

#### Scenario: The role cannot read a title

- **GIVEN** the exporter's database role
- **WHEN** it selects `payload` from any `yt_raw_*` table
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what an alert is about

- **GIVEN** the exporter's database role
- **WHEN** it selects the video, the reading or the score of a YouTube alert
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read a baseline

- **GIVEN** the exporter's database role
- **WHEN** it selects a channel's reference, a curve point or a factor of either platform
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what a list holds

- **GIVEN** the exporter's database role
- **WHEN** it selects any column of a weekly list's entries, or a list's counts or parameters
- **THEN** the database refuses with a permission error

#### Scenario: The role can write nothing

- **WHEN** the exporter's role attempts an insert, update or delete on any table
- **THEN** the database refuses

#### Scenario: A label names no channel

- **WHEN** a client scrapes
- **THEN** no label value in the body names a Telegram or YouTube channel, a video or a person
