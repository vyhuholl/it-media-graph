## Why

Change 3 stored a year of every seed's uploads — 7,660 videos for 95 of the 105 seeds, walked by hand on 2026-11-01 — and nothing reads them yet. The site already states what they are read for: "to see which other channels they mention or link to — to map how the channels refer to one another, and to find channels to review" (`docs/index.html`). This change is that reader. It also turns on `youtube_backfill_enabled`, which keeps the year fresh. Without it, the manual walk expires on 2026-12-01: the refresh at 05:00 Moscow that day is the last one before it.

The base rules shape it. Decided on 2026-11-01: an edge or a pending mention may exist before acceptance, and lives no longer than the payload it was parsed from. A handle read out of a description is API data, and API data lives only in `yt_raw_*`. So the references are worked out from the current payloads whenever they are asked for, and never stored.

## What Changes

- A pure parser of a video's title and description into the channels and videos it references:
  - **channel links:** `youtube.com/@handle`, `youtube.com/channel/UC…`, `youtube.com/user/NAME`, on `www.`, `m.` and `music.` hosts, scheme optional, any trailing path or query;
  - **video links:** `watch?v=`, `youtu.be/`, `/shorts/`, `/live/`, `/embed/` and `/v/` — the last two on `youtube-nocookie.com` too — with `list=`, `t=` and `si=` ignored;
  - **bare `@handle` mentions,** taken only when they name a handle the inventory already holds;
  - **counted, never followed:** `/c/NAME` and `youtube.com/NAME`, which the API cannot look up; playlist pages; link shorteners; `t.me` links.
- Reading the references on demand, with no network:
  - **sources:** seed channels' stored videos, the poll loop's and the walk's alike;
  - **known targets:** a referenced channel in the inventory, or a video whose payload is stored, becomes an edge;
  - **pending targets:** an unknown handle link, legacy name, channel id or video id becomes a pending target, with the set of seeds that name it;
  - self-references are dropped.
- `itgraph yt mentions`: a report for the operator. It shows what the seeds reference, which seeds do, and how many distinct seeds name each target, most first; then the pending targets in the same order; then what was counted and not followed. It makes no request and writes nothing, and no scheduled job runs it.
- No inventory record is created. Any record created from a reference is change 5's.
- The walk switched on in the deploy: `youtube_backfill_enabled: true`. The daily CronJob then walks unwalked seeds, and `yt refresh` keeps the year fresh.
- Documentation:
  - `docs/index.html` and `docs/privacy.html`: mentions read from new uploads as well as past ones, and the date;
  - `docs/PLAN.md`:
    - what `brandPartner` actually is;
    - what the derived-metrics amendment does and does not cover;
    - the measured shape of references;
    - observations on shorteners and `t.me`;
    - change 4's row;
  - `src/itgraph/README.md`, `src/itgraph/CLAUDE.md`, and one clause in the root `CLAUDE.md`.

Out of scope:
- resolving pending targets, featured channels and search (change 5);
- alerts (6);
- scoring, PageRank, clustering and any other measure over the graph (see design: the amendment does not cover them);
- channel descriptions as a source;
- bare `@` mentions of handles outside the inventory;
- expanding shortened links;
- `brandPartner`;
- cross-platform links;
- affiliation between YouTube channels;
- comments.

## Capabilities

### New Capabilities

- `youtube-mention-graph`: what a YouTube video is taken to reference — the forms recognized, the handle grammar, which forms are counted and not followed — and from whose videos. Also how references become edges or pending targets without the network, how they live and die with the payloads they are read from, and what the operator's report shows.

### Modified Capabilities

None. `youtube-data-retention`'s "API Data Has One Home" already forbids keeping a handle outside its payload, and nothing here is stored. Turning the walk on is a deploy setting: `youtube-backfill` already says what happens when it is on.

## Impact

- **Code:**
  - new `derive/yt_references.py` (pure) and `db/yt_references.py` (loading);
  - one command in the `yt` sub-app;
  - no setting.
- **Schema:** none. No migration, no enum value, nothing new for `db/backup.py` to exclude.
- **Deploy:**
  - `youtube_backfill_enabled: true` in `deploy/group_vars/all/main.yml`;
  - `tests/test_deploy_manifests.py`'s check that the walk is off becomes a check that it is on.
  - The first refresh of the walked year, on 2026-11-27, costs about 154 units at once, then 6–12 a day.
- **Quota:** none spent by this change. What it hands to change 5, measured on 2026-11-01: 93 unknown channel targets (84 handle links, 9 channel ids) at a unit per handle and 50 ids a unit; 1,047 unknown videos at 50 a unit, about 21 units; and the legacy names.
- **External:** no new method, no new field. The use case is the one the site states. No allocated quota is used, so no notice is due under III.D.3, as decided for change 3. The site's two pages are updated before archiving.
