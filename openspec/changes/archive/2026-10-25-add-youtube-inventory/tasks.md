## 1. Dependency and settings

- [x] 1.1 `uv add httpx` (runtime group). Verify `uv.lock` lists it as a direct dependency of the project and `make typecheck` passes with an `import httpx` in place.
- [x] 1.2 Add the YouTube settings to `config.py` (`youtube_api_key` as `SecretStr | None` with an empty value read as unset, `youtube_daily_quota`, `youtube_quota_margin`, `youtube_watch_reserve`, `youtube_refresh_after_days`, `youtube_request_timeout_seconds`) with validators: refresh age under 30, caps leaving the general pool something to spend. Verify with tests: settings load with no key; a refresh age of 30 is refused; a margin plus reserve reaching the quota is refused; neither error carries the key.
- [x] 1.3 Add `YOUTUBE_API_KEY` to `.env.example` (Russian comment: a secret on the api hash's footing), `youtube_api_key: ""` to `secrets.vault.yml.example`, and a block in `env.j2` skipped when the key is empty. Verify `make ansible-lint` passes.

## 2. Storage

- [x] 2.1 Add the seven enums and five models (`YtChannel`, `YtRawChannel`, `YtQuotaDay`, `YtQuotaExhausted`, `YtRequest`) to `db/models.py` per design.md, `status` reusing `channel_status`. Verify with tests that the database refuses a rejection without a reason, a seed without a kind or a language, and a malformed channel id.
- [x] 2.2 Generate and review the Alembic revision: it creates the YouTube types, does not create `channel_status`, and its downgrade drops the YouTube types but not `channel_status`. Verify `upgrade head`, `downgrade -1` and `upgrade head` again on a `_test` database, and that autogenerate then reports no difference.
- [x] 2.3 Backups: add `yt_channels` to `INVENTORY_TABLES`; give every full dump `--exclude-table-data='yt_raw_*'`. Verify with tests on the built `pg_dump` command for both tiers, and add the restore note (payloads come back with the next refresh) to `src/itgraph/README.md`.

## 3. Quota ledger

- [x] 3.1 Add the pure quota-day module: the Pacific date of a moment, the next reset, the caps from settings. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests at 06:59/07:00 UTC in summer, 07:30 UTC on 2026-11-02 (counts towards 1 November; reset 08:00 UTC), and the 23-hour day of 2027-03-14.
- [x] 3.2 Add `db/yt_quota.py`: booking (exhaustion check, conditional `UPDATE`, journal row with no outcome, one transaction), recording an outcome, recording exhaustion, the day's report. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: a booking raises the count; one past the cap is refused; nothing is refunded; two sessions racing for the last unit, exactly one wins; exhaustion refuses every pool until the next day.
- [x] 3.3 Create the `yt` typer sub-app and `itgraph yt quota`. Document it in `src/itgraph/README.md`. Verify with a test of its output (per pool, reset in UTC and local time, per command and method) and that it builds no HTTP client.

## 4. API client

- [x] 4.1 Add synthetic fixtures in `tests/ytfakes.py` (builders rather than JSON files, as `tests/fakes.py` is for Telegram): channel resources with invented ids and handles, Google error bodies for `quotaExceeded`, 404, 400 and 5xx, and a fake `channels.list` over `httpx.MockTransport`. The key in every test is `test-youtube-api-key`. Verify gitleaks passes without an exclusion.
- [x] 4.2 Add `yt/client.py`: the only `httpx.AsyncClient`, key in `x-goog-api-key`, the timeout, a booking per attempt, error classification, retries for 5xx and 429. Add its row to `src/itgraph/CLAUDE.md`. Verify with `MockTransport` tests: the header is sent and the URL is keyless; no log record holds the key at debug level; `quotaExceeded` records exhaustion and is not retried; a 5xx is retried and both attempts are counted; persistent 5xx stops; a 400 is not retried; an unreadable 403 is not taken for exhaustion; a missing key fails before any request; a configured MTProto proxy is not used.
- [x] 4.3 Add `yt/channels.py`: `channels.list` by ids (fifty per call; a 404 batch retried one id at a time), by handle and by legacy name, with the six parts. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: a hundred ids make two requests; a 404 batch still yields every live channel; an empty `items` and a 404 both mean not found.

## 5. Retention

- [x] 5.1 Add `db/yt_channels.py`: purge of payloads older than 30 days, payload replacement, gone and restore. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: a 31-day payload is removed and the record kept; a new payload replaces the old; no other table holds the title after removal.
- [x] 5.2 Run the purge first in every `yt` command, through `_run_yt`, the wrapper every one of them runs in — not the sub-app's callback, which click runs before a subcommand's `--help` and which made help need a database. Verify with a test that an offline command with no key configured still purges, and that every `yt … --help` opens no database.
- [x] 5.3 Add `yt/refresh.py` and `itgraph yt refresh`: missing or stale payloads of every status, gone channels included; batches; gone and restore; progress kept on exhaustion; log records carry channel ids only. Add its row to `src/itgraph/CLAUDE.md` and document the command in `src/itgraph/README.md`. Verify with tests for each of those, including a rejected channel being refreshed and a log capture holding no title.
- [x] 5.4 Add `itgraph-yt-refresh.service` and `.timer` (`OnCalendar=daily`, `Persistent=true`) to `deploy/templates/` and the playbook, enabled only when `youtube_api_key` is set. Verify `make ansible-lint` passes.

## 6. Import

- [x] 6.1 Add the pure entry parser: accepted and refused forms, every bad entry reported together with its line number and what to supply instead, normalization so forms and letter case collapse. Add its row to `src/itgraph/CLAUDE.md`. Verify with a table-driven test over every form in the spec, and a test that an input with three bad lines reports all three.
- [x] 6.2 Add `yt/add.py` and `itgraph yt add`: arguments, `--from-file PATH|-`, `--limit`, `--failures PATH|-`. Already-known entries cost nothing; two entries naming one channel make one record; existing reviews are untouched; no review is applied. Add its row to `src/itgraph/CLAUDE.md` and document the command in `src/itgraph/README.md`. Verify with tests for every scenario of "Seed Import", "Entry Forms", "Import Spending" and "Import Failures", including failures on standard output with the report on standard error.

## 7. Review

- [x] 7.1 Add the pure review-file module: render the header and rows, parse and validate collecting every error, read the export moment, refuse past 30 days. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: round trip; the six-mistake file from the spec reports all six; a missing header and an expired file are refused; tabs in a title are flattened; values match case-insensitively.
- [x] 7.2 Add `itgraph yt review-export` (standard output or a path; unreviewed by default, `--status` filter; current values filled in; channels without a payload exported with id and link). Verify with tests of each, and that no HTTP client is built.
- [x] 7.3 Add `itgraph yt review-apply PATH|-` (one transaction; applied, unchanged, conflict, skipped; note mapping). Verify with tests: decisions applied; a second apply changes nothing; a row changed after export is a conflict while the others apply; read-only edits are ignored.
- [x] 7.4 Add `itgraph yt mark` (id or `@handle`; `--seed` requires `--kind` and `--lang`; `--reject --reason [--note]`; `--maybe`; replaces an earlier decision). Verify with tests for every "Single Review" scenario.
- [x] 7.5 Add `itgraph yt channels` (status filter; id, handle, title, status, kind, language; expired and gone shown as such; summary per status, seeds per language, API-data state). Verify with tests of rows and summary.
- [x] 7.6 Add `scripts/on-host.sh` and `*.review.tsv` to `.gitignore`. Verify with a test running the script against a stub `ssh` on `PATH`: an argument with spaces and quotes arrives intact, the remote exit status is returned, a missing inventory fails without connecting.
- [x] 7.7 Write "YouTube: разметка" in `src/itgraph/README.md` (Russian): the round trip through `on-host.sh`, the accepted values, the boundary table from design.md, deleting the file after applying it. Verify each documented command against its `--help`.

## 8. Project documents

- [x] 8.1 Root `CLAUDE.md`: add the YouTube exception under "The raw layer is immutable" in the wording agreed in explore; under "Never commit", add the YouTube API key and review files. Verify the wording matches design.md.
- [x] 8.2 `docs/PLAN.md`: the `search.list` cost, III.E.4.h(ii) and what it reaches, the derived-metrics page as of 2026-09-14, the operator's decision and account, `viewCount` since 2026-08-24, change 1's row in the order of changes. Verify every corrected figure against the sources table in design.md.

## 9. Integration

- [x] 9.1 Run `make validate`; fix everything it reports. Not done while it is red.
- [x] 9.2 Run `openspec validate add-youtube-inventory --strict`; fix everything it reports.

## Workflow follow-up

- The operator puts the key in the vault, runs the playbook (`.env`, timer, `alembic upgrade head` on the host), then makes the first real call: `scripts/on-host.sh yt add @<a handle they know>`, `yt channels`, `yt quota`. That settles the header, `customUrl` and the not-found shape (design.md, "Assumptions"); any surprise is a fix before the seed list is imported.
- The seed list with `--limit`, then the review round trip.
- `/opsx:verify`, then `/opsx:archive`. Commits go to `master`.
