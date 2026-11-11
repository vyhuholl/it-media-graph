## Why

The poll loop sees a channel's last 29 days: about 650 videos for 105 seeds. The mention graph (change 4) and the discovery loop that follows it (change 5) read who a channel mentions and links to, and a year of uploads holds about twelve times that.

## What Changes

- `itgraph yt backfill` walks the uploads playlist of each seed back to a depth, 365 days from the walk by default, and stores the videos it finds in `yt_raw_videos`, as payloads only.
  - It stops on the page where the listing crosses the depth, and checks the undocumented newest-first order rather than trusting it. Named channels can be walked to the end of the playlist, which is how the first run verifies the order.
  - Videos inside the poll loop's 29-day horizon are left to the loop. A broadcast yet to start, dated before the horizon, is handed to the loop to be checked at its start.
  - Each walk stores at most 1,000 videos per channel.
  - Each page is committed before the next. An interrupted channel is walked again from the top, which re-reads nothing already stored. No page token outlives a run.
  - Spending comes from the general pool and stops while 2,000 units of it are still left for `add`, `refresh` and discovery. A stop at that floor, or on an exhausted quota day, exits quietly.
  - A lock of its own, apart from the poll loop's and from the Telegram lease.
- The walk asks `videos.list` for the payload without counters.
- `itgraph yt refresh` keeps past uploads fresh when the walk is enabled: every seed video past the horizon and inside the depth, fifty to a call.
- It controls two things: the daily CronJob that walks seeds not yet walked, and the refresh of past uploads.
- `itgraph yt backfill-status`: walked, due, failing and capped channels, the order and availability counts the first run settles, and the general pool against the floor. It makes no request.
- `backfill` joins `yt_command`, spending from the general pool, and `yt_discovery_path`. `yt watch-status` counts only the loop's own paths, so the count-against-check measurement stays clean.
- The uploads listing asks for `nextPageToken`.
- Documentation:
  - `docs/index.html` and `docs/privacy.html`: past uploads, what they are read for, how long they are kept, the date;
  - `src/itgraph/README.md`.

Out of scope: edges and pending mentions (change 4), resolving them (5), alerts (6), scoring, curves and the maturity window (7), comments. `videos.batchGetStats` is not used. Labelling the first listing after a restore as `count` is a poll-loop fix of its own.

## Capabilities

### New Capabilities

- `youtube-backfill`: the history walk — which channels are walked and when, how deep, where a walk stops, how it checks the listing's order, what it stores and hands to the poll loop, how it resumes, spends and stays out of the loop's way, what it records about itself, and what it reports.

### Modified Capabilities

- `youtube-api-access`: a request may ask for what paging needs beside the declared fields.
- `youtube-snapshots`: a broadcast handed over by the walk is recorded as found by it. The loop's status counts its own discovery paths only.

## Impact

- **Code:**
  - new `yt/backfill.py` and `db/yt_backfill.py`;
  - `yt/uploads.py` gains a paged listing;
  - `yt/videos.py` gains a counter-less request;
  - `yt/refresh.py` and `db/yt_videos.py` refresh past uploads;
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
- **External:** no new method. The site states the new reading of past uploads before any is made.
