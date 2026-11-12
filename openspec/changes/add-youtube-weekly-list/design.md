# Design

## Context

See `proposal.md`, Why, and its decision record. The requirements are in `specs/`. What shapes the approach, read on 2026-11-12 — aggregates only:

- **Who can be on a list.** Channels in scope with a baseline in the newest run: 154 of 500 English seeds, 67 of 169 Russian. The rest are not scored, and so are on no list, until they have a reference (change 7, Decision 5).
- **How many are above a floor in a week.** The replay of 5–11 November 2026 on a restored copy in the test environment, with `--late-settled`, at four thresholds — events are a channel's crossings folded within 24 hours; channels ≈ events less a channel's repeats more than 24 hours apart, which the replay does not split by language, so for Russian the events are an upper bound on channels:

  | z at least | English events | Russian events | channels, both ≈ | faded, of those decided |
  |---|---|---|---|---|
  | 3.0 (the alerts') | 27 | 4 | 22 | 3% |
  | 2.5 | 49 | 6 | 37 | 6% |
  | 2.0 | 81 | 11 | 59 | 7% |
  | 1.5 | 129 | 20 | 86 | 10% |

  English fills ten at the alerts' threshold; Russian is the constraint, three or four channels a week at z 3.0. "Faded" is the replay's: the newest reading at least three days old under twice its expectation.
- **Baseline runs are kept.** `yt_baseline_runs` is never pruned — only `e2e.sh` deletes one — so the run that was newest at any past moment can be read. Production's first completed run is of 2026-11-12, 06:09 Moscow time; the first week every reading of which has a run before it is 16–22 November.
- **What the site promises** (`docs/index.html`, `docs/privacy.html`, `docs/terms.html`): the score is labelled as itgraph's wherever it appears and always shown next to the counts YouTube reports; a YouTube alert holds the title, the channel, a link, the counts with the time they were read, and the score with the time it was calculated. The site names alerts only.
- **The bot** (`bot/app.py`): two queues behind one claim → send → mark loop, a destination per topic with its cap, quiet window and digest; `digest_parts` splits a summary into messages of at most 4,096 characters with each entry whole; a summary of nothing is not sent. Its database role writes the delivery state of the two alert queues and nothing else.

## Goals / Non-Goals

**Goals:**

- Once a week, per language, the channels whose videos of that week did best against their own, in the topic the alerts go to.
- The same judgement as the alerts': the same scores, the same signals, the same exclusions.
- Nothing YouTube said stored with the list, and every promise of the site kept by every entry.

**Non-Goals:**

- Telegram, calibration, what became of a video after the list, `/status` — proposal.md, Out of scope.

## Decisions

### 1. The week and the moment

A week is `[Monday 00:00, next Monday 00:00)` in `youtube_alert_timezone` (Europe/Moscow), and a video belongs to the week of its publication. A run computes **the last week that has ended** before its moment, so a run late by days still lists the week it was meant to; `--week YYYY-MM-DD` names another, a Monday of a week that has ended, and anything else is refused.

The readings counted are those **up to the moment of the run**: at 10:00 on Monday a video of Sunday evening has some hours of readings, and one of the Monday before has seven days. Scores account for age, so a young video is not ranked down for being young; its spread is wider, so it needs more to cross. A run late by a day counts a day more of readings, and its message says when it was computed. The other reading — readings up to the week's end only — would make a video of Sunday 23:00 unlistable, and was not chosen.

Each video is in one list at most, the list of its publication week.

### 2. Scored as the live pass saw it

Each reading is scored against **the baseline run that was newest at the moment of the reading** — `completed_at` before `observed_at` — which is what the live pass, running within fifteen minutes of each reading, scored it against. A reading with no run before it is not scored, and is counted. Not one run for the week: today's run holds the week's own videos in its channels' references and its curves, so a hit would raise its own expectation. Not a refit per day, as the replay does: the stored runs are the live judgement, and a refit is a second one to keep in agreement with it.

The code is the scoring pass's: `yt_run._prepare` cuts the week's readings by the moments between consecutive runs, `_best` scores each cut against its run with the alerts' signals and comment minimum, and a video's score is its best across the cuts. `db/yt_baselines.py` gains loading a run by id and listing the runs completed within a period, the newest before it included. Neither `_prepare` nor `_best` changes. A list's score agrees with an alert's on the same reading; it may be higher, from a later reading.

### 3. Who is listed

- **A video** is above the floor when its best score is at least `youtube_weekly_list_z` (2.0). Broadcasts and videos without a readable length are not scored, as live.
- **A channel** is listed by its best video above the floor; its other videos above the floor are counted, and the entry says how many. Shorts and long-form compete; the entry marks a Short.
- **Ranked** by that best score, highest first; a tie goes to the earlier crossing, then the video id. The first `youtube_weekly_list_size` (10) are the list. Fewer above the floor make a shorter list, never one padded from below it.

### 4. Language

A channel's language is its label **when the list is computed**: the list is "this week's Russian list", and a list does not move between topics once computed. An alert routes at sending instead, because it can wait for hours in a digest; a list is sent seconds after it is computed. A channel labelled `other`, or out of scope, by then is on neither list.

### 5. Storage

One revision:

```
yt_weekly_lists    id, week_start date, route yt_alert_route, computed_at,
                   threshold, size, comments_min, videos_scored, channels_above,
                   delivered_at, attempts, last_error
                   UNIQUE (week_start, route)
yt_weekly_entries  list_id → lists CASCADE, rank, channel_id → yt_channels,
                   video_id, scored_at → yt_raw_video_stats (video_id, observed_at) CASCADE,
                   metric yt_metric, value, expected, others, delivered_at
                   PK (list_id, rank), UNIQUE (list_id, channel_id)
```

A list holds ids, moments and itgraph's own numbers: the score, the expectation it was scored against, the count of a channel's other videos. **Nothing YouTube said** — title, channel name and counts are read at sending from the stored payloads and the newest reading, as an alert's are, and the format is worked out from the payload then, never stored per video. **An entry lives as long as its reading**, as an alert does, so a video YouTube stops returning takes its entries with it; the list row, holding no video, stays. Computing a week that already has a list writes nothing and says so. `itgraph_reader` reads both tables through its default privileges.

### 6. The message

One per language and week, in Russian as the alerts are, every moment in Moscow time. Each entry is three lines, and whole in one part:

```
📋 YouTube · RU — выше нормы за неделю 16–22 ноября
Лучшее видео каждого канала относительно его обычного уровня; оценено 214 видео.
Оценка itgraph на 23.11 10:00 — рассчитана itgraph, не метрика YouTube.

1. <b>Название видео</b> — Канал · Shorts · опубликовано 6 дн. назад
https://www.youtube.com/watch?v=…
YouTube на 23.11 09:47: просмотры 12 345 · лайки 678 · комментарии 90
📈 Оценка itgraph: просмотры — в 4 раза выше обычного для канала (z 4.2); ещё 2 видео канала выше нормы
```

What the site promises an alert holds, every entry holds: YouTube named and the video linked in the entry itself; the counts of the newest stored reading with its time, a counter YouTube did not report left out; the score labelled as itgraph's. The signal's wording is the alert's — a comment entry states its count and the usual count. "One line per channel" is read as one entry per channel; an entry with counts and a link does not fit one line of a message.

### 7. An empty week

A week with nothing above the floor is a list with no entries, sent as its header and one line: «выше нормы — ничего». The digest's rule is that a summary of nothing is not sent, because a daily message saying nothing trains the reader to ignore the topic. A weekly list is expected on Monday, and its absence reads as a failure; once a week does not train anyone. The header's count of videos scored tells "nothing took off" from "nothing was scored" — on a stale copy in the test environment, every Monday says «оценено 0 видео».

### 8. Delivery

The bot, on each pass and for each YouTube destination, takes the undelivered lists of its route, oldest week first:

- **in parts** through `digest_parts`, its headers made arguments — the list's header on the first part, «Продолжение» on the rest, each entry whole;
- **claim → send → mark** per part: a part sent marks its entries delivered, one that fails counts an attempt on the list with its error and is sent again on the next pass, and the parts already sent are not; the list is delivered once every entry is, or, empty, once its one message is;
- **outside quiet hours** — a list computed inside them, by a run catching up, waits for them to end; **not counted against the daily cap** and never held for the digest: the cap counts alert messages, and a list is not one;
- **without a link preview**, which would show one video's card above ten;
- **nowhere else** when a topic refuses, as an alert; with no YouTube chat configured, a list waits undelivered.

A list's entry whose reading was removed before sending is gone with it; the list is sent without it, and the ranks close up. The bot's role gains `SELECT` on both tables and `UPDATE` on their delivery columns alone (`delivered_at`, `attempts`, `last_error`); it cannot write an entry, a rank or a score. `Sender.send` gains the preview flag; `bot/handlers.py` maps it to aiogram's link preview options, checked against aiogram's documentation when implemented.

### 9. Series

| Series | Type | From |
|---|---|---|
| `itgraph_youtube_weekly_list_computed_timestamp_seconds{route}` | gauge | the newest list of that route, 0 when none |
| `itgraph_youtube_weekly_list_delivered_timestamp_seconds{route}` | gauge | when the newest delivered list of that route went out, 0 when none |

From the list rows alone; the exporter's role gains `SELECT` on `route`, `computed_at` and `delivered_at` of `yt_weekly_lists`, nothing else. A failed pass is already the shared monitoring's, as a failed CronJob. The infrastructure repository is asked for an alert per route on a newest delivered list older than seven days and six hours, once one exists, and a panel.

### 10. Settings, command, schedule

- `config.py`: `youtube_weekly_list_z` (2.0, positive) and `youtube_weekly_list_size` (10, at least 1). The signals and the comment minimum are the alerts': `youtube_alert_spike_metrics`, `youtube_alert_spike_comments_min`. Not role inputs: changing them is a release, as the scoring's other settings are.
- `itgraph yt weekly [--week YYYY-MM-DD] [--preview] [--now]`: computes and stores the lists of a week; `--preview` prints them — rank, score, signal, format, video and channel ids, others — and writes nothing. The summary per route: entries, channels and videos above the floor; over the week: videos published, scored, broadcasts and unreadable lengths not scored, readings with no run before them, the runs used. Needs no YouTube key, and says so. Logs ids only.
- **CronJob `itgraph-yt-weekly`**: `itgraph_yt_weekly_schedule` `0 10 * * 1`, Moscow time; starting deadline 518,400 s — a host down over Monday sends that week's list when it is back within six days; active deadline 1,800 s; in `__itgraph_cronjobs_always`, labelled for the database alone, the config and the database's URL and no other secret. It needs no key, so it runs in the test environment too, into that environment's own chat.

### 11. Backups and the restore

Full dumps carry both tables. `neutralise.sql` marks every undelivered list and entry delivered, and `readback.sql` fails while one remains — each statement's removal failing the read-back, as the suite requires. The restore's trial runs `itgraph yt weekly --preview` on the copy and reports it; it writes nothing, so the scheduled pass on the copy is not pre-empted. Delivery of a list is tried by the suite with a fake sender and by `e2e.sh` on rows.

### 12. The site

The site describes alerts and nothing else that is sent; a weekly list is a new kind of message about the same data, to the same chat. The operator's edit, outside this change's code and before production — a suggested wording for `docs/index.html` after "Alerts.", and its counterpart in `docs/privacy.html`'s delivery paragraph:

> Weekly lists. Every Monday, itgraph sends to each language's topic a list of up to ten channels whose videos published the previous week did best against that channel's own videos, one video per channel, each with the same content as an alert. A list record holds IDs, itgraph's scores and when it was sent; an entry is deleted together with the reading it was calculated on.

`docs/index.html`'s status line still says the scores that would raise YouTube alerts are not calculated; that has been untrue since change 7, and is the same edit's.

### 13. Docs

`docs/PLAN.md`, YouTube: the weekly list, the week's replay table, the change 8 row. `src/itgraph/README.md`: `yt weekly`, its summary and preview. `src/itgraph/CLAUDE.md`: rows for `scoring/yt_weekly.py`, `db/yt_weekly.py`, and the changed bot rows. `deploy/runbook.md`: the CronJob, the series, a list that did not go. `docs/*.html` is not touched.

## Risks / Trade-offs

- **[Russian lists are short]** → about six to ten entries at z 2.0 on one replayed week; a shorter list rather than noise. The floor and the size move with calibration.
- **[A floor below the alerts' lists more that fades]** → 7% of decided crossings at z 2.0 against 3% at 3.0, on one week; the list is a ranking, so the faded sit at its bottom.
- **[Sunday's videos are judged on hours]** → their wider spread already asks more of them; a video that takes off on Tuesday after a Sunday publication is in no list. Accepted for one list per video.
- **[An entry is three lines]** → ten entries are about 3,500 characters; `digest_parts` splits what does not fit.
- **[Runs missing for days]** → each reading takes the newest run before it, however old, as the live pass would have; the summary names the runs used.
- **[The site lags the product]** → the list does not go to production before the operator's edit (Migration Plan, 3).

## Migration Plan

1. Implement, `make validate` and `make test-e2e`; the revision verified on a `_test` database.
2. **(operator)** A release; the role in the test environment; a restore from a fresh full dump. There: `itgraph yt weekly --preview` for the newest ended week, then a Job made from `itgraph-yt-weekly` — `kubectl create job --from=cronjob/itgraph-yt-weekly` — and both lists read in the test chat's topics.
3. **(operator)** The site's edit (Decision 12).
4. **(operator)** The release pinned in production; the first list the next Monday at 10:00.
5. Rollback: revert the pin. The tables stay unread; the downgrade drops them.

## Open Questions

None.
