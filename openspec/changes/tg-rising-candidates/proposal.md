# Proposal

## Why

The second of the changes that bring rising channels into the inventory (the explore of 2026-11-16). Telegram's inventory grew by references, as PLAN's "Expanding the seed set" describes. But nothing has said which of the references are new — a channel that several seeds started citing in the last two weeks — and nothing has resolved them without an operator. The last resolution and the last review were in August. Since then the queue has grown to 2,519 unreviewed candidates and 6,829 pending usernames, unranked by recency.

This change answers "who is rising" and makes the answer reviewable from the command line. The daily selection, the bot's message and its buttons are the next change. A seed marked from what this change shows is walked by the poll loop since `walk-new-seeds`.

Measured on the production database on 2026-11-17 (aggregates only):

- **The weekly flow at the threshold the operator accepted** — at least two distinct seed families in the trailing 14 days, the candidate's own family left out. Channels known by id crossed it for the first time 5–9 times a week from late September to mid-November. Pending usernames first seen in a fortnight and named by two families or more: about 6–8 a week. So about 12–16 a week.
- **Rising now:** 16 known channels — 9 with a username, 7 never resolved — and 6 pending usernames first seen in the last 14 days, named by two families or more.
- **Most of the list needs a resolution before anyone can look at it:** a forward leaves a bare id, a mention a bare username. That is 13 now, and about 2 a day at the weekly rate.
- **The account has met the resolution quota.** Three `FloodWait`s on `ResolveUsernameRequest` in 120 days, the longest 85,490 s, all during the manual resolution runs of July.
- **Pending usernames carry no dates.** A source of a mention is a (username, channel) pair. The window cannot be applied to it without knowing when the channel referred to the name.

## What Changes

- **A ranking of rising candidates, worked out when asked and never stored.**
  - What is ranked: channels in status `candidate` or `maybe`, and pending usernames.
  - What counts: references from seeds that are not chats — forwards and mentions alike.
  - The threshold: at least `rising_min_families` (2) distinct seed families in the trailing `rising_window_days` (14). The candidate's own family is left out.
  - The order: by families in the window; then by families that referred to it before the window, fewest first, so the newer comes first.
  - Left out, and counted: supergroups, channels without a public username, and whatever resolved to a user, a bot or nothing.
- **`itgraph rising`** prints the ranking: each channel with a username, its title, its families in the window and before, and when it was first referred to in the window. Then how many await a resolution and how many were left out, and the resolutions of the last 24 hours against the cap. It reads the database only: no session, no request. A pending username is never printed, since it may name a person; it is counted until it resolves into a channel.
- **The poll loop resolves the head of the ranking.** When nothing is due and no walk has a page to take, it resolves the highest-ranked candidate still awaiting an identity:
  - by id for a channel, by username for a pending name, through the resolution pass's own code;
  - at most one every `rising_resolve_gap_minutes` (20);
  - and only while fewer than `resolution_cap` (10) resolutions were made in the last 24 hours by any command.
- **A journal of resolutions.** Every resolution by `itgraph resolve`, `itgraph add` and the loop is a row: when, by which command, of which kind (id or username), with which outcome. Never which name: a username can name a person. The cap is read from it.
- **A rate limit on resolution stops resolution, not collection.** The loop's resolution sleeps off nothing. A wait the client hands back is recorded and holds resolution until it ends, read from `tg_flood_events`, so a restart does not ask again. The polls and the walk go on: Telegram's limits are per method.
- **Pending mentions remember when.** Each source of a pending username keeps the publication time of the first and the last message in which that channel referred to it, written by `itgraph derive` and rebuilt with it. The next derivation after the release fills them for every row.
- **A series:** resolutions in the last 24 hours, by command. The infrastructure repository is asked for an alert on the loop's own count above the cap — only a bug could cause that.

**Out of scope:**

- The daily selection at 11:00, the bot's message and its review buttons: change 3.
- YouTube's rising candidates: change 4.
- Retrying a resolution that failed. As `itgraph resolve` does without `--retry-failed`, a failed one is left out.
- Ranking by the referrers' authority (PLAN's PageRank). Families are the unit until the list shows they are not enough.

## Capabilities

### New Capabilities

- `telegram-rising-candidates`: which Telegram channels and pending usernames are rising, how they rank, what is left out, `itgraph rising`, the poll loop's capped resolution of the ranking's head, and the journal of resolutions the cap is read from.

### Modified Capabilities

- `forward-graph`: a pending mention's source keeps when its channel first and last referred to the name.
- `metric-snapshots`: the poll loop may resolve the head of the rising ranking under the cap, and nothing else; polling itself still spends no quota-bearing request.
- `message-backfill`: who may issue `contacts.resolveUsername` — the resolution pass, `itgraph add` and the poll loop's capped resolution, never a walk, a poll or the metadata pass. The old wording named the resolution pass alone, which `itgraph add` already contradicted.
- `collection-monitoring`: a series of resolutions in the last 24 hours, by command, read from the journal's time and command alone.

## Impact

- **Code:**
  - `derive/rising.py` (the ranking; pure), `db/rising.py` (loading the evidence; the journal), `tg/rising.py` (the loop's one resolution).
  - `tg/resolve.py` and `tg/manual.py` (journaling), `tg/watch.py` (the loop's turn), `tg/backfill.py` (a halt threshold a caller can lower).
  - `derive/edges.py` and `db/edges.py` (dated sources), `metrics/series.py`, `config.py`, `cli.py` (`rising`).
  - No new dependency.
- **Schema:** one Alembic revision.
  - `first_referenced_at` and `last_referenced_at` on `tg_pending_mention_sources`.
  - A table `tg_resolutions` (time, command, kind, outcome; no name).
  - The exporter's `SELECT (requested_at, command)` on it.
  - Nothing to neutralise on a restore: the journal names nothing and acts on nothing.
- **Role:** no new workload, CronJob, secret or connection. `itgraph rising` runs through `scripts/on-host.sh` without `--session`.
- **Docs:**
  - `src/itgraph/README.md`: the ranking, the command, the loop's resolution, the journal.
  - `src/itgraph/CLAUDE.md`: the module map.
  - `deploy/runbook.md`: the series and the alert.
  - `docs/PLAN.md`: "Expanding the seed set". The rule that only `itgraph resolve` spends `contacts.resolveUsername` changes here, in the README and in PLAN.
- **Infrastructure repository:** an alert on `itgraph_telegram_resolutions_24h{command="watch"}` above `resolution_cap`, and a panel.
- **Release:** a release of its own, test environment first. The loop's resolution cannot be tried there, since nothing collects. Its first real run is production's first rising candidate awaiting an identity.

## Decision record

Brought in the explore session of 2026-11-16. The operator's answers then, quoted in `walk-new-seeds`' proposal, set the flow: Telegram's candidates by references from seeds; option A, a hard cap on resolution in the loop; and at most five a day at 11:00 for the bot to send. On the threshold the operator accepted the recommendation:

> Рекомендация по п.4 подходит.

That is: at least two distinct seed families in the trailing 14 days, the candidate's own family left out; ranked by families in the window, then by fewer references before it; the cap 10 a day.

On 2026-11-17 the operator chose the recommended answer to each of four questions:
- this change is the ranking, the loop's resolution and a command-line report, and the bot is the next change;
- the cap counts every resolution by any command in the last 24 hours;
- supergroups and channels without a username are left out of the list;
- a release of its own.

Confirmed:

1. Rising means at least two seed families in the trailing 14 days, the candidate's own left out, ranked by families in the window, then by fewer before it.
2. The poll loop resolves the head of the ranking, at most 10 resolutions in any 24 hours across every command.
3. This change ends at `itgraph rising`; the bot is change 3.
4. Supergroups and channels without a public username are left out and counted.
5. A release of its own.

**Not covered by the requirements.** These were chosen here, not by the operator:

- The cap is a rolling 24 hours, not a calendar day (design.md, Decision 4).
- The loop resolves at most one candidate every 20 minutes, after the polls and the walk (design.md, Decision 5).
- The loop sleeps off no rate limit on resolution. A wait holds resolution until it ends, read from `tg_flood_events` (design.md, Decision 6).
- The loop's resolutions and their rate limits are recorded under `watch`, the command that made them (design.md, Decision 4).
- A pending username is never printed by `itgraph rising`, only counted (design.md, Decision 7).
- A failed resolution is not retried by the loop (design.md, Decision 5).
- The journal keeps no name (design.md, Decision 4).
- The window and threshold are settings: `rising_window_days`, `rising_min_families` (design.md, Decision 2).
