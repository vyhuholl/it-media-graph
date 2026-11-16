# Design

## Context

See `proposal.md` (Why, and the decision record). The requirements are in `specs/`. What shapes the approach:

- **Readings.**
  - Telegram's late readings (`add-late-readings`) are taken at 3, 4, 5, 7, 10, 14, 21 and 30 days of age, up to 35, from posts the poll's window already holds.
  - YouTube's are daily up to 120 days and weekly up to a year, from the refresh at 05:00 Moscow time. The poll loop reads videos up to 28 days.
  - Readings are kept, so any past week can be computed again.
- **The norms already exist.** A channel's `Baseline.reference × factor` is its value at the reference age: Telegram's eight hours (`Baselines.for_channel`), YouTube's day old per format (`YtBaselines.for_video`).
- **The lists** (`scoring/weekly.py`, `scoring/yt_weekly.py`) compute and store a week once. The bot sends them through `bot/app.WeeklySource`, which shows entries as rank and text and marks them by rank as parts go out.
- **What was measured** (proposal.md, Why): a multiple of the norm alone lists slow-burn channels; acceleration alone is noise; within 28 days the section is nearly empty on YouTube.

## Goals / Non-Goals

**Goals:**

- The section computed the same way on both platforms, from readings alone, and re-computable for any past week.
- Nothing a reader sees until calibration, and calibration with no change of code: thresholds are settings, the switch is a setting, and every list records what it was computed with.

**Non-Goals:**

- Calibration itself, alerts, other signals, Shorts: proposal.md, Out of scope.

## Decisions

### 1. What an item is, and which items are judged

- **Telegram:**
  - a post of a seed that is not a chat, published in `[week_start − watch_late_horizon_days, week_start − 7 days)`;
  - read as the scoring pass reads posts: an album by its first part, a burst by its first message, through the same `burst_heads` and first-part rule.
- **YouTube:**
  - a long-form video (`format_of`, over 180 seconds, not a broadcast) of a channel in scope, labelled `en` or `ru` with a kind;
  - published in `[week_end − youtube_backfill_days, week_start − 7 days)`;
  - broadcasts are left out with the Shorts, since they have no format and so no norm.
- **The signal is views.**

### 2. Two paces, from three readings

Each item takes three readings, each the last at or before a moment:

```
  p: at or before week_start − 7 d     a: at or before week_start     z: at or before min(week_end, moment)
  previous pace = (a − p) / days(p, a)                  pace = (z − a) / days(a, z)
  acceleration  = pace / previous pace     (no previous growth, previous pace ≤ 0: acceleration is not a number,
                                            and the item qualifies on size alone)
  share         = 7 × pace / norm
```

An item is judged only when:

- all three readings exist;
- `z` is after the week's start;
- each interval is at least two days, so neither pace is a few hours' noise;
- `z ≥ a` — YouTube taking views away makes no second wind.

**It qualifies when** `acceleration ≥ A` (or there was no previous growth) **and** `share ≥ M`.

The intervals follow the readings rather than the week's exact bounds, because Telegram's late readings are days apart. "A week at this pace" is what the share compares, so a pace measured over nine days and one over six are judged alike.

The readings are read with three `LATERAL` probes per item on the readings' primary keys: `(channel_id, msg_id, observed_at)` and `(video_id, observed_at)`. That is about 16 thousand Telegram posts and 55 thousand YouTube videos a week, at 05:00 and 10:00 on a Monday.

### 3. The norm

The channel's value at the reference age, `reference × factor`, from the baseline run **newest when the list is computed**:

- Telegram: views at eight hours;
- YouTube: long-form views at a day old.

Not the run in force at each reading, as the list's own scores are: a norm moves slowly, and the share is a size floor, not a score. A channel without a baseline has no norm, and none of its items is judged.

### 4. Settings, provisional

| Setting | Default |
|---|---|
| `weekly_second_wind_enabled` / `youtube_weekly_second_wind_enabled` | `false` |
| `weekly_second_wind_acceleration` / `youtube_…` | `3.0` (positive) |
| `weekly_second_wind_norm_share` / `youtube_…` | `0.5` (positive) |
| `weekly_second_wind_size` / `youtube_…` | `5` (at least 1) |

Two sets, because the platforms are calibrated apart and may be switched on at different times. 3 and 0.5 are placeholders: 3 is where long-form YouTube videos of 7 to 28 days were 6 in 505, and 0.5 is half a new post's day, or eight hours on Telegram. Both are replaced by calibration.

### 5. Storage

One revision:

```
weekly_revivals     list_id → weekly_lists CASCADE, rank, channel_id → channels(tg_id), msg_id,
                    before_at, start_at, end_at,
                    (channel_id, msg_id, end_at) → message_metrics CASCADE,
                    previous_pace (NULL: no previous growth), pace, norm, others, delivered_at
                    PK (list_id, rank), UNIQUE (list_id, channel_id)
yt_weekly_revivals  list_id → yt_weekly_lists CASCADE, rank, channel_id → yt_channels, video_id,
                    before_at, start_at, end_at,
                    (video_id, start_at) and (video_id, end_at) → yt_raw_video_stats CASCADE,
                    acceleration (NULL: no previous growth), share, others, delivered_at
                    PK (list_id, rank), UNIQUE (list_id, channel_id)
weekly_lists, yt_weekly_lists  + second_wind_shown boolean NOT NULL DEFAULT false,
                    second_wind_acceleration, second_wind_norm_share, second_wind_size
```

- **Telegram's rows keep the paces and the norm**, as its entries keep the count scored.
- **YouTube's rows copy nothing YouTube said.** They keep the moments of the readings and itgraph's two multiples; the views and the gain are read from those readings at sending, as an entry's counts are.
- **A revival goes with the reading that ends its week**, and a YouTube one with its start reading too.

**Grants:**

- the bot's role: `SELECT` on both tables and `UPDATE (delivered_at)`;
- the exporter's: nothing;
- `itgraph_reader`: through its default privileges.

### 6. The shadow

The pass always computes and stores the section. It records on the list the switch's state and the thresholds, at computing. The bot sends the section only for a list with `second_wind_shown` true, so a switch turned on mid-week does not reach a list computed before it. `--preview` prints the section whatever the switch, by ids: pace, acceleration, share, others.

### 7. Computing the section

- **`scoring/weeks.py`** gains `Revival[C, P]` and the pure parts:
  - `pace_of(p, a, z)`, which gives the two paces, the acceleration and the gain, or "not judged";
  - `qualifies(…, acceleration, share)`;
  - `rank_revivals(…, size)`, one per channel by share with others counted, ties to the larger acceleration and then the item's id.
- **`db/weekly.py` and `db/yt_weekly.py`** gain the items with their three readings, and storing and claiming revivals.
- **`scoring/weekly.py` and `scoring/yt_weekly.py`** compute the section after the list, per language for YouTube, and store it in the list's transaction.
- **The summaries** add the items judged, the qualifying, and the listed.

### 8. The message

After the list's entries, under its own heading:

```
🔁 Второе дыхание — старые посты, которые разогнались за неделю
1. Канал (@channel) · опубликовано 18 дн. назад
https://t.me/channel/1234
Первая строка поста…
🔁 Просмотры шли в 4 раза быстрее прошлой недели — за неделю в 2,8 раза больше обычного нового поста канала за 8 часов (оценка itgraph)
```

YouTube's entry:

```
1. <b>Название видео</b> — Канал · опубликовано 2 мес. назад
https://www.youtube.com/watch?v=…
YouTube: 12 345 просмотров на 16.11 05:02 → 15 145 на 23.11 05:01 (+2 800)
🔁 В 8 раз быстрее прошлой недели — за неделю в 1,4 раза больше обычного нового видео канала за сутки (оценка itgraph)
```

- An item with no previous growth says «снова начал набирать просмотры» instead of the first multiple.
- With the switch on and no entry: «🔁 Второе дыхание — ничего не разогналось».
- The heading travels in the first revival entry's text, so `message_parts` keeps it whole with that entry.
- The section is numbered from 1.
- A list whose main part is empty but whose section is not sends the header, «выше нормы — ничего», then the section.

### 9. Delivery

`WeeklySource` gains the section:

- `claim` brings the list's `second_wind_shown` and its undelivered revivals;
- `show` returns the list's entries, then the revivals, keyed by section and rank;
- marking a part marks each section's ranks.

YouTube's revival reads its title, channel and the views of its two readings at sending, from what is stored. Telegram's reads its post as an entry does. A revival whose post or video is gone is left out. Everything else is the list's: parts, attempts, quiet hours, cap, digest, no preview.

### 10. Backups and the restore

- `neutralise.sql` marks undelivered revivals of both platforms delivered, and `readback.sql` fails while one remains. Removing either statement fails the suite's test.
- The restore's previews already run both passes, and now print their sections.
- `e2e.sh` stores a list with a revival from synthetic readings, and has the bot deliver it with the switch on.

### 11. Calibration (after this change, with a release of its own)

From the list of 30 November:

- the agent reads production's readings in aggregates, through the reader, computing the section's numbers in SQL at a grid of `A` and `M`;
- the operator runs `itgraph weekly --preview` and `itgraph yt weekly --preview` in the test environment, the thresholds set through the environment.

After two or three weeks, a release sets each platform's thresholds and switches it on. Nothing in this change waits for it.

### 12. Docs

- `docs/PLAN.md`: change C in "Late growth": the measurements, the model, the shadow, the calibration plan.
- `src/itgraph/README.md`: the section, its settings, what the previews print.
- `src/itgraph/CLAUDE.md`: the changed rows of `scoring/weeks.py`, `scoring/weekly.py`, `scoring/yt_weekly.py`, `db/weekly.py`, `db/yt_weekly.py`, `bot/app.py` and `bot/render.py`.
- `deploy/runbook.md`: switching a section on is a release, never a role input.
- `docs/*.html` is not touched.

## Risks / Trade-offs

- **[The section is empty for weeks]** → Measured, likely on YouTube within 28 days. The shadow weeks tell whether older videos and Telegram fill it; an empty section is said in one line once switched on.
- **[Telegram's section favours quiet channels]** → Only their posts stay in the poll's window for weeks. Accepted with change A. The calibration reads which channels it lists.
- **[Paces from sparse readings]** → Intervals of at least two days, and a share per week of pace. Telegram's intervals are 3 to 9 days at these ages.
- **[YouTube's view corrections]** → A falling count does not qualify; a correction inside the previous week can inflate an acceleration, and the size floor bounds what that lists.
- **[Provisional thresholds stored on shadow lists]** → They describe what the list was computed with, and nothing reads them but the operator and the calibration.

## Migration Plan

1. Implement; `make validate` and `make test-e2e`. Verify the revision on a `_test` database.
2. **(operator)** Cut a release, deploy it to the test environment, and restore. The previews print both sections.
3. **(operator)** Pin the release in production. The lists of 23 November onward carry shadow sections, and the first fully bracketed week is the list of 30 November.
4. Calibration and switching on: a later change of settings, through a release (Decision 11).
