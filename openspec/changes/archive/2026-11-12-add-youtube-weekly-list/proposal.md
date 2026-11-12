# Proposal

## Why

The task as received names "what took off" on English-language and Russian-language IT YouTube. Change 7 answers it as it happens, with alerts at z 3.0; the operator decided then that the periodic list per language would be a change of its own, after it. An alert says "now"; nobody can read a week back out of a topic's history, and at the alert threshold the Russian topic gets three or four channels a week — too few to see what the week was about.

## What Changes

- **A weekly list per language.** Every Monday at 10:00 Moscow time, for the calendar week just ended (Monday to Monday, Moscow): the videos that channels in scope published that week, scored, and the top ten channels above z 2.0 — **one entry per channel**, its best video, with how many more of its videos were above the floor. Fewer than ten above the floor make a shorter list; none make a message that says so.
- **Scored as the live pass saw it.** Each reading against the baseline run that was newest at the moment of the reading, through the scoring pass's own code; the best per video across readings and signals, the comment minimum first, broadcasts not scored. No refit, no new baseline.
- **Stored as a list, sent by the bot.** A pass writes the list per language — ids, ranks, scores, nothing YouTube said — and the bot sends it to the language's YouTube topic: in parts that fit, outside the daily cap and the digest, after quiet hours, without a link preview. Each entry names YouTube, links the video, shows its counts with the moment they were read and itgraph's score as itgraph's.
- **`itgraph yt weekly [--week] [--preview] [--now]`**, needing no YouTube key; a CronJob in every environment; a series per language of the newest list computed and delivered; the restore closes unsent lists and tries the pass on the copy.
- **Docs:** `docs/PLAN.md`, `src/itgraph/README.md`, the module map, the runbook. `docs/*.html` is not touched by this change; the site's wording is the operator's (design.md, Decision 12).

**Out of scope:**

- Telegram: no weekly list there, and its scoring and alerts do not change.
- Calibration of the floor and the list's size on weeks of production data.
- What happened to a video after the list (held, faded): the replay's measure, not the list's.
- The bot's `/status` reporting the list.

## Capabilities

### New Capabilities

- `youtube-weekly-list`: the weekly list of what took off on YouTube per language — the week, how a video and a channel are ranked, what the list holds and keeps, what its message says and how it is delivered.

### Modified Capabilities

- `alert-delivery`: the bot's writes are confined to the delivery state of the alert queues and of the weekly lists.
- `collection-monitoring`: two series of the weekly lists per language; what the exporter may read of them.
- `collection-deploy`: the weekly list's pass is scheduled in every environment, Mondays at 10:00 Moscow time.
- `database-backups`: a restore closes unsent weekly lists, and tries the weekly pass on the copy.

## Impact

- **Code:** new `scoring/yt_weekly.py` and `db/yt_weekly.py`; `db/yt_baselines.py` (a run by id, and the runs completed within a period); `scoring/yt_run.py` (its preparing and best-per-video shared, unchanged); `bot/render.py`, `bot/app.py` and `bot/destinations.py` or a sibling for the list's delivery; `config.py`; `cli.py` (`yt weekly`); `metrics/series.py`. No new dependency.
- **Schema:** one Alembic revision: `yt_weekly_lists`, `yt_weekly_entries`; the bot's grants on their delivery state, the exporter's on the lists' route and moments.
- **Role:** one CronJob and its schedule; `neutralise.sql` and `readback.sql`; the restore's trial; `e2e.sh`.
- **Infrastructure repository:** an alert on a list not delivered by Monday afternoon, and a panel.
- **The site:** a sentence on the weekly list, by the operator (design.md, Decision 12).
- **Production:** one message a week in each YouTube topic, from the first Monday after the pin; the first list covers 16–22 November 2026 at the earliest.

## Decision record

Brought as four questions and a list of defaults in the explore session of 2026-11-12, on a week replayed in the test environment at four thresholds and on the production database's aggregates. The operator's answer:

> Всё как рекомендуешь

Confirmed as of 2026-11-12:

1. The week is a calendar week of publication, Monday to Monday, Moscow time; the list goes on the Monday after, at 10:00. Each video is in one list at most.
2. Ten channels per language, above a floor of z 2.0; a shorter list rather than one padded below the floor.
3. One entry per channel, its best video, Shorts and long-form together with Shorts marked, and the count of its other videos above the floor.
4. The same YouTube topics as the alerts, outside the cap and the digest.
5. YouTube only.
6. Ranked by the best score across signals, as alerts are.
7. A week with nothing above the floor sends a message saying so.
8. Computing the list again for the same week writes nothing.
9. `--preview` prints the list by ids and writes nothing.
10. The restore closes unsent lists; the pass runs in the test environment too, into its own chat.
11. Series of the list per language, and an alert on a list not delivered, in the infrastructure repository.

**Withdrawn after the answer, against the site.** Question 3 was recommended as "no YouTube counts in an entry", to keep an entry short. The site says that itgraph's score "is always shown next to the counts YouTube reports" (`docs/index.html`, `docs/terms.html`; `docs/privacy.html`: "always shown alongside"). An entry without counts would break that promise, so each entry shows the counts with the moment they were read — question 3's option (b). Changing the promise instead is the operator's, on the site.

**Not covered by the requirements** — chosen in `design.md`, not by the operator:

- "One line per channel" read as one entry per channel; an entry takes four lines of a message — title, link, counts, score (design.md, Decision 6).
- The readings counted are those up to the moment the list is computed, not up to the week's end (design.md, Decision 1).
- Each reading against the run newest at its moment, not one run for the week and not a refit (design.md, Decision 2).
- The list's language is the channel's label when the list is computed, not when it is sent, unlike an alert (design.md, Decision 4).
- The list waits out quiet hours, is not counted against the cap, and is sent without a link preview (design.md, Decision 8).
- An empty week is sent, against the digest's rule that a summary of nothing is not (design.md, Decision 7).
- The size and the floor are settings of `config.py` alone, not role inputs (design.md, Decision 10).
- The restore's trial is the pass with `--preview`, not a list delivered (design.md, Decision 11).
- The names of the two series; a CronJob that catches up within six days (design.md, Decisions 9 and 10).
