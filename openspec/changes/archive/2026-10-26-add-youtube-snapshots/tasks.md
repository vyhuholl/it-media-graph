## 1. Settings and pure arithmetic

- [x] 1.1 Give `schedule.next_sample_at` a `horizon` parameter defaulting to Telegram's. Verify every existing test in `tests/test_schedule.py` and `tests/test_watch.py` passes unchanged, plus a new test with an explicit horizon.
- [x] 1.2 Add the `youtube_watch_*` settings from design.md ("Settings") with validators: offsets non-empty and ascending, horizon strictly past the last offset. Verify with tests: defaults load; a horizon at the last offset is refused, naming both settings.
- [x] 1.3 Add `day_start` to `yt_quota.py` and the pure `yt_schedule.py`: origin, next reading or finished, upcoming checks (scheduled start, hourly, given up after 7 days, capped by the refresh age), pace, listing backoff. Add its row to `src/itgraph/CLAUDE.md`. Verify with table-driven tests covering every "Reading Schedule", "The Age Of A Reading" and "Upcoming Broadcasts Are Not Read" scenario, plus pace on the 25-hour day of 2026-11-01 and the 23-hour day of 2027-03-14.
- [x] 1.4 Add the pure `derive/yt_videos.py`: a video payload read as the facts the schedule needs (publication, actual start, scheduled start, broadcast state, channel id) and the three counters (strings to `int`, absent to `None`, booleans refused). Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: a missing like count is `None`; 3,000,000,000 views survive; a completed stream yields its actual start.

## 2. Storage

- [x] 2.1 Add `YtQuotaPool.WATCH` and `YtCommand.WATCH`, and revision A adding both values, alone, with an empty downgrade carrying the docstring of `b4e1d7c93af2`. Verify `upgrade head` on a `_test` database.
- [x] 2.2 Add the models and revision B from design.md ("The tables"): the four tables, `yt_discovery_path`, `yt_pass_outcome`, the generated `channel_id`, the cascade, the id `CHECK`, and the trim of stored channel payloads to the declared fields. Verify `upgrade head`, `downgrade -1`, `upgrade head` on a `_test` database only, and that autogenerate then reports no difference. Add tests: the generated column follows a replaced payload; deleting a video deletes its readings; a malformed video id is refused; the trim leaves exactly the declared keys.
- [x] 2.3 Verify with tests on the built `pg_dump` command that a full dump carries no rows of `yt_raw_videos` or `yt_raw_video_stats`, and does carry `yt_channel_polls` and `yt_watch_passes`. Update the restore note in `src/itgraph/README.md`: videos and readings come back only as new readings.
- [x] 2.4 Add `db/yt_videos.py`: storing a response's payloads and readings under one moment, writing `due_at`, selecting due videos (seed channels only, youngest first, older ones left out on request), known ids, removing videos YouTube did not return, and expiry of payloads and readings including the acceptance branch. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests for "Readings Are Observations", "A Reading Holds Counters Only", "Removing A Video Removes Everything About It", and both acceptance scenarios with `DERIVED_METRICS_ACCEPTED_ON` patched.
- [x] 2.5 Add `db/yt_watch.py`:
  - channel poll rows: the owed listing written in the same transaction as the payload, `listed_at`, failures, backoff;
  - pass records;
  - the lock: `pg_try_advisory_lock(bigint)` on a dedicated `NullPool` connection;
  - the status queries.

  Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: a second lock attempt fails while the first is held; the Telegram session lease can be taken while it is held, and vice versa; a closed connection releases it.
- [x] 2.6 Extend `_expire_youtube_data` to video payloads and readings, and add `DERIVED_METRICS_ACCEPTED_ON = None` to `yt_policy.py` with the comment from design.md. Verify with tests: an offline `yt` command with no key removes a 31-day video and a 31-day reading; with the constant patched, a reading taken on the date survives at 31 days and one taken the day before does not; `yt … --help` still opens no database.

## 3. Ledger and requests

- [x] 3.1 Choose the pool from the command in the ledger (`WATCH` → `WATCH`, everything else → `GENERAL`), with `pool_cap(WATCH)` = `youtube_watch_reserve`. Verify with tests for every "Reserved Share" scenario, including the quota report listing both pools.
- [x] 3.2 Add `playlistItems.list` and `videos.list` to `COSTS`; give `channels.list` the parts and `fields` from design.md; add `fields` constants for the two new methods. Update `tests/ytfakes.py`: the channel resource in the declared shape; builders for uploads listings and videos (upcoming, live, completed, hidden counters, invented eleven-character ids spelt as fakes). Verify with `MockTransport` tests that every request carries its `part` and `fields`, and that gitleaks passes without an exclusion.
- [x] 3.3 Add `yt/uploads.py`: one listing, `maxResults=50`, ids and publication times; 404 and non-quota 403 surfaced as the channel's failure. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: the newest page only; a 404 is a channel failure; a 400 stops.
- [x] 3.4 Add `yt/videos.py`: by ids, fifty to a request; a batch answered 404 asked again id by id. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests: sixty ids make two requests; a missing id is absent from the result; a 404 batch still yields every live video.

## 4. The pass

- [x] 4.1 Add `yt/watch.py` detection: every seed by id, the count compared before the payload is replaced, debts written in that transaction, gone and back. Add its row to `src/itgraph/CLAUDE.md`. Verify with tests for every "Watched Channels" and "New Uploads Are Noticed By The Video Count" scenario, and "The poll loop finds a channel gone".
- [x] 4.2 Listing: owed and first listings, daily checks deferred when ahead of pace, backoff, the candidate rule. Verify with tests for every "Every Channel Is Checked Daily", "What Counts As A New Video" and "One Channel's Failure Stays With That Channel" scenario, and that a pass dying between listing and reading leaves the listing owed.
- [x] 4.3 Reading: candidates first, then due videos youngest first, without those over 7 days when ahead of pace; payload replaced and reading stored; upcoming, reassigned and missing videos; debts cleared once candidates are stored; discovery path recorded. Verify with tests for every scenario of "One Request Serves Payload And Reading", "Reading Schedule", "Upcoming Broadcasts Are Not Read", "Every Format Is Collected And None Is Classified", "A Video That Leaves The Channel", "Discovery Is Recorded", "Spending Is Paced Across The Quota Day" and "Videos That Disappear".
- [x] 4.4 Outcomes and the pass record:
  - a spent pool or an exhausted day exits 0 and names the reset;
  - a held lock exits 0, saying so;
  - an API failure exits 1;
  - every request's results are committed before the next.

  Verify with tests for "A Spent Pool Ends A Pass Quietly", "One Pass At A Time" and "Progress Is Kept Per Request", and that expiry ran in each.
- [x] 4.5 Verify with a log capture at debug level that a pass tracking and reading videos writes no title, description or count ("Logs Name Ids Only"), and that nothing but channel data, video data, readings and the loop's own rows was written ("The Loop Derives Nothing").
- [x] 4.6 Add `itgraph yt watch` to the `yt` sub-app and document it in `src/itgraph/README.md` (Russian): what one pass does, what a quiet exit means, running it by hand through `scripts/on-host.sh`. Verify the README against `--help`, and that `--help` opens no database.

## 5. Status

- [x] 5.1 Add `itgraph yt watch-status`: tracked videos, readings due and the oldest overdue, the last pass and what it deferred or failed, the watch pool against its cap and pace, discoveries per path over 7 days. Document it in `src/itgraph/README.md`. Verify with an output test and that no HTTP client is built.

## 6. Refresh after acceptance

- [x] 6.1 Extend `yt/refresh.py`: with an acceptance date recorded, fetch again videos that are past the horizon and keep readings longer than 30 days, once their payload is older than the refresh age, fifty to a request, from the general pool, removing those YouTube no longer returns. Verify with tests for both scenarios of "Videos Are Refreshed While Their Readings Are Kept", with the constant patched and unpatched.

## 7. Deploy

- [x] 7.1 Add `itgraph-yt-watch.service` (oneshot, `TimeoutStartSec=10min`) and `.timer` (`OnCalendar` from `yt_watch_schedule`, default `*:0/15`, `Persistent=true`) to `deploy/templates/`. Install them with the other units and enable them only when `youtube_api_key` is set, as the refresh timer is. Verify `make ansible-lint` passes and the rendered units read as design.md says.

## 8. Project documents

- [x] 8.1 `docs/privacy.html` and `docs/index.html`: the field lists, the Methods line, the status panel, "at least every 25 days", and "Last updated" set to the day the change is deployed. Verify each field in the three `fields` constants appears in the page's lists and nothing on the page is unread.
- [x] 8.2 `docs/PLAN.md`: the corrections listed in design.md ("Documentation"). Verify every figure against the facts table and the quota table in design.md.
- [x] 8.3 Root `CLAUDE.md`: the YouTube exception reworded as in design.md, once the operator has agreed the wording. Verify it matches.

## 9. Integration

- [x] 9.1 Run `make validate`; fix everything it reports. Not done while it is red.
- [x] 9.2 Run `openspec validate add-youtube-snapshots --strict`; fix everything it reports.

## Workflow follow-up

- The operator deploys with the playbook (migration with its dump, units, timer). The first pass is run by hand — `scripts/on-host.sh yt watch`, then `yt watch-status` and `yt quota` — before the timer is trusted. That settles `fields`, the 404 shapes, absent counters and the first listing's cost (design.md, "Assumptions"). Any surprise is a fix first.
- After two weeks, read `yt quota` and `yt watch-status`: units a day against the 1,830–2,500 estimate, and how often the daily check found what the count missed. Revisit the reserve and the discovery choice against them.
- On acceptance of the derived-metrics application: one commit setting `DERIVED_METRICS_ACCEPTED_ON`, updating `docs/privacy.html` (36 months, the date) and recording it in `docs/PLAN.md`; then deploy.
- `/opsx:verify`, then `/opsx:archive`. Commits go to `master`.
