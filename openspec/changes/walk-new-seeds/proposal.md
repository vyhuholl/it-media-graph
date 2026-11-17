# Proposal

## Why

The first of the changes that bring rising channels into the inventory (the explore of 2026-11-16): once a channel is approved, its history should arrive on its own. Today it does not on Telegram. The poll loop reads a new seed's newest window from the next poll on, but its history back to the inventory's depth comes only from `itgraph backfill`, which the operator runs by hand with the loop stopped. That costs the operator a stop of collection per new seed. Until it is run, the new seed has no outgoing edges, no mature reference for scoring, and no mentions. On YouTube a new seed is already walked by itself, but only by the next daily run at 11:30.

The changes after this one find candidates and let the operator approve them in the bot. Each approval is a new seed, so this walk is what they rely on. The trigger is the seed's state rather than how it became one, so a seed marked from the CLI is walked the same way.

Measured on the production database on 2026-11-17 (aggregates only):

- **Every seed's history is complete today:** 550 of 550. 534 are walked to the common cutoff of 2025-07-24, and 16 stopped at the 2,000-message ceiling. Each of those 16 has its cutoff equal to its oldest stored post, the sign of a channel that hit the ceiling, not of a later `--since`.
- **A walk to that cutoff is short.** Messages held per seed: median 243, p90 1,300. At 100 a page under the ceiling, that is 3 requests at the median, 13 at p90 and 20 at most — against the 3–4 thousand `messages.getHistory` the loop makes a day.
- **New seeds have been rare so far:** 564 reviewed in July, 7 in August, none since. The changes after this one are expected to bring about one or two candidates a day.
- **Every seed has a username and a review time,** so a seed's wait for its walk can be measured from when it was marked.

## What Changes

- **The poll loop walks new Telegram seeds.** A seed in scope whose walk is not complete is walked back to the common cutoff (`2025-07-24`, a setting), under the same 2,000-message ceiling as the inventory's, through the same cursor `itgraph backfill` keeps. It goes one page at a time, only when no poll is due, so polls lose no sample. The pacing is the walk's, there is no walk in quiet hours, and no quota-bearing request is made. A walk resumes where it stopped after a restart.
- **A walk that could not run is tried again an hour later.** That covers a peer the session does not hold and a transient failure. A permanent failure takes the channel out of scope, as it does today. A long rate limit postpones the polls and the walk alike.
- **What triggers it is the inventory's state, not a command.** `itgraph mark --seed`, `itgraph add --seed` and the bot's review in a later change all lead to the same walk. A seed whose walk is complete is never walked again, whatever its depth. `itgraph backfill` stays, for an operator who wants a channel walked deeper.
- **The mention graph needs nothing new.** `itgraph derive` runs every 15 minutes and reads the new rows. A new seed's outgoing edges appear within a quarter of an hour of its walk.
- **Two new series.** How many seeds await their walk, and how long the oldest has waited since it was marked. The exporter's role is granted those two columns and nothing else of the inventory. The infrastructure repository is asked for an alert and a panel.
- **YouTube's walk runs hourly instead of daily.** `itgraph-yt-backfill` goes from 11:30 every day to half past every hour, and may start up to 55 minutes late rather than 23 hours. A run with nothing to walk spends no unit.

**Out of scope:**

- Resolving a candidate's username so that its peer is cached: change 2, with the loop's daily cap on `contacts.resolveUsername`.
- Review in the bot: change 3. YouTube's rising candidates: change 4.
- `itgraph metadata` for a new seed (its description and linked chat). It stays monthly and by hand: its request carries a daily quota, and nothing in a walk or a poll needs it.
- Walking a seed deeper than its completed walk. That stays `itgraph backfill --since`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `message-backfill`: a seed whose walk is not complete is walked by the poll loop, page by page when no poll is due, to the configured cutoff under the ceiling. One that could not be walked is tried again an hour later.
- `metric-snapshots`: when no channel is due, the loop may walk a page instead of only waiting.
- `collection-monitoring`: two series on seeds awaiting their walk, and the exporter reads nothing of the inventory but what they need.
- `youtube-backfill`: the unattended walk runs hourly. "Unattended Walk" is replaced by "Hourly Unattended Walk", since a modified requirement cannot drop its daily scenario.

## Impact

- **Code:** `tg/backfill.py` (one page of a walk as a step of its own, which `itgraph backfill` loops over and the poll loop takes one at a time), `tg/watch.py` (a walk page when no poll is due, the hourly retry, the shared postponement), `db/backfill.py` (the seeds awaiting their walk), `config.py` (the common cutoff), `metrics/series.py`. No new dependency.
- **Schema:** one Alembic revision. It grants the exporter `SELECT (reviewed_at)` on `tg_channels` and `SELECT (status)` on `tg_backfill_state`, and creates no table.
- **Role:** `itgraph_yt_backfill_schedule` from `30 11 * * *` to `30 * * * *`, with its starting deadline from 82,800 s to 3,300 s. No new workload, secret or connection.
- **Docs:** `src/itgraph/README.md` (the loop walks new seeds; `backfill` for deeper walks; the hourly YouTube walk), `src/itgraph/CLAUDE.md` (the rows of `tg/watch.py`, `tg/backfill.py`, `db/backfill.py`), `deploy/runbook.md` (the series, the schedule), `docs/PLAN.md`.
- **Infrastructure repository:** an alert on a seed waiting more than a day for its walk (`itgraph_telegram_oldest_awaiting_history_seconds` past 86,400), and a panel of both series.
- **Release:** a release of its own, test environment first. The walk cannot be tried there, since a test environment does not collect. The role's tests and e2e cover the schedule and the series, and the first production seed marked after the pin is the walk's first real run.

## Decision record

Brought in the explore session of 2026-11-16, on the production database's aggregates. The operator's words, then:

> Если я одобрила, то для блога сразу же делается годовой backfill и строится граф упоминаний. (Возможно, имеет смысл сделать так, чтобы при добавлении канала в сиды через CLI тоже сразу же делался годовой backfill и строился граф упоминаний.)

> 6. До общей даты отсечения. Но с тем же лимитом, который был при backfill текущих сидов.

On 2026-11-17, asked how the loop walks, what to do with a walk that could not run, how often YouTube's walk runs and how this change ships, the operator chose the recommended answer to each: page by page when no poll is due; tried again an hour later; hourly; a release of its own.

Confirmed:

1. A new seed is walked without an operator, to the common cutoff, under the existing ceiling. The trigger is the seed's state, so the CLI's seeds are walked too.
2. On Telegram, the poll loop walks it one page at a time, only when no poll is due.
3. A skipped or transiently failed walk is tried again an hour later; a permanent failure never.
4. YouTube's unattended walk runs hourly.
5. This change ships in a release of its own.

**Not covered by the requirements.** These were chosen here, not by the operator:

- The cutoff is a setting, `2025-07-24T00:00Z`, the depth the inventory was walked to (design.md, Decision 2).
- A seed whose walk is complete is never walked again by the loop, whatever its depth (design.md, Decision 2).
- The hour's retry is kept in the loop's memory, and a restart tries everything at once (design.md, Decision 4).
- One walk at a time, the seed marked earliest first. A new seed's first page waits out the walk's pause between channels, measured rather than slept (design.md, Decision 3).
- A rate limit too long to wait out holds the walk until it ends, as it postpones the polls (design.md, Decision 5).
- The two series, measured from when a channel was marked a seed, and the alert threshold of a day asked of the infrastructure repository (design.md, Decision 6).
- The YouTube CronJob's starting deadline of 55 minutes (design.md, Decision 7).
