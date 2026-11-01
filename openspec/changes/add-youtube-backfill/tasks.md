## 1. Settings

- [x] 1.1 Add `youtube_backfill_enabled` (`False`), `youtube_backfill_days` (365), `youtube_backfill_max_videos` (1,000; 0 lifts it) and `youtube_backfill_general_floor` (2,000) to `config.py`, with validators. The depth must lie past `youtube_watch_horizon_days`, naming both settings. The floor must be below the general cap, naming the settings involved. Verify with tests: defaults load; a depth of 29 days with a 29-day horizon is refused; a floor of 6,000 against a 6,000 cap is refused; a negative ceiling is refused.

## 2. Storage

- [x] 2.1 Add `YtCommand.BACKFILL` and `YtDiscoveryPath.BACKFILL`, and revision A adding both values, alone, with an empty downgrade carrying the docstring of `b4e1d7c93af2`. Verify `upgrade head` on a `_test` database.
- [x] 2.2 Add the `YtBackfillWalk` model and revision B creating `yt_backfill_walks` as in design.md ("The walk record"). Verify:
  - `upgrade head`, `downgrade -1` and `upgrade head` on a `_test` database only, after which autogenerate reports no difference;
  - a test that deleting a channel record deletes its walk.
- [x] 2.3 Add `yt_backfill_walks` to `EXCLUDED_DATA` in `db/backup.py`. Verify with tests on the built `pg_dump` command that a full dump, the pre-migration one included, carries the table's definition and none of its rows, and the inventory dump still carries the YouTube channel records ("Backups Carry No API Data"). Update the restore note in `src/itgraph/README.md`: after a restore, walks start again.
- [x] 2.4 Add `db/yt_backfill.py`:
  - seeds due a walk: seed, not gone, no record or one not finished;
  - starting a walk, which resets its counters;
  - recording a page's counters with its videos in one transaction;
  - finishing, capping and failing a walk;
  - forgetting walks;
  - the lock: `pg_try_advisory_lock(bigint)` on a `NullPool` connection, with a key unequal to `WATCH_LOCK_KEY`;
  - the status queries.

  Add its row to `src/itgraph/CLAUDE.md`. Verify with tests:
  - a second lock attempt fails while the first is held;
  - the watch lock and the Telegram session lease can each be taken while it is held, and vice versa;
  - a closed connection releases it;
  - "Walked Channels": seeds of every language due; non-seeds and gone not due; a finished walk not due.
- [x] 2.5 Add to `db/yt_videos.py` an insert for the walk's videos that never overwrites a stored one (`ON CONFLICT DO NOTHING`), with `discovered_via = backfill`, and with a reading only when one is given. Verify with tests: a video the poll loop tracks keeps its payload, `due_at` and path when the walk inserts the same id ("A stored video is untouched"); a reading goes in with its video in one transaction.
- [x] 2.6 Forget walks:
  - in the transaction that moves a channel off `seed`, through `_write` in `db/yt_channels.py`, which both `mark` and `review-apply` use;
  - in `mark_gone`;
  - while `youtube_backfill_enabled` is off, those finished more than 30 days ago, in `_expire_youtube_data`.

  Verify with tests for every scenario of "Walks Are Forgotten With Their Videos", through `mark`, through `review-apply`, and through a refresh that marks a channel gone; and that `yt … --help` still opens no database.

## 3. Requests

- [x] 3.1 Add a paged listing to `yt/uploads.py`: `maxResults=50`, `pageToken` passed when given, `fields` = `nextPageToken,items/contentDetails(videoId,videoPublishedAt)`, returning the uploads and the next token. 404 and non-quota 403 are the channel's failure, as in `newest_uploads`. Verify with `MockTransport` tests: the request names exactly those fields and no `pageInfo`; the token is passed back; a page with no token ends the listing; a 404 is a channel failure; a 400 stops.
- [x] 3.2 Give `yt/videos.by_ids` a choice of parts and fields, and add the pair for past uploads: the loop's parts and fields without `statistics`. Verify with tests:
  - a past-upload request names no `statistics` part and no counter field;
  - the loop's request is unchanged;
  - every field of both pairs appears in `docs/privacy.html`'s list ("Requests Read Only Declared Fields").
- [x] 3.3 Map `YtCommand.BACKFILL` to the general pool in `POOL_OF`, and add the floor check: a walk refuses a request that would leave the general pool fewer units for the day than the floor. Verify with tests for "Spending Leaves The Other Commands Their Share": 2,001 left with a floor of 2,000 allows one request and not a second; the journal attributes the requests to `backfill`; `yt quota` lists them under the general pool.
- [x] 3.4 Extend `tests/ytfakes.py` with builders for paged uploads listings: tokens; undated items; an item out of order; a deleted id the listing names and `videos.list` does not return; a broadcast yet to start dated before the horizon. All ids are invented eleven-character values spelt as fakes. Verify `prek run gitleaks --all-files` passes with no exclusion.

## 4. The walk

- [x] 4.1 Add `yt/backfill.py`. For one channel:
  - pages from the newest;
  - items dated inside the horizon, or already stored, skipped;
  - items dated inside the depth, or undated, asked about fifty to a request;
  - videos published inside the depth and past the horizon stored with `due_at` NULL;
  - the page that holds an item older than the depth is the last;
  - a whole walk goes on to the end;
  - each page committed with the walk record's counters.

  Add its row to `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Depth", "Where A Walk Stops", "What The Poll Loop Owns Is Left To It" and "Found By The Walk Is Recorded".
- [x] 4.2 Count, per walk:
  - an item dated later than the earliest already listed, as out of order;
  - an undated item, asked about and stored if YouTube dates it inside the depth;
  - a listed id YouTube did not return, as not returned.

  Verify with tests for "The Listing's Order Is Checked, Not Trusted", and that the record holds counts only and no date YouTube returned ("What A Walk Records About Itself").
- [x] 4.3 The ceiling: stop at `youtube_backfill_max_videos` stored videos and record the walk as capped; zero lifts it. Verify with tests for both scenarios of "A Ceiling Per Channel".
- [x] 4.4 A broadcast yet to start: store it with `due_at` from `yt_schedule.next_check_of_upcoming`, take no reading, and leave it to the poll loop. Verify with a test that hands one over and then runs a `watch_pass`: the pass checks it at its scheduled start, reads it once live, and gives it up seven days after a start that never came. The video keeps its `backfill` path throughout.
- [x] 4.5 Counters from acceptance: with `DERIVED_METRICS_ACCEPTED_ON` unset, the walk asks for and stores no counts. With it patched, it asks with the loop's fields and stores a reading from the same answer. Verify with tests for both scenarios of "No Counters Before Acceptance", including that the stored payload has no `statistics` key before acceptance.
- [x] 4.6 The run, over the seeds due or over the named channels:
  - one channel at a time;
  - a channel's 404 or 403 recorded and the run continued;
  - `--limit` counting requests;
  - the floor, a spent pool and an exhausted day stopping it with what it stored kept;
  - an interrupted channel walked again from its newest page next time, re-asking no stored video;
  - no page token kept between runs.

  Verify with tests for:
  - every scenario of "Progress Is Kept Per Page", "Spending Leaves The Other Commands Their Share", "A Stop For Quota Is Not A Failure" and "One Channel's Failure Stays With That Channel";
  - that expiry ran in each.
- [x] 4.7 Verify with a log capture at debug level that a walk which stores videos and hands a broadcast over writes no title, description, date or count ("Logs Name Ids Only"). Verify that it writes nothing but video data, readings where acceptance allows them, and walk records ("The Walk Derives Nothing").

## 5. Commands

- [x] 5.1 Add `itgraph yt backfill [CHANNEL…] [--whole] [--limit N]` to the `yt` sub-app:
  - channels by id or handle, as for `mark`;
  - `--whole` refused without named channels, before the database;
  - a named channel that is not a seed refused;
  - a named seed walked again;
  - quota stops and a held lock exit 0 with one sentence; an API failure exits 1;
  - one summary line.

  Verify with CLI tests for each exit, for "One Walk At A Time", and that `--help` opens no database.
- [x] 5.2 Add `itgraph yt backfill-status` as in "Status Report", reading the walk records, `yt_raw_videos` by path and the ledger. Verify with an output test and that no HTTP client is built.
- [x] 5.3 Count only the poll loop's paths in `yt watch-status`. `discoveries_since` leaves `backfill` out, and the "found in 7 days" line names the loop's three paths. Verify with tests for both scenarios of youtube-snapshots' modified "Status Report", and that a broadcast handed over by the walk is counted among tracked videos and recorded as found by the walk ("Discovery Is Recorded").
- [x] 5.4 Document both commands in `src/itgraph/README.md` (Russian), a section after «Цикл опроса: `yt watch`»:
  - what a walk does and stores, and what it leaves to the poll loop;
  - no counters until acceptance;
  - the switch and when to turn it on (with change 4, or at acceptance if that comes first);
  - the first run;
  - quiet exits;
  - `backfill-status`;
  - a restore walks again.

  Verify the README against both commands' `--help`.

## 6. Past uploads kept fresh

- [x] 6.1 Extend `db/yt_videos.py` and `yt/refresh.py`. While `youtube_backfill_enabled` is on, the refresh selects every seed video meeting all of these:
  - `due_at` NULL;
  - not a broadcast yet to start;
  - published inside the depth;
  - payload older than the refresh age.

  It fetches them fifty to a request from the general pool, with the past-upload fields before acceptance and the loop's after, taking a reading when accepted. It removes those YouTube no longer returns. The accepted-readings selection keeps its rule and now reaches past the depth. Verify with tests for:
  - every scenario of "Past Uploads Are Kept Fresh While The Walk Is Enabled";
  - the three scenarios of the modified "Videos Are Refreshed While Their Readings Are Kept", with the switch and the acceptance constant patched both ways.
- [x] 6.2 Document the refresh of past uploads in the README's refresh section and the new backfill section: what is kept, for how long, what it costs. Verify the figures match design.md.

## 7. Deploy

- [x] 7.1 Add `youtube_backfill_enabled: false` and `itgraph_yt_backfill_schedule: "30 11 * * *"` to `deploy/group_vars/all/main.yml`, and `YOUTUBE_BACKFILL_ENABLED` to `secret-env.yml.j2`. Verify `make ansible-lint` passes and the rendered Secret carries the value.
- [x] 7.2 Add the `itgraph-yt-backfill` CronJob to `cronjobs.yml.j2`, as in design.md ("The schedule"):
  - present only with the key and the switch;
  - `args: ["yt", "backfill"]`;
  - the YouTube jobs' security context and resources.

  Extend the playbook's delete task to remove it when the key or the switch is absent. Add its row to the design table in `tests/test_deploy_manifests.py`. Verify with tests: no CronJob without the key; none with the key and the switch off; present with both; the schedule and deadlines match the table; `make ansible-lint` passes.
- [x] 7.3 Extend `deploy/README.md`'s note on explicit deletion («так удаляются YouTube-CronJob'ы без ключа») to the backfill CronJob without the switch, and name `youtube_backfill_enabled` there. Verify the variable name matches `main.yml`.

## 8. Project documents

- [x] 8.1 `docs/index.html` and `docs/privacy.html`: the wording proposed in design.md ("Documentation"), as the operator settles it, and "Last updated" set to the day the change is deployed. Verify every field the walk requests appears in the page's lists, and that the page says what past uploads are read for and how long they are kept.
- [x] 8.2 `docs/PLAN.md`: the corrections listed in design.md ("Documentation"). Verify every figure against the measured table and the quota table in design.md.

## 9. Integration

- [x] 9.1 Run `make validate`; fix everything it reports. Not done while it is red.
- [x] 9.2 Run `openspec validate add-youtube-backfill --strict`; fix everything it reports.

## Workflow follow-up

- The operator deploys with the playbook, switch off: migration with its dump, no CronJob.
- First real run, by the operator:
  - `scripts/on-host.sh yt backfill <five seeds with videoCount ≤ 300> --whole`, then `yt backfill-status` and `yt quota`. That settles paging, order past the first page, undated and not-returned items, and the cost per page (design.md, "Assumptions");
  - a surprise is a fix first;
  - the data expires in 30 days.
- With change 4: `youtube_backfill_enabled: true` in `deploy/group_vars/all/main.yml` and a deploy. The next morning the CronJob walks every seed, about 430 units. Check `yt backfill-status` the day after. Turn the switch on at acceptance instead, if that comes first.
- On acceptance of the derived-metrics application: the commit setting `DERIVED_METRICS_ACCEPTED_ON` also updates `docs/index.html`'s "Past uploads" item, which says their counts are not read.
- A separate fix: the poll loop records a channel's first listing after a restore as `count` (`docs/PLAN.md`).
- `/opsx:verify`, then `/opsx:archive`. Commits go to `master`.
