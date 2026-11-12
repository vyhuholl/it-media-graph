## MODIFIED Requirements

### Requirement: YouTube Scoring Runs In Every Environment

The YouTube baseline refresh, scoring pass and weekly list SHALL be scheduled wherever the role runs, whether or not the environment has a YouTube key: the scoring every fifteen minutes after the poll loop's pass, the refresh daily after the refresh of YouTube data, and the weekly list on Mondays at 10:00. Each SHALL reach the database alone and hold no secret but the database's URL.

#### Scenario: A test environment

- **GIVEN** an environment that does not collect and has no YouTube key
- **WHEN** the role has run
- **THEN** the YouTube scoring, refresh and weekly list CronJobs exist there, and the YouTube collection CronJobs do not

#### Scenario: Production

- **GIVEN** an environment with a YouTube key
- **WHEN** the role has run
- **THEN** the YouTube scoring pass is scheduled at minutes 6, 21, 36 and 51 of every hour, the YouTube baseline refresh at 05:30, and the weekly list on Mondays at 10:00, Moscow time

#### Scenario: A weekly list run missed

- **GIVEN** a host that was down from Sunday to Wednesday
- **WHEN** it is back on Wednesday
- **THEN** the weekly list's missed run starts, and computes the week that ended on Monday

#### Scenario: What the weekly list's pod holds

- **WHEN** the role renders the weekly list's CronJob
- **THEN** its pod has the configuration and the database's URL, no YouTube key, bot token or other secret, and may reach the database and nothing else
