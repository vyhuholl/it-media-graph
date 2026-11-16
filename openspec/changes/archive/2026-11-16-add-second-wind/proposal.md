# Proposal

## Why

Change C of "Late growth" (`docs/PLAN.md`): the weekly lists of both platforms get a "second wind" section. It names older posts and videos whose views sped up again this week, which no alert and no main list can say. Changes A and B collect the readings and send the lists. This one judges the readings.

The thresholds cannot be priced yet. Late readings began on 2026-11-16, so a week of them bracketed on both sides first exists for the list of 30 November. So the section is built now and kept **in the shadow**: computed, stored and shown by `--preview`, but not sent until it is calibrated on two or three weeks and switched on by a release.

Measured on 2026-11-16 on the production database, as aggregates, on the only late growth that could be measured: YouTube videos 7 to 28 days old, read by the poll loop, over the weeks 2–9 and 9–16 November.

- **A multiple of the channel's norm alone lists slow-burn channels.** The weekly gain of a long-form video, divided by its channel's views at a day old, reached 1× for 15 of 189 videos. All 15 came from 4 channels whose day-old median is about 46 views. Their old videos gain steadily from search every week, and would fill the section every week.
- **Acceleration separates a second wind from that.** A video's views a day this week, divided by the same video's last week, have a median of 0.48 for long-form videos (p90 1.03): ordinary decay halves the pace. Long-form videos that sped up 3× or more: 6 of 505. For short-form: 14 of 478.
- **Acceleration alone is noise.** The six long-form videos that tripled their pace gained a median of 30 views, 6% of what they had; one gained more than 1,000. So acceleration needs a floor of size: a share of the channel's norm.
- **Within 28 days the section would be nearly empty.** Whether videos of 29 days to a year and Telegram posts of 3 to 35 days fill it is what the shadow weeks measure. On 2026-11-16 no video had two readings past 29 days, and 17 Telegram posts had two late readings.

## What Changes

- **A "second wind" section in each weekly list**: Telegram's one list, and each YouTube language's. It covers posts and long-form videos published before the previous week (at least seven days old when the week begins):
  - Telegram's up to its late horizon (35 days), and YouTube's up to the walk's depth (a year);
  - by views alone.
- **What qualifies:** both conditions.
  - **Pace:** an item's views a day this week, from its readings around the week's bounds, must be at least `A` times its pace the week before (any pace counts if it did not grow the week before).
  - **Size:** a week at that pace must be at least `M` times its channel's norm: the views a new post of the channel reaches at the reference age (8 hours on Telegram, a day on YouTube).
- **The list:** one entry per channel (its best item and how many more qualified), ranked by the pace's share of the norm, up to five entries.
- **In the shadow until switched on.**
  - The section is computed and stored with every list, and printed by `--preview`, whatever its switch.
  - The bot sends it only with a list computed while the platform's switch was on.
  - Both switches start off, with provisional thresholds `A = 3`, `M = 0.5`.
- **An entry** is like its list's:
  - **Telegram's** shows the channel, age, link and first line, with how many times faster than the week before and the multiple of the channel's norm. No counts, as the main list has none.
  - **YouTube's** shows the title, channel, age and link, the views gained between two dated readings, and the same two multiples labelled as itgraph's.
- **Docs:** `docs/PLAN.md` (the measurements, the shadow, the calibration plan), `src/itgraph/README.md`, the module map, the runbook. `docs/*.html` is not touched.

**Out of scope:**

- Short-form videos, broadcasts, and every signal but views.
- Calibrating the thresholds and switching the section on. That needs two or three weeks of late readings, from 30 November, and is a release of its own.
- Alerts on a second wind.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `telegram-weekly-list`: the second wind section, what qualifies, its entries, its shadow.
- `youtube-weekly-list`: the same for each language's list, long-form videos only, with YouTube's counts dated.
- `alert-delivery`: the bot's writes cover the second wind entries' delivery state.
- `database-backups`: a restore closes undelivered second wind entries too.
- `collection-monitoring`: the exporter reads nothing of a second wind entry.

## Impact

- **Code:**
  - `scoring/weeks.py` (the pace, the acceleration and the ranking of a section; pure);
  - `scoring/weekly.py`, `scoring/yt_weekly.py` (computing the section);
  - `db/weekly.py`, `db/yt_weekly.py` (the readings around a week's bounds, storing and claiming entries);
  - `bot/app.py` and `bot/render.py` (the section in a list's parts);
  - `config.py`.
  - No new dependency.
- **Schema:** one Alembic revision: `weekly_revivals`, `yt_weekly_revivals`, each list's `second_wind_shown` and its thresholds, the bot's grants on the revivals' delivery state.
- **Role:** `neutralise.sql` and `readback.sql`, `e2e.sh`. No new CronJob or schedule.
- **Production:** nothing a reader sees until a release switches a platform's section on.

## Decision record

Brought in the explore session of 2026-11-16 as eight questions, on the production database's aggregates. The operator's answer:

> Всё как рекомендуешь

Confirmed as of 2026-11-16:

1. **The model:** acceleration of the item's own pace at least `A`, and a week at that pace at least `M` times the channel's norm. The norm is the channel's own reference, or its mature median times its kind's factor. Ranked by the pace's share of the norm.
2. **Views only.**
3. **Age:** published before the previous week, up to Telegram's late horizon and YouTube's walk depth.
4. **Up to five entries per list.**
5. **A channel may be in both sections**, never the same item.
6. **The shadow:** computed and stored with the list and printed by the preview; sent only once the platform's switch is on, calibrated and switched by a release.
7. **A video whose views fell** (YouTube removing views) does not qualify.
8. **An entry** is its list's kind: Telegram's without counts, YouTube's with the gain and the moments of its readings.

From earlier decisions: Shorts are left out (2026-11-16, change A's decision record). `docs/*.html` is not touched.

**Not covered by the requirements.** These were chosen in `design.md`, not by the operator:

- The provisional thresholds `A = 3` and `M = 0.5`, until calibration (design.md, Decision 4).
- Each pace measured between readings at least two days apart, and a pace's week as seven days at it (design.md, Decision 2).
- The norm from the baseline run newest when the list is computed, not the run in force at each reading (design.md, Decision 3).
- Broadcasts left out on YouTube with the Shorts (design.md, Decision 1).
- A section with nothing in it said in one line (design.md, Decision 8).
- The switch recorded on the list when it is computed, not read when it is sent (design.md, Decision 6).
- The table and setting names (design.md, Decisions 5 and 4).
