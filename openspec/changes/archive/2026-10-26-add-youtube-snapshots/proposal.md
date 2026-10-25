## Why

Alerts on YouTube score a video against what that channel's videos normally reach at the same age, and that reference accrues only in wall-clock time — at one to three uploads a week, months of it (`docs/PLAN.md`, "The channel's own reference takes months"). So the poll loop goes second, before anything that reads its output: every week it starts later is a week of reference that cannot be bought back.

The expensive half turned out not to be the one `docs/PLAN.md` priced. Snapshots are cheap — `videos.list` takes fifty videos of any channels for one unit. Discovery is not: `playlistItems.list` is one unit per channel per call with no batch form, so the obvious design, every channel's uploads every fifteen minutes, is 48,000 units a day for 500 channels against a 10,000-unit quota. The 3,000-unit reserve change 1 set aside priced the snapshots and not the discovery, and this change is where it is either justified or revised.

Until the derived-metrics application is accepted, the base rules hold: statistics read with the key are kept at most 30 days and nothing is scored. The loop collects under those rules, and is shaped so that acceptance changes a retention period rather than a schema.

## What Changes

- `itgraph yt watch` — one pass of the poll loop, run every fifteen minutes by a systemd timer: expiry, then discovery, then due snapshots.
  - **Discovery** reads every seed channel's video count through `channels.list`, fifty channels a unit, and lists a channel's uploads with `playlistItems.list` only when that count has changed — plus once a day for every channel regardless, as a check on the count. A video is new if it is unknown and was published inside the tracking horizon.
  - **Snapshots** read the views, likes and comments of due videos through `videos.list`, fifty a unit, on a schedule from 1 hour to 28 days after publication (or after a stream's actual start), plus one at discovery. A missed sample is skipped, never replayed.
  - Every format is collected; none is classified. Upcoming streams and premieres are not sampled until they start.
  - Spending is paced across the quota day; under pressure the daily check and the oldest videos' snapshots are deferred first. A spent pool ends a pass quietly rather than as a failure.
  - One pass at a time, by an advisory lock of its own — never the Telegram session lease.
- `itgraph yt watch-status` — what the loop is tracking, what is due, what it skipped, and how often the daily check found a video the count missed.
- The `watch` quota pool and the `watch` value of `yt_command`: the loop spends only its own reserve, and nothing borrows across pools.
- `yt_raw_videos` and `yt_raw_video_stats`: a video's payload with its schedule, and its readings. Both are API data under the 30-day rule, both stay out of backups, and a video YouTube stops returning is deleted with its readings. Nothing about a video lives outside them.
- Requests ask for exactly the fields `docs/privacy.html` lists, through the documented `fields` parameter. That includes `channels.list`, which since change 1 has also read branding, status and topic data that the page does not mention and no code uses; the stored payloads are trimmed to match.
- A dormant switch for acceptance, as a constant in `yt_policy.py`: once it holds a date, readings taken from that date are kept up to 36 months and their videos' payloads are refreshed beyond the horizon. Titles and descriptions stay at 30 days in any case.
- `schedule.py`: `next_sample_at` takes the horizon as a parameter. Telegram's behaviour is unchanged.
- Documentation: `docs/index.html` and `docs/privacy.html` (methods, fields, refresh wording, date); `docs/PLAN.md` (the discovery cost, the reserve, why RSS and undocumented playlists are out, the new `videos.batchGetStats` method, change 2's row); `src/itgraph/README.md`; the root `CLAUDE.md` statement of the YouTube raw-layer exception, since readings are appended rather than overwritten.

Out of scope: a year of history (change 3), the mention graph (4), discovery of new channels (5), alerts (6), scoring and any classification of videos (7), comments.

## Capabilities

### New Capabilities

- `youtube-snapshots`: the YouTube poll loop — which channels are watched, how new uploads are found, when a video is read again, what a reading records, how a pass spends and paces its pool, how passes are kept from overlapping, and what the loop reports.

### Modified Capabilities

- `youtube-api-access`: the poll loop's own pool comes into existence, with no borrowing either way; every request names the parts and fields it reads, and they are the ones the privacy policy lists.
- `youtube-data-retention`: video payloads and readings join channel payloads as API data with one home each; expiry, removal of what YouTube no longer returns, and backups cover them; channels the poll loop no longer receives are marked gone as a refresh would; statistics retention follows the derived-metrics decision recorded in code.

## Impact

- New code: `yt/uploads.py`, `yt/videos.py`, `yt/watch.py`, a pure `yt_schedule.py`, a pure `derive/yt_videos.py`, `db/yt_videos.py`, `db/yt_watch.py`; two commands in the `yt` sub-app; the ledger's second pool; `fields` on `channels.list`.
- Schema: two Alembic revisions — `watch` on two enum types, as the project adds enum values; then `yt_raw_videos`, `yt_raw_video_stats`, `yt_channel_polls`, `yt_watch_passes`, two new enum types, and a one-way trim of stored channel payloads to the declared fields.
- Quota: estimated 440–540 units a day for 100 channels and 1,830–2,500 for 500, inside the 3,000 reserve, which stays. To be measured in the first two weeks.
- Deploy: `itgraph-yt-watch.service` and `.timer`, enabled only when the API key is set, like the refresh.
- External: no new API method is used. `channels.list` gains a purpose ("to notice new uploads"), which the homepage states. The use case is unchanged, so no notice to YouTube is owed under III.D.3 — the operator's decision.
