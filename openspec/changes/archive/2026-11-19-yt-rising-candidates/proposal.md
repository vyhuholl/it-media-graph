# Proposal

## Why

The fourth of the changes that bring rising channels into the inventory (the explore of 2026-11-16; changes 1–3 shipped as `tg-rising-candidates` and `tg-rising-review`). Telegram now finds its rising channels and walks them into the graph; YouTube does not. What exists are three manual queues — `yt tg-candidates`, `yt mention-candidates`, `yt featured-candidates` — that rank channels only when the operator runs them, and whose rankings count seeds over all history, not families over a window: they answer "who is cited at all", never "who started being cited this fortnight". The last review round drained them completely, so from now on nothing will surface a channel the seeds started citing, featuring or charting next week.

Measured on the production database on 2026-11-18 (aggregates only):

- **The queues are empty:** 887 seeds, 241 rejected, no candidate and no maybe; 865 channels featured by seeds, all of them already in the inventory, zero outside it. The rising flow starts from a clean slate — its threshold shapes future noise, not a backlog.
- **The general pool is idle:** ~5 units a day, peaking at ~1,700 on refresh and backfill days, against a cap near 6,000. The chart (up to eight units a day, 200 videos a region), weekly featured readings (~130 a day) and bounded daily lookups (~20 a day) all fit with room to spare; the watch loop's reserve is untouched.
- **Language almost never comes from the API:** 56 of 1,127 stored channel payloads carry `defaultLanguage` (31 `en`, 20 `ru`). Language is a review decision, not a detected fact.
- **Families exist on YouTube now:** 57 confirmed multi-channel families (`yt_channel_families`), so "families, not seeds" — the unit Telegram's rising ranking already uses — is available here too.
- **The chart answers.** Probed from the collection host on 2026-11-18, with the production key: both `US` and `RU` returned 200 videos of category 28, fifty to a page. The Trending page left YouTube's interface in July 2025; the endpoint outlived it. The probe also settled an operational fact: the key is restricted to the host's IPv4, so requests over IPv6 are refused — harmless here, for the reading runs in the cluster, which walks IPv4.

## What Changes

- **Two rankings, worked out when asked and never stored.**
  - **The link ranking:** a YouTube channel absent from the inventory, referred to within the trailing `rising_window_days` (14) by at least `rising_min_families` (2) distinct families — a Telegram seed's post linking it (the family of the TG seed), a YouTube seed's video referencing it in title or description (the family of the YT seed), or a YouTube seed newly featuring it in its channel sections (that seed's family). One family per referrer whatever it does, families on either platform counting as families. The order: families in the window, then fewer families before it (the newer first), then by channel id.
  - **The chart ranking:** the channels of the day's `chart=mostPopular` reading, category 28, region US and region RU, absent from the inventory, in chart order. Every chart channel is a candidate — no "took off" filter, per the operator's decision.
- **A daily chart reading, stored.** The refresh pass reads the chart once a day for each configured region, asking for each video's id and its channel's id and nothing else, and stores the answer as a raw reading — append-only, kept like a statistics reading, never removed by age.
- **Featured readings become a history.** `yt_raw_channel_sections` stops being overwritten: every fetch appends a reading, and what a seed features is read from its newest reading. An appearance — a channel in the newest reading that the previous one lacked — is derived on reading, never stored. Sections gain their own freshness setting, `youtube_sections_after_days` (7): due for a re-read that often, while a list still counts as evidence for `youtube_answers_fresh_days` (30) as today.
- **The lookups run unattended, bounded.** A new command, `itgraph yt rising-lookups`, runs both lookup passes — `tg-lookup`'s and `mention-lookup`'s — most evidence first as they order themselves, each stopped after `youtube_tg_lookup_daily_requests` / `youtube_mention_lookup_daily_requests` requests (10 each by default), booked from the general pool. A CronJob runs it daily. The manual lookup commands are unchanged, and the three candidates reports stay manual-only: their output names channels, a job's logs are not the place for it.
- **`itgraph yt rising`** prints both rankings — each ranked channel's id and its evidence, how many references await a lookup or were not found, how many seeds' featured lists are out of date. Ids and counts only, no title or handle, no request, nothing written.
- **Series** for the shared monitoring: the newest chart reading per region, and the lookup job's last run.

**Out of scope:**

- The daily selection, the bot's cards, the review buttons and how a decision reaches the inventory: `yt-rising-review`.
- Discovery through `search.list`: deliberately deferred — queries need curation the operator has no time for now.
- A "took off" filter on chart channels: the operator chose to take them all.
- Changing the manual queues: they keep counting seeds over all history, as their specs say. The rising rankings are separate and count families over a window.
- Retrying lookups that failed, and re-reading a chart the API refused: as elsewhere, the next scheduled run is the retry.

## Capabilities

### New Capabilities

- `youtube-rising-candidates`: which YouTube channels are rising and how the two rankings are worked out — the link ranking's evidence, families, window and threshold; the chart ranking; what is left out and counted; the stored chart reading; the append-only featured readings and their own freshness; the bounded unattended lookups; the report.

### Modified Capabilities

- `youtube-api-access`: a new request — the chart reading — with its declared fields (each video's id and its channel's id, and the token for the next page), booked from the general pool by the refresh.
- `youtube-featured-channels`: featured readings are appended, not replaced; a reading per fetch; `youtube_sections_after_days` governs when a seed's sections are read again; an appearance is what the newest reading lists that the previous one lacked.
- `youtube-data-retention`: featured readings and chart readings are readings in the "kept, never removed by age" sense — the sections table stops being a latest-payload table.
- `youtube-telegram-links`: `tg-lookup`'s "not scheduled" protection is lifted for the bounded daily run; `tg-candidates` stays manual.
- `youtube-lookups`: the scheduled `rising-lookups` command, its per-pass daily request limits, and that it changes nothing about the manual commands.
- `collection-monitoring`: series for the chart reading and the lookup job.

## Impact

- **Code:**
  - `yt/chart.py` (the chart request), `db/yt_chart.py` (the readings), `derive/yt_rising.py` (both rankings; pure), `db/yt_rising.py` (the rows they read).
  - `yt/refresh.py` (the chart step), `yt/lookup_pass.py` / new `yt/rising_lookups.py` (the scheduled pass), `db/yt_sections.py` and `yt/sections.py` (append readings).
  - `metrics/series.py`, `config.py`, `cli.py` (`yt rising`, `yt rising-lookups`).
  - No new dependency.
- **Schema:** one Alembic revision.
  - A table for the chart readings: region, day, position, video id, channel id, fetched at.
  - `yt_raw_channel_sections` to a composite key `(channel_id, fetched_at)`; the rows present become each seed's first reading; nothing else moves.
  - The exporter's grants on the new table's moments.
  - Nothing to neutralise on a restore: readings name channels by public id only and act on nothing.
- **Role:** one new CronJob (`itgraph-yt-lookup`, daily, YouTube env — scheduled only where a key is configured, as the refresh is); the refresh CronJob gains the chart step, its manifest otherwise unchanged.
- **Docs:** `src/itgraph/README.md` (the rankings, the readings, the report), `src/itgraph/CLAUDE.md`, `deploy/runbook.md` (the series), `docs/PLAN.md` ("Expanding the seed set").
- **Infrastructure repository:** alerts on a chart reading older than a day and a lookup run older than a day, and a panel.
- **Release:** a release of its own, test environment first. The test environment, having no YouTube key, runs no chart and no lookups — the rankings and the report are tried there on a restored copy, as the TG rankings were.

## Decision record

Brought from the explore of 2026-11-16 and the operator's answers of 2026-11-18: three sources — the regional chart of category 28, new featured channels, fresh links from Telegram and YouTube posts; the chart read once a day; no "took off" filter, consumer-tech noise accepted; two rankings — a link ranking by families and a chart ranking as a plain list; the lookups embedded in a scheduled job under a limit. The probe, run from the collection host on 2026-11-18 with the production key, answered: both regions, 200 videos of category 28 each.

On 2026-11-18 the operator chose the recommended answer to each of three questions:

1. The link ranking's threshold is **at least two families in the window** — one knob, `rising_min_families`, shared with Telegram. A channel featured by a single seed is not rising: the manual `yt featured-candidates` queue, minimum one seed over all history, keeps catching those.
2. Sections are read every **seven days** of their own setting — ~130 units a day from the idle general pool, so an appearance is dated within a week and fits the fortnight's window.
3. The chart reading stores **ids only** — each video's id and its channel's. No new declared field, the privacy page unchanged; the card links by id.

Confirmed:

1. Rising, for links, means at least two distinct families — Telegram families for Telegram posts, YouTube families for references, features and appearances — within the trailing fourteen days, ranked by families in the window, then by fewer before it, then by id.
2. The chart contributes every channel of the day's reading that the inventory does not hold, in chart order, split by region.
3. Featured readings are append-only from this change on; appearances are derived, dated by the reading that first lists them.
4. The lookups run daily, bounded by settings, from the general pool; the candidates reports stay manual.
5. A release of its own.

**Not covered by the requirements.** These were chosen here, not by the operator:

- The chart is read by the refresh pass, not a job of its own (design.md).
- The scheduled lookups run through a new `rising-lookups` command; the manual ones keep their behaviour (design.md).
- Evidence is dated by its content — a post's or a video's publication time; a featured appearance by its reading. An old video's description naming a channel is old evidence, whenever it was parsed (design.md).
- The link ranking ranks only channels absent from the inventory; a candidate or a maybe already in it is not ranked, unlike Telegram's — the manual review-export round owns those (design.md).
- A family is per platform: a Telegram family and a YouTube family are two families, and an unaffiliated channel is a family of its own (design.md).
