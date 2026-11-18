# Tasks

Tasks marked **(operator)** reach a server, YouTube or the test environment, and are the operator's. The agent sends no request to YouTube, runs no CronJob, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`).
- Migrations are verified on a scratch database whose name ends in `_test`.
- No test makes a request; the YouTube client is faked where a pass needs one, and fake ids are spelled as obviously fake.
- Logs and reports name channel and video ids only — checked by a test wherever either is produced.

## 1. Schema

- [x] 1.1 One revision (design.md, Decisions 2 to 4):
  - `yt_raw_channel_sections` to the composite key `(channel_id, fetched_at)`, the rows present kept as they are;
  - the table `yt_raw_charts` (`region`, `fetched_at`, `payload`), keyed by both, the payload the trimmed answer in chart order;
  - the table `yt_lookup_runs` (`id`, `started_at`, `finished_at`, counts, outcome), the pattern of `yt_refresh_runs`;
  - the exporter's `SELECT (region, fetched_at)` on the charts and `SELECT (started_at, finished_at)` on the runs.

  The downgrade restores the single-column sections key keeping each seed's newest reading, and drops both tables. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; `tests/test_schema_names.py` green.

## 2. Featured readings append

- [x] 2.1 `config.py` gains `youtube_sections_after_days` (7, at least 1). `db/yt_sections.py`: `store_sections` inserts and never updates; `featured_lists` reads each seed's newest in-date reading; `sections_due` compares against the newest of any age. `yt/refresh.py` passes the sections their own age, not the channels'. Verify against the modified requirements of `youtube-featured-channels`: a second reading is stored beside the first; a list younger than the setting costs nothing; evidence reads the newest in-date reading; the day's section count in the refresh summary still reads true.
- [ ] 2.2 The first-reading baseline: a seed with one reading lists no appearance, and its ids still count as the standing queue reads them. Verify in the ranking's tests (group 5) and in `yt featured-candidates`' existing tests, which stay green.

## 3. The chart reading

- [x] 3.1 `config.py` gains `youtube_chart_regions` (`US`, `RU`) and `youtube_chart_category` (28). `yt/chart.py`: the `videos.list` `chart=mostPopular` request — the region, the category, fifty a page, `fields` naming each video's id, its channel's id and the page token — paged while a token comes. `db/yt_chart.py` stores each answer as a reading. Verify: two regions, two readings; paging over four pages of fifty, 200 a region, as the probe of 2026-11-18 measured; an empty chart stored as empty; a reading is never rewritten.
- [x] 3.2 `yt/refresh.py` reads the chart first, one region at a time, catching its API errors so a refused chart fails nothing else; `RefreshSummary` counts the readings. Verify: a refused region leaves the rest of the refresh completing; the pool-spent path still stops everything as before; the refresh's existing tests stay green.
- [x] 3.3 Docs: `src/itgraph/README.md`'s refresh section and the chart; the `yt/chart.py`, `db/yt_chart.py` rows of `src/itgraph/CLAUDE.md`.

## 4. The scheduled lookup pass

- [ ] 4.1 `config.py` gains `youtube_tg_lookup_daily_requests` and `youtube_mention_lookup_daily_requests` (10 each, at least 0). `yt/rising_lookups.py` runs `tg-lookup`'s pass then `mention-lookup`'s, each through its own code, each stopped after its setting's requests; `db/yt_lookup_runs.py` records each run — moments, counts, outcome, an empty run included. `cli.py` gains `yt rising-lookups`. Verify: both passes bounded; nothing to look up still records a run; a spent pool records a stopped run; the manual `tg-lookup` and `mention-lookup` tests stay green and their `--limit` behaviour is unchanged.
- [ ] 4.2 Docs: the command, the settings and the bounds in `src/itgraph/README.md`; the module row in `src/itgraph/CLAUDE.md`.

## 5. The rankings

- [ ] 5.1 `derive/yt_rising.py`, pure: rows and a moment in; the link ranking, the chart ranking and the counts out (design.md, Decision 1). Verify with unit tests, a scenario each, of "What Counts As Evidence", "An Appearance Is New In A Seed's List", "Families Across Both Platforms", "What Is Rising" and "How Rising Link Candidates Rank": one family however many links; a TG family and a YT family are two; old evidence outside the window; the newer first on a tie.
- [ ] 5.2 `db/yt_rising.py` loads the rows in one `REPEATABLE READ` transaction: both platforms' seeds with their families; the inventory's ids; the links with their posts' times; the references with their videos' times, resolved through payloads and lookups as the queues resolve them; the two newest featured readings per seed; the day's chart readings. Verify on a `_test` database: a link through a video's lookup; an appearance from two readings; a seed whose list is out of date counted; "Nothing Joined Is Stored" (no row changes).
- [ ] 5.3 The chart ranking's day rule: only a region's newest reading fetched on the Moscow day of computing counts, and a region without one is said. Verify with a reading of yesterday and one of today.

## 6. The report

- [ ] 6.1 `cli.py` gains `yt rising`: the link ranking's head, the chart ranking's rows, the counts — awaiting a lookup, not found, lists out of date. Verify: a ranked row of each kind shows ids, numbers and dates and nothing read from the API beyond ids; the command writes nothing and makes no request without a key; logs at debug level carry ids only.

## 7. Series

- [ ] 7.1 `metrics/series.py` gains `itgraph_youtube_chart_read_timestamp_seconds{region}` and `itgraph_youtube_rising_lookups_timestamp_seconds`. Verify: both served from a scrape with rows present and 0 with none; the exporter's role tests — anything of a chart reading but region and moment, and of a lookup run but its moments, refused.

## 8. Deploy

- [ ] 8.1 The CronJob `itgraph-yt-lookup` in `deploy/ansible/itgraph/vars/main.yml`, scheduled by `itgraph_yt_lookup_schedule` (`0 6 * * *` in `defaults/main.yml`), among the YouTube jobs: rendered only with an API key, its pod holding the configuration, the database's URL and the YouTube key and reaching those alone. Verify: `make ansible-lint`; the e2e render asserts the job exists with a key and not without one; `make test-e2e` on the throwaway cluster.

## 9. Docs and release

- [ ] 9.1 `src/itgraph/README.md`: the two rankings, the readings they read, the report, the unattended lookups. `deploy/runbook.md`: the two series and the alerts asked of the infrastructure repository. `docs/PLAN.md`: "Expanding the seed set" gains YouTube's rising flow.
