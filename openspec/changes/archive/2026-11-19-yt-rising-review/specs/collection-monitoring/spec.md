# Spec Delta

## MODIFIED Requirements

### Requirement: The Series Come From What Is Already Recorded

Every series SHALL be worked out at scrape time from durable state in the database or from settings, not from counters inside another process, so that a stopped or stuck collector is measured the same as a working one. A series SHALL NOT need a sequential scan of `tg_raw_messages` or `tg_message_metrics`.

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
| `itgraph_youtube_last_complete_refresh_timestamp_seconds` | gauge | when the newest refresh that completed ended, 0 when none |
| `itgraph_youtube_oldest_payload_age_seconds{kind}` | gauge | how long ago the oldest stored channel payload was read, `kind="channel"` only; absent when there is none |
| `itgraph_youtube_rising_lookups_timestamp_seconds` | gauge | when the newest scheduled lookup pass ended, 0 when none |
| `itgraph_alerts_oldest_undelivered_seconds{platform}` | gauge | the oldest alert of that platform's queue not delivered, held ones included, 0 when none |
| `itgraph_alerts_failing{platform}` | gauge | undelivered alerts of that platform's queue past the failure-report attempts |
| `itgraph_baselines_completed_timestamp_seconds{platform}` | gauge | that platform's newest completed baseline run, 0 when none |
| `itgraph_baselines_channels{platform,state}` | gauge | that run's channels in scope (`state="in_scope"`) and with a baseline (`state="with_baseline"`), 0 when there is no run |
| `itgraph_youtube_weekly_list_computed_timestamp_seconds{route}` | gauge | when the newest weekly list of that language was computed, 0 when none |
| `itgraph_youtube_weekly_list_delivered_timestamp_seconds{route}` | gauge | when the newest delivered weekly list of that language went out, 0 when none |
| `itgraph_telegram_weekly_list_computed_timestamp_seconds` | gauge | when Telegram's newest weekly list was computed, 0 when none |
| `itgraph_telegram_weekly_list_delivered_timestamp_seconds` | gauge | when Telegram's newest delivered weekly list went out, 0 when none |
| `itgraph_telegram_seeds_awaiting_history` | gauge | seeds in scope whose history walk is not complete |
| `itgraph_telegram_oldest_awaiting_history_seconds` | gauge | how long ago the earliest of them was marked a seed, 0 when none |
| `itgraph_telegram_resolutions_24h{command}` | gauge | resolutions recorded in the last 24 hours, per command that made them: `resolve`, `add`, `watch`; 0 for a command that made none |
| `itgraph_telegram_rising_offers_computed_timestamp_seconds` | gauge | when the newest day's selection of rising channels was computed, an empty one included; 0 when none |
| `itgraph_telegram_rising_offers_oldest_undelivered_seconds` | gauge | how long the oldest rising offer not yet delivered has waited since it was offered; 0 when none |
| `itgraph_telegram_rising_decisions_oldest_unapplied_seconds` | gauge | how long the oldest decision on a rising offer has waited to be applied since it was taken; 0 when none |
| `itgraph_youtube_rising_offers_computed_timestamp_seconds` | gauge | when the newest day's selection of YouTube's rising channels was computed, an empty one included; 0 when none |
| `itgraph_youtube_rising_offers_oldest_undelivered_seconds` | gauge | how long the oldest YouTube rising offer not yet delivered has waited since it was offered; 0 when none |
| `itgraph_youtube_rising_decisions_oldest_unapplied_seconds` | gauge | how long the oldest decision on a YouTube rising offer has waited to be applied since it was taken; 0 when none |

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

#### Scenario: Telegram's list computed and not sent

- **GIVEN** this week's Telegram list computed and undelivered, and last week's delivered
- **WHEN** a client scrapes
- **THEN** `itgraph_telegram_weekly_list_computed_timestamp_seconds` is this week's moment and `itgraph_telegram_weekly_list_delivered_timestamp_seconds` last week's

#### Scenario: No Telegram list yet

- **GIVEN** no Telegram weekly list
- **WHEN** a client scrapes
- **THEN** both Telegram weekly list series are 0

#### Scenario: A refresh that stopped for quota

- **GIVEN** a refresh that completed yesterday and one today that stopped for quota
- **WHEN** a client scrapes
- **THEN** `itgraph_youtube_last_complete_refresh_timestamp_seconds` is when yesterday's ended

#### Scenario: No refresh yet

- **GIVEN** no refresh recorded
- **WHEN** a client scrapes
- **THEN** `itgraph_youtube_last_complete_refresh_timestamp_seconds` is 0

#### Scenario: The lookups of the day

- **GIVEN** a scheduled lookup pass that ended at 06:00
- **WHEN** a client scrapes
- **THEN** `itgraph_youtube_rising_lookups_timestamp_seconds` is 06:00

#### Scenario: Seeds awaiting their walk

- **GIVEN** two seeds marked three hours and one hour ago, neither walked, and a seed whose walk is complete
- **WHEN** a client scrapes
- **THEN** `itgraph_telegram_seeds_awaiting_history` is 2
- **AND** `itgraph_telegram_oldest_awaiting_history_seconds` is about 10800

#### Scenario: What is out of scope does not wait

- **GIVEN** a rejected channel, a discussion chat and a seed whose walk failed permanently, none walked
- **WHEN** a client scrapes
- **THEN** both series are 0

#### Scenario: Resolutions by command

- **GIVEN** three resolutions by `resolve` and two by `watch` in the last 24 hours, and one by `add` a day and a half ago
- **WHEN** a client scrapes
- **THEN** `itgraph_telegram_resolutions_24h` is 3 for `command="resolve"`, 2 for `command="watch"` and 0 for `command="add"`

#### Scenario: The rising selection and its decisions

- **GIVEN** today's selection computed at 11:00 with nothing offered, an offer of yesterday's not delivered, and a decision taken an hour ago and not applied
- **WHEN** a client scrapes
- **THEN** `itgraph_telegram_rising_offers_computed_timestamp_seconds` is today's 11:00
- **AND** `itgraph_telegram_rising_offers_oldest_undelivered_seconds` is how long ago yesterday's selection was computed
- **AND** `itgraph_telegram_rising_decisions_oldest_unapplied_seconds` is about 3600

#### Scenario: YouTube's rising selection and its decisions

- **GIVEN** today's YouTube selection computed at 11:00 with nothing offered, an offer of yesterday's not delivered, and a decision taken an hour ago and not applied
- **WHEN** a client scrapes
- **THEN** `itgraph_youtube_rising_offers_computed_timestamp_seconds` is today's 11:00
- **AND** `itgraph_youtube_rising_offers_oldest_undelivered_seconds` is how long ago yesterday's selection was computed
- **AND** `itgraph_youtube_rising_decisions_oldest_unapplied_seconds` is about 3600

### Requirement: The Exporter Reads Only Moments And Outcomes

Of a YouTube alert, the exporter SHALL read only its moments and delivery state; of a baseline run of either platform, only when it completed and how many channels it counted; of a YouTube weekly list, only its language and its moments, and of a Telegram weekly list only its moments, and nothing of either's entries or second wind entries; of a refresh's record, only when it ended and how; of a resolution's record, only when it was made and by which command; of either platform's rising selection, only when a day was computed and when each offer was offered, delivered, decided and applied; of a scheduled lookup run, only its moments. Each limit SHALL be enforced by the database role it connects as.

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
- **WHEN** it selects any column of a weekly list's entries or second wind entries of either platform, or a list's week, counts or parameters
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read a refresh's counts

- **GIVEN** the exporter's database role
- **WHEN** it selects how many late readings a refresh found due or took, or its error
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what a resolution found

- **GIVEN** the exporter's database role
- **WHEN** it selects a resolution's kind or outcome
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read what was offered or decided

- **GIVEN** the exporter's database role
- **WHEN** it selects a rising offer's channel, rank, numbers, decision, kind or reason, or a day's date
- **THEN** the database refuses with a permission error

#### Scenario: The role cannot read a lookup run's counts

- **GIVEN** the exporter's database role
- **WHEN** it selects a scheduled lookup run's counts or how it ended
- **THEN** the database refuses with a permission error
