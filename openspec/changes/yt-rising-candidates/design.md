# Design

## Context

See proposal.md for why. What shapes the approach:

- **Three evidence readers already exist; none is dated by families.** `derive/tg_youtube.py` reads Telegram's links and counts TG families; `derive/yt_references.py` + `derive/yt_mention_candidates.py` read the mention graph and count seeds; `derive/yt_featured.py` reads featured lists and counts seeds. The rising link ranking composes the three under one window and one unit — families on both platforms — which no existing queue does.
- **`yt_raw_channel_sections` is overwritten in place.** `store_sections` upserts on `channel_id`. There is no past to diff against, and a derived table of appearances could not be rebuilt once the raw row is replaced — so the history has to be raw itself, like a statistics reading.
- **The lookups are the ranking's gate.** A reference that awaits a lookup names no channel, and the two lookup commands are run by hand today. Telegram's answer was the loop resolving the ranking's head; YouTube's pools make that shape wrong (the watch loop books its own share and may not spend from the general pool), so the unattended lookups become a job of their own.
- **The general pool is idle** (~5 units a day, ~1,700 on refresh and backfill days, against a cap near 6,000), so the chart's 8, the sections' ~130 and the lookups' ~20 a day cost nothing that anything else wanted.
- **The chart answers.** Probed from the collection host on 2026-11-18, with the production key: both configured regions returned 200 videos of category 28, fifty to a page — four requests a region a day. The Trending page left the interface in July 2025; the endpoint outlived it. The key is restricted to the host's IPv4 and refuses IPv6; the reading runs in the cluster, which walks IPv4, so nothing follows from that.

## Goals / Non-Goals

**Goals:**
- Two rankings reproducible from what is stored, worked out when asked, never stored.
- Featured evidence that can say *when* a channel appeared, from a raw history that survives rebuilds.
- Unattended lookups that keep the link ranking's evidence complete, under a bounded daily spend.
- No new privilege, no new declared field, nothing named that a person could be named by.

**Non-Goals:**
- The daily selection, the cards and the review buttons: `yt-rising-review`.
- Discovery through `search.list`, a "took off" filter on the chart, retrying a refused chart reading.
- Changing the manual queues: they keep counting seeds over all history.

## Decisions

### 1. The rankings are pure modules over one snapshot

`derive/yt_rising.py` takes plain rows and a moment and returns the two rankings and the counts; `db/yt_rising.py` loads the rows in one `REPEATABLE READ` transaction, as `db/rising.py` does for Telegram:

- the seeds of both platforms with their families (`tg_channel_families`, `yt_channel_families`, or the channel's own id), and the inventory's channel ids;
- the links of Telegram's seeds with their posts' times, as `tg_youtube` reads them, and the references of YouTube seeds' videos with their publication times, as the mention queue reads them — each resolved to a channel through the stored payloads and lookups exactly as those queues resolve them;
- every seed's two newest featured readings;
- the day's chart readings.

A family's key is the platform's family key, prefixed by the platform, so a Telegram family and a YouTube family never collide and an unaffiliated channel is its own family. What is rising, how it ranks and what is left out are the spec's requirements, and the module is their only implementation: `itgraph yt rising` and the selection of `yt-rising-review` both call it.

The mention side re-parses the seeds' stored descriptions, as `yt mention-candidates` does — seconds of work at 887 seeds, paid by whoever asks, never by `itgraph derive`.

### 2. Featured readings append; the migration keeps what is there

`yt_raw_channel_sections` gains `fetched_at` in its primary key and `store_sections` becomes a plain insert. `featured_lists` reads each seed's newest in-date reading; `sections_due` compares against the newest of any age. Nothing is deleted, and a rebuild of anything derived from the readings can replay the whole history.

The revision moves the existing rows as they are: each becomes its seed's first reading, establishing the baseline, so nothing is an appearance until the second reading after the release — about `youtube_sections_after_days` later. The downgrade recreates the single-row key, keeping each seed's newest reading.

An appearance is read from the two newest readings of a seed, whatever the previous one's age: a channel in the newest that the previous lacked, dated by the newest reading's fetch. The window and the queue rules live in `youtube-rising-candidates`; `youtube-featured-channels` keeps owning how lists are read.

### 3. The chart is read by the refresh, first, and isolated from it

The chart reading is at most eight units a day and belongs to no channel, so it runs at the head of `itgraph yt refresh`, before the channels: booked from the general pool, paged while the API offers a token — four requests a region at fifty a page, 200 videos a region, as probed — trimmed by `fields` to each video's id, its channel's id and the page token.

`yt_raw_charts` holds one row per region and fetch: the region, the moment, and the answer as YouTube sent it, trimmed — a list of `(video id, channel id)` in chart order, the position being the order. Nothing else. It is never overwritten and never removed.

A region whose chart the API refuses or that fails is logged, skipped, and the refresh goes on: one dead endpoint must not cost the payloads their daily refresh. The series shows the reading's age, and the alert on it is the operator's signal — and should the endpoint ever die for good, emptying `youtube_chart_regions` switches the source off without touching anything else.

### 4. `rising-lookups` chains the two passes under settings

A new command runs `tg-lookup`'s pass, then `mention-lookup`'s, each through the code its own command runs — most evidence first, one answer committed before the next request — stopping each after `youtube_tg_lookup_daily_requests` / `youtube_mention_lookup_daily_requests` requests (10 each by default: ~0.3% of the daily quota, and video batches answer fifty a request). The manual commands keep their `--limit` and their behaviour; the scheduled one is separate so a job's bounds are settings, not manifest literals.

Each run is a row of `yt_lookup_runs` — started, ended, counts, outcome — the pattern of `yt_refresh_runs`, read by the series, an empty run included so quiet and broken stay distinguishable. The CronJob `itgraph-yt-lookup` runs daily at 06:00 Moscow (`itgraph_yt_lookup_schedule`), after the refresh has done its work, wherever an API key is configured.

### 5. Settings

| Setting | Default | What it bounds |
|---|---|---|
| `youtube_sections_after_days` | 7 | when a seed's sections are read again (~130 units a day) |
| `youtube_chart_regions` | `US`, `RU` | which charts are read (4 units a region a day at 200 videos) |
| `youtube_chart_category` | 28 | the chart's video category |
| `youtube_tg_lookup_daily_requests` | 10 | the scheduled Telegram-links lookup pass |
| `youtube_mention_lookup_daily_requests` | 10 | the scheduled description-references pass |

`rising_window_days` and `rising_min_families` are Telegram's settings, reused: one window and one threshold for both platforms, as the operator chose.

### 6. `itgraph yt rising` names ids, and nothing else

The report prints the link ranking's head — each channel's id, its families in the window and before it, its first reference in the window — then the chart ranking's rows with region and position, then the counts: references awaiting a lookup, not found, seeds whose featured list is out of date. No title, no handle: the sibling YouTube reports show ids only, and the operator reviews what a channel *is* in the bot's cards, which read the payload at sending. The command opens no client, needs no key, and runs beside everything.

### 7. Roles, restore, monitoring

- **The exporter** gains `SELECT (region, fetched_at)` on `yt_raw_charts` and `SELECT (started_at, finished_at)` on `yt_lookup_runs`, and nothing else of either; role tests hold it.
- **The reader** reads the new tables through its default privileges.
- **The restore** gains nothing to neutralise: readings and run records name channels by public id and act on nothing. `files/readback.sql` gains no check for them, and a restored copy's chart history is history like any other.
- **Series:** `itgraph_youtube_chart_read_timestamp_seconds{region}` (the newest reading's fetch, 0 when none) and `itgraph_youtube_rising_lookups_timestamp_seconds` (the newest run's end, 0 when none). The infrastructure repository is asked for an alert on each older than 25 hours, and a panel.

## Risks / Trade-offs

- **[The chart may die later]** → it outlived the interface's Trending page when probed on 2026-11-18, but if it ever goes the way of the page, the reading is isolated in the refresh, the series shows its age, and emptying `youtube_chart_regions` switches the source off; the link ranking never depended on it.
- **[A channel appears up to seven days late]** → the reading cadence quantizes appearances, which the operator accepted against the cost of reading daily. The window is a fortnight; an appearance seen at day seven still has seven days of window left.
- **[The first week after release has no featured evidence]** → the migration's baseline is the rows already stored; the second reading, a week in, is the first that can show an appearance. Telegram's ranking covers the interim.
- **[The sections step of the refresh may not be running]** → production shows 193 seeds marked after 2026-11-11 with no featured reading, though the daily refresh should read an absent list. If the deploy's tests confirm a bug, it is fixed as its own work before this change relies on the step.
- **[Twenty unattended requests a day]** → bounded by settings, booked from a pool nothing else wants, ordered most evidence first, and each answer committed before the next request; the journal already accounts them per command.
- **[Description parsing on every ranking]** → the cost the manual mention queue already pays, on demand; `itgraph derive` gains nothing, and the selection pass that will run daily is change B's to keep bounded.

## Migration Plan

1. Release, then deploy to the test environment. The revision changes the sections key, adds `yt_raw_charts` and `yt_lookup_runs` and the grants. No key there: no chart, no lookups — the rankings and `scripts/on-host.sh yt rising` run on the restored copy, and the sections key change is exercised by the role's tests.
2. Deploy to production. The next 05:00 refresh reads both charts (or the probe's verdict removes them); the 06:00 lookup run spends its first bounded requests; the sections settle into their weekly cadence, and the first appearances show a week in.
3. Ask the infrastructure repository for the two alerts and a panel.

Rollback: revert the pin. The revision's downgrade restores the single-row sections key, keeping each seed's newest reading, and drops the two tables. The chart readings and lookup answers already stored are raw history; dropping them loses nothing the rankings promise to rebuild.
