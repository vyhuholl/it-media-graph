# Proposal

## Why

The task: catch what took off long after it was published, on both platforms and above all on YouTube. Neither collector can see it today. A Telegram post is read until 48 hours and never again, and a YouTube video until 28 days and then every 25 days. A late jump either never reaches a reading or is averaged into one 25-day step. These counters are a time series that nothing can fetch back later, so every day without late readings is lost for good. That is why collection goes first and alone, as the poll loop did before the scoring that reads it. How the readings are judged, a "second wind" section in each platform's weekly list, is a change of its own, priced on the readings this one starts collecting.

Measured on the production database on 2026-11-16 (aggregates only):

- **Telegram posts are still moving at the horizon.** Between 24 h and 48 h, views grow by a median ×1.12, p90 ×1.28, p99 ×1.58 (8,040 posts, the last two weeks). The code's comment that a post past the horizon "has stopped moving" is not what the data says.
- **14% of reposts by other channels arrive after 49 h** (4,139 over 120 days): 9.3% at 49 h–7 d, 3.1% at 7–30 d, 1.7% later.
- **The poll window already holds old posts' counters, and drops them.** For the median seed (0.40 posts a day) the window spans about 17 days of posts. It spans at least 7 days for 73% of seeds and at least 30 days for 37%, and less than 3 days for 9%.
- **YouTube videos speed up again inside the loop's window.** In two weeks, 22 of about 2,400 videos read past day 4 at least tripled their daily views over the previous interval, at 200 a day or more. The median start was day 12, and the median gain +50% of everything gained before (p90 ×6.4). 16 of the 22 were 3 minutes or shorter. Alerts stop at 7 days, so none of these was seen.
- **Past 29 days, YouTube growth cannot be measured at all.** No video has two readings past that age. The next pairs arrive with the 25-day refresh of about 2026-12-06.
- **Quota is not the constraint.** The general pool spends 5 of its 6,000 units a day. The poll loop spends about 2,200 of its 3,000.

## What Changes

- **Telegram: late readings from the window the poll already fetches.** When a polled channel's window holds a post past the tracking horizon and younger than a late horizon (35 days), the post's counters are stored as a reading. This happens once the post has passed a late age (3, 4, 5, 7, 10, 14, 21 and 30 days) at which it has not yet been read. No request is added, and the window's size and the poll schedule do not change. A post outside the window is not read.
- **YouTube: the refresh ends with the late readings.** `itgraph yt refresh`, still daily, reads the counts of every watched seed's past upload inside the walk's depth. A video up to 120 days old is read daily and an older one weekly. The request asks for the counts alone (`part=statistics`), fifty videos a unit, from the general pool. The stored payload is not replaced, and a video YouTube no longer returns is removed as today. Shorts and broadcasts are read like any other video.
- **The YouTube settled reference keeps its meaning.** The fallback reference takes each settled video's *earliest* reading between 29 and 120 days instead of its latest. With daily readings, "latest" would drift towards 120 days and push every z down. Today no video has two readings in that window, so no reference changes on the day of the switch.
- **The refresh records itself.** Each run of `yt refresh` is a row holding its outcome and how many late readings were due and taken. A series exposes the newest complete run, and the infrastructure repository is asked for an alert on it.
- **Docs:** `docs/PLAN.md` (the measurements, the three-change order), `src/itgraph/README.md`, the module map. `docs/privacy.html` is not touched (decision record, at the end).

**Out of scope:**

- Any scoring, alert or list of a late reading: change C, `add-second-wind`, after two to four weeks of readings.
- The Telegram weekly list: change B, `add-telegram-weekly-list`, which can start at once.
- A larger Telegram window, or any other change to the requests the account makes.
- Videos older than the walk's depth (a year), and `videos.batchGetStats` (kept as the reserve should the main bucket become tight).
- Reviving Telegram's mature-median fallback from late readings. It reads `raw_messages` today, and changing that is a change to scoring.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `metric-snapshots`: posts past the tracking horizon are read at late ages from the window the poll already fetches, up to a late horizon; the poll schedule and the window stay sized to the tracking horizon.
- `youtube-data-retention`: while the walk is enabled, the refresh also reads the counts of past uploads, daily up to 120 days and weekly after; each run of the refresh is recorded.
- `youtube-api-access`: a late reading asks for a video's counts alone.
- `youtube-virality-scoring`: a settled video contributes its earliest reading inside the settled window, not its latest.
- `collection-monitoring`: a series of the newest complete refresh, and what the exporter may read of the refresh's record.

## Impact

- **Code:** `tg/watch.py` (keeping late readings from the window), `schedule.py` (the late ages; pure), `db/metrics.py` (each post's newest reading); `yt/refresh.py` (the late pass and its record), `yt/videos.py` (the counts-only shape), `yt_schedule.py` (the cadence by age; pure), `db/yt_videos.py` (the past uploads due a late reading); `scoring/yt_refresh.py` (`_settled`); `config.py`; `metrics/series.py`; `cli.py` (the refresh's report line). No new dependency.
- **Schema:** one Alembic revision: `yt_refresh_runs`, and the exporter's grant on its outcome and moments.
- **Role:** no new CronJob or schedule. The exporter's grants come with the revision.
- **Volume:** Telegram up to ~4.6 thousand rows a day (against ~38 thousand today). YouTube about 20 thousand rows and ~400 units a day now, ~450 units at the current upload rate in a year. That is about 8 million rows a year.
- **Infrastructure repository:** an alert on a complete refresh older than about 26 hours, and a panel.
- **Production:** the first late readings on the first poll and the first refresh after the pin. Nothing a reader sees changes until change C.

## Decision record

Brought in the explore session of 2026-11-16, on the production database's aggregates and on what others do. vidIQ, 1of10 and ViewStats rank against a channel's usual views and watch view velocity. Yu, Xie and Sanner (ICWSM 2015) found that videos go through several popularity phases. The operator's answers:

> 1. Раздел в недельном списке — и tg, и yt. Для tg тогда тоже нужно будет сделать недельный список. 2. Как рекомендуешь. 3. Как рекомендуешь. 4. Shorts игнорируем.

> Всё как рекомендуешь. Недельный список TG повторяет форму YouTube. Разбиение устраивает.

Confirmed as of 2026-11-16:

1. What a late jump becomes is a "second wind" section in each platform's weekly list, and so Telegram gets a weekly list in the shape of YouTube's. Both are changes of their own (C and B), and this change only collects.
2. Telegram's late readings come only from the window the poll already fetches. The account's requests do not change in number or shape.
3. YouTube reads past uploads daily up to 120 days and weekly up to the walk's depth, with `videos.list` from the general pool. Videos older than a year are out of scope.
4. Shorts (up to 180 s, as scoring defines them) are left out of what a late reading is judged for. They are still collected, because collection classifies no format and a time series that was not collected cannot be fetched later.
5. The YouTube settled reference takes the earliest reading in its window, not the latest.
6. Three changes: A (this one), B and C.

**Not covered by the requirements.** These were chosen in `design.md`, not by the operator:

- The late ages and the late horizon: 3, 4, 5, 7, 10, 14, 21 and 30 days, up to 35 (design.md, Decision 2).
- A Telegram late reading is taken at the first poll past its age, so it is the one reading taken after its moment (design.md, Decision 3).
- The YouTube late readings are a step of `yt refresh`, not a command or CronJob of their own (design.md, Decision 5).
- A late reading leaves the stored payload as it was (design.md, Decision 6).
- A YouTube late reading falls due a few hours before its full interval, so a daily job never slips to every other day (design.md, Decision 7).
- The refresh's record as a table and a series, not a late pass's own (design.md, Decision 9).

**Ignored, by the operator's decision of 2026-11-16:** `docs/privacy.html` says that YouTube statistics are kept at most 30 days, that what is not fetched again within 30 days is deleted, and that YouTube data is never in backups. Since `b5b0c15` (2026-11-11) none of this is true: nothing is removed for its age, and full dumps carry every YouTube table. This change adds more of what that page says is not kept. Asked, the operator answered:

> Несоответствие между privacy.html и реальным кодом проекта игнорируем.

This change does not touch `docs/privacy.html`.
