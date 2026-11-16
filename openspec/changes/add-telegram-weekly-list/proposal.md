# Proposal

## Why

Change B of "Late growth" (`docs/PLAN.md`): Telegram gets the weekly list YouTube got in change 8, so that change C can later add a "second wind" section to both. Telegram's alerts say "now", about eight messages a day. Read back from the chat, they cannot tell what the week was about. A weekly list per channel, of what did best against that channel's own posts, is what YouTube's readers already get on Mondays.

Measured on the production database on 2026-11-16, over the five weeks from 2026-10-12, as aggregates:

- **The alerts' threshold already fills a list.** 29 to 41 distinct channels a week had a post cross z 3.7, with a median crossing of about z 4.2. A list of ten at the alerts' threshold is full every week. YouTube's floor of 2.0 was there to fill a thin Russian list, a reason Telegram does not have.
- **Telegram's bands end at 9.5 hours.** So a list scored as the live pass scores, at the alerts' threshold, is the best of the week's alerts by construction, and the count above is exact rather than an estimate.
- **Views carry a channel's best in most weeks:** 136 of 177 channel-weeks, against 27 for reactions, 9 for forwards and 5 for comments.
- **The kinds in a week's ten,** over five weeks: personal 20, aggregator 12, event 6, company 5, media 4, vacancies 2, community 1.
- **Families do not crowd a list.** Each of the five tens held ten distinct author families, so one entry per channel needs no family rule.
- **Telegram's baseline runs are kept**: 37 completed runs since 2026-08-06, about 1,340 channel references each. Every reading of a past week can be scored against the run in force when it was taken, with no migration, and a list can be previewed for past weeks at once.

## What Changes

- **A weekly list for Telegram.** Every Monday at 10:00 Moscow time, for the calendar week just ended (Monday to Monday, Moscow): the posts the seeds published that week, scored. The list holds the top ten channels at or above the alerts' threshold, **one entry per channel** (its best post), with how many more of its posts reached the threshold. Fewer channels make a shorter list; none make a message that says so. One list, to Telegram's alert chat: Telegram's channels carry no language.
- **Scored as the live pass saw it.** Each reading is scored against the baseline run that was newest when it was taken, through the scoring pass's own reading of posts (an album as its first part, a burst as its first message), on the alerts' four signals, with their comment minimum. A post's score is its best across readings and signals. No refit and no new baseline.
- **An entry shows what an alert shows:** the channel, the post's age, its link, the first line of its text, and the signal with how many times the usual and its z. It also shows how many more of the channel's posts reached the threshold. No counts, as no Telegram alert has them.
- **Stored as a list, sent by the bot,** the way YouTube's are: in parts that fit, outside the daily cap and the digest, after quiet hours, without a link preview.
- **`itgraph weekly [--week] [--preview] [--now]`**; a CronJob in every environment; two series, the newest list computed and delivered; the restore closes unsent lists and tries the pass on the copy by preview.
- **Shared week arithmetic:** the week's bounds, the last ended week, the refusal of a week not ended, and the ranking move out of `scoring/yt_weekly.py` into a module both platforms use. YouTube's list does not change.
- **Docs:** `docs/PLAN.md`, `src/itgraph/README.md`, the module map, the runbook. `docs/*.html` is not touched (decision record).

**Out of scope:**

- The "second wind" section: change C, `add-second-wind`.
- Counts in an entry, a list per author family, any filter by channel kind.
- Changes to Telegram's scoring, alerts or threshold.

## Capabilities

### New Capabilities

- `telegram-weekly-list`: the weekly list of what took off on Telegram. It covers the week, how a post and a channel are ranked, what the list holds and keeps, what its message says, and how it is delivered.

### Modified Capabilities

- `alert-delivery`: the bot's writes are confined to the delivery state of the alert queues and of both platforms' weekly lists.
- `collection-deploy`: Telegram's weekly list is scheduled in every environment, Mondays at 10:00 Moscow time.
- `collection-monitoring`: two series of Telegram's weekly list, and what the exporter may read of it.
- `database-backups`: a restore closes Telegram's unsent weekly lists and tries Telegram's weekly pass on the copy; a stale copy's Telegram list is empty and exits zero.

## Impact

- **Code:**
  - New: `scoring/weeks.py` (shared, pure), `scoring/weekly.py`, `db/weekly.py`.
  - `scoring/yt_weekly.py`: the week arithmetic and the ranking move out, its behaviour unchanged.
  - `db/baselines.py`: a run by id, and the runs completed within a period.
  - `scoring/run.py`: its reading of posts made callable for a week.
  - Bot: `bot/render.py`, and `bot/app.py` with the weekly lists of both platforms behind one interface.
  - `config.py`, `cli.py`, `metrics/series.py`.
  - No new dependency.
- **Schema:** one Alembic revision: `weekly_lists`, `weekly_entries`, the bot's grants on their delivery state, the exporter's on the lists' moments.
- **Role:** one CronJob and its schedule; `neutralise.sql` and `readback.sql`; the restore's trial; `e2e.sh`.
- **Infrastructure repository:** an alert on a Telegram list not delivered by Monday afternoon, and a panel.
- **Production:** one message a week in Telegram's alert chat, from the first Monday after the pin.

## Decision record

Brought in the explore session of 2026-11-16 as four questions, on the production database's aggregates. The operator's answers:

> Недельный список TG повторяет форму YouTube.

> Всё как рекомендуешь. Пиши артефакты.

Confirmed as of 2026-11-16:

1. The form is YouTube's (change 8): a calendar week by publication, Monday 10:00, ten channels, one entry per channel with its others counted, a shorter list rather than a padded one, an empty week said, each reading against the run in force when taken, outside the cap and the digest.
2. **The floor is the alerts' threshold** (z 3.7 today), not YouTube's 2.0: a full list every week at it, and a quiet week makes a shorter list rather than one padded with posts that did not alert.
3. **An entry is what a Telegram alert is**: channel, age, link, the first line of the text, the signal with its multiple and z, and the count of the channel's others. No counts.
4. **Every kind of channel**, as the alerts.
5. **The site is not touched,** by the operator's decision of 2026-11-16 to ignore where `docs/privacy.html` and the code disagree.

**Not covered by the requirements.** These were chosen in `design.md`, not by the operator:

- The floor follows `alert_spike_z` itself, with no setting of its own (design.md, Decision 3).
- The list is computed from the readings, not read out of the alerts table (design.md, Decision 2).
- The first line of the text, cut at 120 characters (design.md, Decision 6).
- An entry keeps the count its score came from beside the expectation, as a Telegram comment alert does, so the bot reads no snapshot (design.md, Decision 5).
- The names `weekly_lists`, `weekly_entries`, `itgraph weekly`, `itgraph-weekly`, and the two series (design.md, Decisions 5, 9 and 10).
- The week's arithmetic and the ranking shared with YouTube's list, unchanged for it (design.md, Decision 1).
