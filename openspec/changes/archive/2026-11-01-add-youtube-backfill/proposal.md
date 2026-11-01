## Why

The poll loop sees a channel's last 29 days: about 650 videos for 105 seeds. The mention graph (change 4) and the discovery loop that follows it (change 5) read who a channel mentions and links to, and a year of uploads holds about twelve times that. Scoring (change 7) needs a channel's mature baseline the moment the derived-metrics use case is accepted, rather than months of watching later (`docs/PLAN.md`, "The channel's own reference takes months").

The base rules shape all of it. Everything read is kept at most 30 days unless refreshed, statistics may not be scored, and — decided on 2026-11-01 — an edge parsed from a description lives no longer than the payload it came from. So history is worth having only while it is kept fresh, it carries no counters until acceptance, and nothing keeps it fresh before change 4 gives it a reader.

## What Changes

- `itgraph yt backfill` walks the uploads playlist of each seed back to a depth, 365 days from the walk by default, and stores the videos it finds in `yt_raw_videos`, as payloads only.
  - It stops on the page where the listing crosses the depth, and checks the undocumented newest-first order rather than trusting it. Named channels can be walked to the end of the playlist, which is how the first run verifies the order.
  - Videos inside the poll loop's 29-day horizon are left to the loop. A broadcast yet to start, dated before the horizon, is handed to the loop to be checked at its start.
  - Each walk stores at most 1,000 videos per channel.
  - Each page is committed before the next. An interrupted channel is walked again from the top, which re-reads nothing already stored. No page token outlives a run.
  - Spending comes from the general pool and stops while 2,000 units of it are still left for `add`, `refresh` and discovery. A stop at that floor, or on an exhausted quota day, exits quietly.
  - A lock of its own, apart from the poll loop's and from the Telegram lease.
- No statistics before acceptance. The walk asks `videos.list` for the payload without counters. Once `DERIVED_METRICS_ACCEPTED_ON` holds a date, every fetch of a past upload, by the walk or by the refresh, also takes a reading, kept up to 36 months.
- `itgraph yt refresh` keeps past uploads fresh when the walk is enabled: every seed video past the horizon and inside the depth, fifty to a call. The year rolls forward without another walk — the poll loop feeds it at the front, and it expires at the back.
- A switch, `youtube_backfill_enabled`, off by default and turned on with change 4, or at acceptance if that comes first. It controls two things: the daily CronJob that walks seeds not yet walked, and the refresh of past uploads. While it is off, a walk run by hand stores data that expires in 30 days, which is what the verification run needs.
- A walk record per channel, `yt_backfill_walks`: when the channel was walked, how far, and what the API did — counts only, no API data. It is left out of every dump, so a restore re-walks instead of claiming walks whose videos are gone. It is also forgotten when the channel stops being a seed or goes gone, and when 30 days have passed with the switch off.
- `itgraph yt backfill-status`: walked, due, failing and capped channels, the order and availability counts the first run settles, and the general pool against the floor. It makes no request.
- `backfill` joins `yt_command`, spending from the general pool, and `yt_discovery_path`. `yt watch-status` counts only the loop's own paths, so the count-against-check measurement stays clean.
- The uploads listing asks for `nextPageToken`. It does not ask for `pageInfo.totalResults`, which III.E.4.b counts as a statistic.
- Documentation:
  - `docs/index.html` and `docs/privacy.html`: past uploads, what they are read for, how long they are kept, the date;
  - `docs/PLAN.md`: measured upload rate and costs, the edge decision, the III.D.3 condition, what `videos.batchGetStats` lacks, change 3's row;
  - `src/itgraph/README.md`.

Out of scope: edges and pending mentions (change 4), resolving them (5), alerts (6), scoring, curves and the maturity window (7), comments. `videos.batchGetStats` is not used. Labelling the first listing after a restore as `count` is a poll-loop fix of its own.

## Capabilities

### New Capabilities

- `youtube-backfill`: the history walk — which channels are walked and when, how deep, where a walk stops, how it checks the listing's order, what it stores and hands to the poll loop, how it resumes, spends and stays out of the loop's way, what it records about itself, and what it reports.

### Modified Capabilities

- `youtube-data-retention`: past uploads are refreshed before acceptance while the walk is enabled, without counters until then and with a reading after. The walk's records are left out of dumps along with the API data they describe.
- `youtube-api-access`: a request may ask for what paging needs beside the declared fields. A past upload is asked for without counters until acceptance.
- `youtube-snapshots`: a broadcast handed over by the walk is recorded as found by it. The loop's status counts its own discovery paths only.

## Impact

- **Code:**
  - new `yt/backfill.py` and `db/yt_backfill.py`;
  - `yt/uploads.py` gains a paged listing;
  - `yt/videos.py` gains a counter-less request;
  - `yt/refresh.py` and `db/yt_videos.py` refresh past uploads;
  - expiry and review forget walks;
  - two commands in the `yt` sub-app;
  - four settings.
- **Schema:** two revisions — `backfill` on two enum types, alone, as the project adds enum values; then `yt_backfill_walks`. `db/backup.py` excludes its rows.
- **Quota,** from 105 seeds measured 2026-10-26 to 11-01 (U = 0.22–0.28 uploads per channel per day, not PLAN's 0.5–1.5):
  - a year's walk is about 430 units for 100 channels and 2,140 for 500, or 740 and 3,680 at twice the rate;
  - keeping it fresh costs 6–12 and 30–60 units a day.
  - The general pool spends about one unit a day today.
- **Deploy:**
  - CronJob `itgraph-yt-backfill`, daily at 11:30 Moscow, after the Pacific reset in either season; it exists only with the key and the switch;
  - `YOUTUBE_BACKFILL_ENABLED` in `itgraph-env`.
- **External:** no new method. The use case is unchanged and no allocated quota is used, so no notice under III.D.3 — the operator's decision of 2026-11-01. The site states the new reading of past uploads before any is made.
