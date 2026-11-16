# Design

## Context

See `proposal.md` (Why, and the decision record). The requirements are in `specs/`. What shapes the approach, read from the code and from the production database on 2026-11-16:

- **YouTube's list is the template** (`add-youtube-weekly-list`): `scoring/yt_weekly.py`, `db/yt_weekly.py`, `yt weekly`, the bot's `_deliver_weekly`, two series per route, the restore's preview trial. Its week arithmetic and ranking know nothing of YouTube except their types.
- **Telegram's baseline runs are kept.** `db/baselines.py` says so ("Older rows stay"), and production holds 37 completed runs since 2026-08-06. But `load_baselines` reads only the newest, and Telegram's replay (`run_scoring` with `dry_run`) scores past moments against the newest run, not the run in force.
- **Telegram's bands end at 9.5 hours** (`BAND_EDGES_MINUTES`, `scoring_window_hours`). A post's last scored reading is under ten hours old, and late readings (`add-late-readings`) are in no band. So a post published on Sunday at 23:00 is fully scored by 08:30 on Monday.
- **How posts are read for scoring** is `scoring/run._load`: an album by its first part, a burst by its first message, seeds that are not chats.
- **Ranking:** a post's best across readings and signals is `scoring/score.best_per_post`, the same function YouTube's list reaches through `yt_run.best_per_video`.
- **What a Telegram alert shows** (`bot/render.render_spike`): its signal with a magnitude and z, the channel, the post's age, a link where the channel has a username, an excerpt, and a fold note. A comment alert gives its count and the usual count. It shows no counts of views, reactions or forwards. Its evidence comes from `db/alerts.alert_evidence`: the channel's title and username, the publication, and the text.
- **The bot's lists are YouTube's alone.** `bot/app._deliver_weekly` returns for a destination with no route, which is exactly Telegram's.

## Goals / Non-Goals

**Goals:**

- Once a week, the channels whose posts of that week did best against their own, in Telegram's alert chat.
- The same judgement as the alerts' and as YouTube's list: the same scores, signals, exclusions, run in force and threshold.
- YouTube's list unchanged, its tests unedited.

**Non-Goals:**

- The second wind, counts in an entry, a family rule, a kind filter: proposal.md, Out of scope.

## Decisions

### 1. The week's arithmetic and the ranking are shared

`scoring/weeks.py`, pure, gets everything of `scoring/yt_weekly.py` that does not know YouTube:

- `WEEK`, `WeekRefused`, `week_bounds`, `last_ended_week`, `check_week`;
- `merge_best` (today's `_merge`);
- `rank_week`, generic over a post key `(channel, item)`, with its tie order: the earlier crossing, then the item.

`scoring/yt_weekly.py` imports them and keeps exporting the same names, so YouTube's behaviour and its tests stay as they are.

Telegram's week is read in the Telegram chat's zone, `watch_timezone` (Moscow), which is the zone its alerts and digest use.

### 2. From the readings, not from the alerts table

The list is computed as YouTube's is: the week's readings, each against the run in force when it was taken.

Considered and rejected: reading the list out of `alerts`, whose crossings at the threshold it would mostly repeat. But:

- an alert exists only where the live pass ran, and a pass down for hours raised nothing;
- an alert keeps the first crossing per band, not the post's best reading;
- a restored copy's alerts are production's;
- a past week before a change of threshold was alerted at another threshold.

The readings give the live pass's answer for any week and on any copy, and agree with the alerts wherever the live pass ran.

### 3. The threshold is the alerts', with no setting of its own

The operator chose the alerts' threshold. A setting of its own would let the two drift apart, and a re-pricing of the alerts would silently leave the list behind. So the floor is `alert_spike_z` itself, which the role takes as `itgraph_alert_spike_z`.

Because the bands end at 9.5 hours, the list is the best of the week's crossings: measured, 29 to 41 channels a week, so ten is full every week. The size is a setting, `weekly_list_size` (10, at least 1). The signals and the comment minimum are the alerts': `alert_spike_metrics` and `alert_spike_comments_min`.

### 4. Scored against the run in force

- **`db/baselines.py`** gains `runs_in_force(session, since, until)`, Telegram's twin of YouTube's: the newest run completed at or before `since`, and every run completed after it and before `until`, oldest first. It also gains `load_baselines(session, run_id=None)`: the given run, or the newest completed as today. `current_run` and every caller of `load_baselines` stay as they are.
- **`scoring/run.py`'s `_load`** becomes `load_readings(session, since, now)`, unchanged. The weekly pass takes the readings of posts published in the week and taken up to the moment of the run.
- **Each run's cut**, the readings taken from its completion to the next's, goes through `best_per_post` against that run, with the alerts' metrics, the threshold and the comment minimum. A post's best across cuts is `merge_best`. Readings before the first run are counted, not scored.

The summary names the runs used — how many, and their ids — the posts published and scored, and the readings with no run before them.

### 5. Storage

One revision:

```
weekly_lists    id, week_start date UNIQUE, computed_at, threshold, size, comments_min,
                posts_scored, channels_above, delivered_at, attempts, last_error
weekly_entries  list_id → weekly_lists CASCADE, rank, channel_id → channels(tg_id),
                msg_id, scored_at,
                (channel_id, msg_id, scored_at) → message_metrics CASCADE,
                metric (the existing `metric` type), value, observed, expected,
                others, delivered_at
                PK (list_id, rank), UNIQUE (list_id, channel_id)
```

No `yt_` prefix: the tables are Telegram's, as `alerts` is ("Separate tables, not a platform column", `docs/PLAN.md`).

An entry keeps `observed`, the scored reading's count of its signal, beside `expected`, as a Telegram comment alert keeps both. The multiple and a comment entry's count are then the entry's own numbers, and the bot reads no snapshot. YouTube's entry copies no count because of what it promises about YouTube's data. Telegram's raw layer has no such rule, and its alerts already keep the count.

**An entry lives as long as its reading.** The reading goes only with its channel, and the list row stays.

**Grants:**

- the bot's role: `SELECT` on both tables, and `UPDATE` on the lists' `delivered_at`, `attempts`, `last_error` and the entries' `delivered_at`;
- the exporter's: `SELECT` on the lists' `computed_at` and `delivered_at`;
- `itgraph_reader`: both tables through its default privileges.

### 6. The message

One list a week, in Russian as the alerts are, every moment in Moscow time. Each entry is whole in one part:

```
📋 Telegram — выше нормы за неделю 16–22 ноября
Лучший пост каждого канала относительно его обычного уровня; оценено 3 912 постов.
Оценка itgraph на 23.11 10:00 MSK — рассчитана itgraph.

1. <b>Канал</b> (@channel) · опубликовано 3 дн назад
https://t.me/channel/1234
Первая строка поста, не длиннее ста двадцати символов…
👁 Просмотры — в 4 раза выше обычного для канала (z 4.2); ещё 2 поста канала выше порога
```

- **The moment** reads as YouTube's list's does: the time with the zone, and the date when it is not today's.
- **The channel** is its title in bold, as YouTube's entry has it, then its `@username`; a channel without a title is named by its `@username` alone.
- **The age** is written as an alert writes it.
- **The first line** is the post's text up to its first line break, with spaces collapsed, cut at 120 characters with «…», and escaped. A post with no text has no line.
- **The signal** is named as the alerts name it: «👁 Просмотры», «❤️ Реакции», «🔁 Пересылки», «💬 Комментарии».
- **The multiple** is `observed / expected`, written as YouTube's entry writes it.
- **A comment entry** says «💬 Комментарии — 37, обычно около 3 (z 5.1)», as a comment alert does.
- **A channel without a username** has no link, as an alert has none.

The fold note of an alert has no place here: «ещё N постов канала» says the same once for the week.

### 7. An empty week

The header and «выше порога — ничего», as YouTube's list says it. The count of posts scored tells "nothing took off" from "nothing was scored", which is what a stale copy in the test environment will say every Monday.

### 8. Delivery

The bot's lists get the two platforms behind one interface, as its alerts did in `bot/queues.py`:

- a weekly source per platform knows which lists a destination takes, how a header and an entry read, and how parts and lists are marked;
- `_deliver_weekly` and `_send_list` become the shared loop;
- YouTube's source is today's code, per route;
- Telegram's takes every undelivered list for the Telegram destination, oldest week first, with each entry's evidence from `db/alerts.alert_evidence`.

Everything else is YouTube's rule:

- in parts through `message_parts`, a part's entries marked as it goes;
- a failed part an attempt on the list;
- outside quiet hours, outside the cap and the digest, without a link preview;
- nowhere else when the chat refuses;
- with no alert chat configured, the list waits.

An entry whose reading is gone is left out, and the ranks close up.

### 9. Series

| Series | Type | From |
|---|---|---|
| `itgraph_telegram_weekly_list_computed_timestamp_seconds` | gauge | Telegram's newest list, 0 when none |
| `itgraph_telegram_weekly_list_delivered_timestamp_seconds` | gauge | when Telegram's newest delivered list went out, 0 when none |

These come from the list rows alone. The infrastructure repository is asked for an alert on a newest delivered list older than seven days and six hours, as YouTube's has, and a panel.

### 10. Settings, command, schedule

- **`config.py`:** `weekly_list_size` (10, at least 1). There is no floor setting (Decision 3).
- **`itgraph weekly [--week YYYY-MM-DD] [--preview] [--now]`:**
  - computes and stores the list of a week;
  - `--preview` prints rank, z, signal, channel id, message id and others, and writes nothing;
  - the summary: entries and channels at the threshold, posts published and scored, readings with no run, the runs used;
  - no session, no lease, no request; logs ids only.
- **CronJob `itgraph-weekly`:**
  - schedule `itgraph_weekly_schedule`, `0 10 * * 1` Moscow time;
  - starting deadline 518,400 s, active deadline 1,800 s;
  - in `__itgraph_cronjobs_always`, labelled for the database alone, with the configuration and the database's URL and no other secret;
  - it needs neither the session nor a key, so it runs in the test environment too, into that environment's own chat.

### 11. Backups and the restore

- Full dumps carry both tables.
- `neutralise.sql` marks every undelivered list and entry delivered, and `readback.sql` fails while one remains. The suite requires that removing either statement fails the read-back.
- The restore's trial runs `itgraph weekly --preview` on the copy, checks that no list was written, and reports it.
- `e2e.sh`:
  - renders the CronJob in an environment that does not collect;
  - runs a weekly pass with `--week` and `--now` on synthetic readings and reads back the expected entry;
  - has the bot deliver the list to the stub chat without a preview.

### 12. Docs

- `docs/PLAN.md`: change B in "Late growth", with what the list is and the five weeks measured.
- `src/itgraph/README.md`: `itgraph weekly`, its summary and preview, and the series.
- `src/itgraph/CLAUDE.md`: rows for `scoring/weeks.py`, `scoring/weekly.py` and `db/weekly.py`, and the changed rows of `scoring/yt_weekly.py`, `scoring/run.py`, `db/baselines.py`, `bot/app.py` and `bot/render.py`.
- `deploy/runbook.md`: the CronJob, the series, and what to do about a list that did not go.
- `docs/*.html` is not touched.

## Risks / Trade-offs

- **[The list repeats the week's alerts]** → By construction it is their top ten, ranked. A reader of daily alerts learns the ranking; a reader of Mondays learns the week. Change C adds what no alert says.
- **[A week the live pass was down]** → The list still scores those readings, so it can name a post that never alerted. The summary names the runs used.
- **[Runs missing for days]** → Each reading takes the newest run before it, however old, as the live pass would have.
- **[Forty channels for ten places]** → It is a ranking: the count of a channel's others says how much more there was. The size moves with a release.
- **[An entry takes four or five lines]** → Ten entries come to about 3,000 characters; `message_parts` splits what does not fit.

## Migration Plan

1. Implement; `make validate` and `make test-e2e`. Verify the revision on a `_test` database.
2. **(operator)** Cut a release, deploy the role to the test environment, and restore from a fresh full dump. There:
   - run `itgraph weekly --preview` for each of the last two ended weeks, and read them against the five weeks measured;
   - make a Job from `itgraph-weekly` and read the list in the test chat.
3. **(operator)** Pin the release in production. The first list goes the next Monday at 10:00. Ask the infrastructure repository for the alert and panel of Decision 9.
