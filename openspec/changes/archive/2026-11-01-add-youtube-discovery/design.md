## Context

Fifth YouTube change. See proposal.md for why. It inherits the conventions of changes 1–4a unchanged:
- API data only in `yt_raw_*`, and whatever is worked out from it computed on reading and never stored;
- expiry first in every `yt` command, through `_run_yt`;
- ids only in logs;
- every networked run by the operator through `scripts/on-host.sh`;
- a networked command that stores, and an offline command that reports and adds — the 4a shape.

**Reused:**
- `yt/lookups.py` and `db/yt_lookups.py`, built in 4a to take names from any source;
- `derive/yt_references.py` and `db/yt_references.py`, the mention graph, read once per command;
- `db/yt_channels.create_channels(…, discovered_via=…)`;
- the ledger in `db/yt_quota.py`, the client in `yt/client.py`, and `request_urls_unlogged`.

**The application.** Its text is not kept. What it promised is in `docs/PLAN.md` ("Filed on 2026-10-25"), `docs/index.html` and `docs/privacy.html`; those are the requirements. No answer yet.

### Facts checked against the documentation, 2026-11-01

| Fact | Source |
|---|---|
| `channelSections.list`: 1 unit. Exactly one filter: `channelId`, `id` (section ids) or `mine`, so one call per channel and no batching. Parts `contentDetails`, `id`, `snippet`; `hl` deprecated | …/v3/docs/channelSections/list (updated 2026-10-15) |
| `snippet.type` lists ten values, `multipleChannels` among them, none marked deprecated. `contentDetails.channels[]`: "A list of one or more channel IDs that are featured in a channel section" | …/v3/docs/channelSections (2026-10-15) |
| 2021-05-12: `brandingSettings.channel.featuredChannelsTitle` and `featuredChannelsUrls[]` deprecated, "already unsupported in the YouTube Studio UI and on YouTube"; for sections, `snippet.style`, `localizations` and `targeting`. Nothing about `multipleChannels` | …/v3/revision_history |
| `search.list`: "1 unit in the Search Queries quota bucket", 100 calls a day. `maxResults` 0–50, default 5. `type` default `video,channel,playlist`. `order` default `relevance` | …/v3/docs/search/list (2026-10-15); …/determine_quota_cost |
| `regionCode`: results "viewable in the specified country", not the channel's country. `relevanceLanguage` ranks rather than filters. `videoDuration` and `eventType` need `type=video`. `relatedToVideoId` removed 2023-08-07 | …/v3/docs/search/list |
| A search result's `snippet.channelId` is "the channel that published the resource", the author for a video; `id.channelId` for a channel result. `snippet` is the only part and carries title, description, thumbnails and channel title | …/v3/docs/search |
| 2026-06-01: `search.list` and `videos.insert` charged to buckets of their own. How exhaustion of a bucket is reported is not documented | …/revision_history; …/determine_quota_cost |
| `fields` must be URL-encoded. What an unknown field selection returns is not documented | …/v3/getting-started |
| Developer Policies updated 2026-10-15, the date checked for 4a, so the verbatim III.D.3, III.E.2, III.E.4.d and III.E.4.h in 4a's design still hold. III.E.6: "must not … scrape YouTube Applications or Google Applications". III.I.14: "use any technology other than YouTube API Services to access or retrieve API Data" | …/terms/developer-policies |
| Derived-metrics page updated 2026-09-14, unchanged: "You may rank channel performance or track views between different channels". Nothing on finding or recommending channels, nothing on III.E.2 | …/terms/derived-metrics-policy |

### The probe, 2026-11-01

`channelSections.list` with `part=snippet,contentDetails` on one seed whose page shows a featured shelf. The first attempt was refused `400 invalidParameter`: the probe script selected `error(...)` in `fields`, which is no property of the list response. One unit, not booked. The corrected probe cost one more.

- 8 sections. Types came back lowercase: `multiplechannels`, `recentuploads`. The reference spells them `multipleChannels`, `recentUploads`.
- 5 of the 8 had type `channelsectiontypeundefined`, which the reference does not list.
- The `multiplechannels` section listed 2 channel ids. The operator confirmed that the shelf on the page shows two.

The reference and the API disagree on the type values. **Decided by the operator on 2026-11-01:** the type is not read at all. Any section's `contentDetails.channels` is the featured list, which is what the reference says that field holds.

### Measured, 2026-11-01

Read-only aggregates, run by the operator. No title, description, handle, link or channel id left the database. Telegram-side counts are not recorded, as in 4a.

| Figure | Value |
|---|---|
| Inventory | 214 records; seeds 175 — 123 `en`, 52 `ru`; 39 rejected |
| Review of 4a's hundred | 70 seeds, 30 rejected (`not_it` 18, `adjacent` 7, `consumer_tech` 5). English: 22 seeds of 43 candidates. Russian: 48 of 57 |
| Stored videos of seeds | 22,381 (walk 20,388, first listings 1,332, moved count 661); 165 of 175 seeds have any; `yt_raw_videos` 30 MB |
| Upload rate, 28 days | 0.40 a channel a day — `en` 0.33, `ru` 0.58; 47 seeds published nothing |
| Walks | 175 walked, 582 pages, median 50 videos, at most 759, 22 items out of order. The 105 original seeds cost 477 units (4.5 a seed); the 70 from Telegram cost 635 (9.1 a seed) |
| Poll loop at 105 seeds | 490–545 units a day: `channels.list` 286–288, listings 108–156, readings 88–102 |
| 4a's first lookup run | Every video batch of fifty was answered in one call: calls equal ⌈videos/50⌉, and the dead ids were simply absent. The 404 fallback never ran |
| `yt mentions` | 22,381 videos of 165 seeds: 171 references to 37 known channels (22 named by one seed, 10 by two, 5 by three or more) |
| Pending targets | 1,345: 1,188 videos (5 named by two seeds), 139 handles (3 by two), 13 ids, 5 legacy names; none by three. One of the eight targets named by two seeds is named by two channels of one author |
| Not followed | 467 custom URLs, 796 playlist pages, 5,264 shortened links, 3,996 Telegram links, 2,617 mentions outside the inventory |
| Bare `@`, regex approximation of the parser | 2,740 in all, 646 distinct, 77 own; 46 name another inventory channel (18 distinct); 9 of the 615 distinct outside are handles a lookup found |
| `youtube.com/@` links, same approximation | 1,385, 470 own; 92 to another inventory channel (10 distinct); 823 outside (140 distinct) |
| Shortened links | 5,264 in 2,392 of 20,604 descriptions (11.6%): `bit.ly` 1,903, `amzn.to` 1,613, `goo.gle` 833, `ow.ly` 331, `clck.ru` 328, `jb.gg` 90. The SQL count equals the parser's |
| Telegram links in descriptions | 3,996 from 42 seeds (at 95 seeds: 251 from 4) |

Two figures disagree with earlier records:
- **Bare `@`.** Counting own channels, bare `@` now names the inventory in 4.5% of cases against 41% for handle links (0.6% and 28% at 95 seeds). The closed set grows as intended: 8 hits became 46.
- **`bit.ly`.** It is in 4.8% of descriptions, not change 4's 15%. That figure came from a looser pattern than the parser's; no decision rests on it.

## Goals / Non-Goals

**Goals:**
- Four review queues — mentions, featured lists, search, Telegram — each ordered by one number or by none, each adding records under its own discovery source, so that the yield of every source is measured from `yt_channels` alone.
- Every new piece of API data in `yt_raw_*`, gone within 30 days, out of every dump.
- A search bucket that cannot stop the poll loop.
- A round that one person can run in an evening from the operator's machine.

**Non-Goals:**
- One rating across sources. Rejected in the explore; see "Policy readings".
- Feeding lookups into `yt mentions`: the graph stays inside the inventory, as 4a left it.
- Reading section types, titles, playlists or positions.
- A Russian-language search, a `--lang` option, `regionCode`, `videoDuration`.
- Automatic filtering of candidates by anything YouTube reports, before a person reviews them.
- YouTube families.

## Decisions

### Separate queues, one number each

Decided by the operator on 2026-11-01. "Policy readings" below gives the reasons.

| Queue | Evidence | Default bound | Order | Source |
|---|---|---|---|---|
| `yt mention-candidates` | distinct seeds whose videos reference the channel, any form, after lookups | ≥ 2 seeds | seeds, then id | `mention` |
| `yt featured-candidates` | distinct seeds whose featured lists name it | ≥ 1 seed | seeds, then id | `featured` |
| `yt search-candidates` | none | results in date | round-robin across runs, YouTube's position | `search` |
| `yt tg-candidates` (4a) | distinct Telegram families | ≥ 2 families | families, newest link, id | `telegram` |

- **Why separate queues:**
  - the thresholds differ: featuring a channel is deliberate, so one seed is enough there, while one link in a description is weak;
  - a separate source per queue makes `discovered_via × status` the yield table that steers the rounds.
- **The cost:** a channel linked by one seed and featured by another has one seed in each queue, not two. It still enters through the featured queue, whose threshold is one.
- **One record per channel.** Every queue skips any channel with a record, whatever its status, so a channel enters through the first `--add` that reaches it. Rejected channels never come back.
- **Rejected:**
  - a sum or weighted score across sources — a new metric partly over API data, mixing families with seeds;
  - one list sorted by source — the same queues in one report, with a less readable cut.

### Mentions: resolve everything, cut afterwards

```
read_references (one REPEATABLE READ: seeds' videos + featured lists, known sets)
   │  video-level references only — featured is not evidence here
   ▼
pending handle | legacy | video  ── lookups in date (yt_raw_lookups, shared with 4a) ──► channel | not found | awaiting
pending id ─────────────────────────────────────────────────────────────────────────► channel
edge to a known target ─────────────────────────────────────────────────────────────► channel
   │  drop: named channel == the referencing video's channel
   │  drop: channel with any yt_channels record
   ▼
distinct seeds per channel ──► rank (seeds desc, id) ──► --min-seeds, --top ──► report | --add
```

- **Lookups cover everything.** The cost is small: 139 handles and 5 legacy names at a unit each, and 1,188 videos at fifty a unit — about 170 units once, then whatever is still unresolved when lookups expire.
- **The cut comes after lookups.** Forms merge only once resolved: a handle from one seed and a video from another become one channel with two seeds, as in 4a ("Two forms, one channel").
- **References to their own channel are dropped after resolution.** Before a lookup, a link to the seed's own video from more than a year back looks foreign. 97% of links to stored videos were to the channel's own (change 4), so most of the 1,188 are expected to resolve to the seed itself.
- **`yt mention-lookup`** has `tg-lookup`'s shape:
  - names awaiting a lookup, most distinct seeds first, then kind and name;
  - videos fifty to a call in that order;
  - every answer committed;
  - `YtCommand.MENTION_LOOKUP`, general pool, no floor, `--limit`; httpx's URLs kept out of the log.
  - The asking itself — order, batching, committing, the report — is moved out of `yt/tg_lookup.py` into `yt/lookup_pass.py`, which both commands call with their own ordered names. Found necessary in implementation: the two passes were the same code but for where the names come from.
- **`--add` refuses** while any reference awaits a lookup, for 4a's reason: a partial lookup biases the cut toward ids and stored videos.
- **No tie-break beyond the channel id.** The newest referencing video's date would be a second number per target, derived from API data, which `youtube-mention-graph` rules out. Rounds take the queue whole at two seeds and then at one, so order within a count matters little.
- **The report shows no API data:** channel id, seed count, seed ids. No handle, unlike `yt mentions`.

### Featured lists: read with the refresh, type not read

- **Request:**
  - `channelSections.list?channelId=…&part=contentDetails&fields=items(contentDetails/channels)`, one unit per seed;
  - `YtCommand.REFRESH`, so the general pool;
  - after the channel payloads — so a seed found gone is not asked about — and before past uploads.
- **Due:** seeds not marked gone whose list is absent or older than the refresh age (25 days).
  - At the first deploy: 175 units.
  - Then about 7 a day at 175 seeds and 20 at 500.
  - A seed added in a round is read by the next daily refresh.
- **Stored:** `yt_raw_channel_sections`, the trimmed response as returned.
  - A seed with no sections, or answered 404, is stored with an empty `items`, so it is not asked again before the refresh age. So is one answered `403 channelSectionForbidden` ("The requester is not allowed to access the requested channel sections"), found necessary in implementation: that refusal is about one channel, and as an error it would stop the whole refresh. Every other refusal still stops it.
  - A list is never deleted when its channel stops being a seed. Readers take seeds only, and expiry removes it at 30 days.
- **In the mention graph:**
  - each id in a seed's list is a channel-level reference of a new form, `featured`, with no referencing video;
  - an edge if the channel is known, a pending id otherwise;
  - `Edge.video_id` becomes optional;
  - the report's first line counts featured lists read beside videos.
- **The queue:**
  - distinct seeds per featured channel outside the inventory, the seed's own id dropped;
  - `--min-seeds 1`, `--top 100`;
  - `--add` refuses while a seed not marked gone has no list in date, pointing to `yt refresh`.
- **Rejected:**
  - a separate networked `yt featured` command — one more thing to run per round, for data the daily job reads anyway;
  - reading the type, case-insensitively — it rests on spelling the reference contradicts, for no gain, since only the shelf type lists channels.

### Search

```
operator's queries (args | --from-file - )            yt_search_queries   operator's; inventory dump
   │  skip: (query, type) with a run in date, unless --force
   ▼
search.list  part=snippet  q  type=video|channel  maxResults=50  relevanceLanguage=en
             [publishedAfter: now−30d | the in-date run's moment with --force]
             fields=nextPageToken,items(snippet/channelId)   (video)
             fields=nextPageToken,items(id/channelId)        (channel)
   │  pool search; --pages (1); --limit; each page committed with its run
   ▼
yt_search_runs (query, type, asked_at, published_after, pages)    no API data; not dumped; 30 d
yt_raw_search_results (run, position, channel_id)                 API data; 30 d
   ▼  offline
yt search-candidates: round-robin over runs in date, by position, skip known and taken ──► --top ──► report | --add
```

- **Queries come from the operator per run**, as entries do for `yt add`. Whitespace is trimmed and collapsed, case is kept, and repeats are asked once. A query file stays on the operator's machine; it is not personal data, and committing it is the operator's choice.
- **Type `video` by default.** A result is then an upload of the last 30 days, so every channel it names publishes now, which is what the poll loop and the alerts need. `--type channel` finds established channels, dormant ones included. The first round runs both types on the same queries and compares their yield (see Migration Plan).
- **Only the channel id is read.** The video id is not needed, and the rest of `snippet` would be API data kept for nobody.
- **`relevanceLanguage=en`, fixed** (decided 2026-11-01: no Russian-language search). No `regionCode`, which filters by where a video can be viewed. No `videoDuration` until the yield says Shorts-only channels cost too much review.
- **Not asked twice.**
  - A run of the same (query, type) in date means no request without `--force`.
  - A forced video search asks only since that run.
  - The run row is written with its first page's results, so a request that failed stores nothing and blocks nothing.
  - A query with no results is recorded as a run with no results, so it is not paid for again within 30 days.
- **Why runs are not dumped.** A run holds no API data, but it says a search was answered. Restored without its results, it would skip a query whose answer is gone — the reason `yt_backfill_walks` is excluded too. `yt_search_queries` holds only what the operator typed and when, and goes into the daily inventory dump.
- **The cut.** Results carry no count. Taking each run's position in turn keeps YouTube's own order and lets no query crowd out the others. "How many queries returned a channel" would be a new number over API results, so it is not used.
- **Logs.** `search.list` URLs carry the query, so the pass runs under `request_urls_unlogged`. Logs name run ids and counts.

### The search bucket in the ledger

| | Today | This change |
|---|---|---|
| Pools | `general`, `watch` | + `search`, cap `youtube_search_daily_quota` (100) less `youtube_quota_margin`: 90 |
| Bucket of a pool | — (one) | `general`, `watch` → `main`; `search` → `search` |
| `quotaExceeded` | ends the day for every pool | ends the day for the bucket of the method that received it |
| `yt_quota_exhausted` | PK `day` | PK `(day, bucket)`; existing rows become `main` |
| Start check | any exhaustion today | exhaustion of the command's bucket |
| `COSTS` | three methods | + `channelSections.list: 1`, `search.list: 1` |

- **Which bucket a request uses** follows from its method: a table holding `search.list → search`, everything else `main`.
- **Consistency check.** A request whose method's bucket differs from its command's pool's bucket is refused before booking: a programming error, never sent.
- **The general cap is unchanged:** search does not draw on the 10,000.
- **How Google reports a spent search bucket is undocumented.**
  - If it answers `quotaExceeded`, the search bucket ends.
  - If it answers some other 403 reason, the client stops the command without recording exhaustion — closed, and harmless to the loop.

  The first run settles it.
- **Rejected:** keeping one exhaustion per day. A spent search bucket would then stop the poll loop until the reset, and nothing could prevent it — our cap of 90 holds only while our count and Google's agree.

### Provenance and the review file

- **`yt_discovery_source`** gains `mention`, `featured` and `search`; only the matching `--add` writes each.
- **The review file** gains a read-only column `source`, last. It holds the discovery source, and for `search` also the query of the earliest run in date that returned the channel. It carries no count, which avoids III.E.4.h's disclosure case as 4a did.
- **The column line changes**, so a file exported before the deploy is refused by the existing check. The deploy is made with no review file out.

### Policy readings

- **III.E.4.h(ii) and III.E.2.**
  - Mentions and featured lists use the one number change 4 established — how many distinct seeds name a target — to order and cut a review queue for the stated purpose, never stored or shown as a score. A featured list is another way a seed names a channel, not a new quantity.
  - Search uses no number at all: YouTube's own order, as YouTube returned it.
  - Each is the operator's reading of 2026-11-01, carrying change 4's risk and no more; a strict reading of III.E.2 would forbid even the count.
- **Records from `--add`** hold a channel id and its provenance, like `manual` and `telegram`; the id is the operator's record (change 1's reading).
- **III.D.3: no notice.** No allocated quota is used: search's 100 calls are every project's default. Both methods are named on the site, for "finding channels to review". Decided by the operator on 2026-11-01, as for changes 2–4a.
- **III.E.4.d, "limited amounts", read again at 175 seeds and priced for 500:**
  - 22,381 videos and 30 MB now; about 64 thousand videos and 90 MB at 500, nearer 100 thousand if the mix leans to the faster-uploading seeds;
  - every one belongs to a hand-reviewed channel, is trimmed to the privacy page, carries no counters and is refreshed or deleted within 30 days.

  The operator's reading holds: a year, revisited at 1,000 seeds or 150 thousand stored videos. Decided 2026-11-01.

### Closed without code

Each is recorded in `docs/PLAN.md`, "Left to change 5's explore", with its figures.
- **`brandPartner`: not read.**
  - It is a sponsorship grant, not a reference, so it answers none of the plan's three questions.
  - No probe was made. An empty answer could not tell "not given to an API key" from "this video has none", since no video is known to carry one.
- **Shortened links: not expanded.**
  - `amzn.to`, `goo.gle` and `jb.gg` alone are 48% of them: affiliate, Google's own and sponsor links.
  - An expansion is a request to a third party carrying a code read from API data, most likely counted by the shortener as a click on the creator's link.
  - Following the redirect onto YouTube would be III.E.6. The privacy page would have to name every shortener.
- **Bare `@`: the rule stands.** Against the inventory only. The closed set grows with each round; re-measure when the inventory reaches 300.
- **Telegram links in descriptions: counted only.** 42 seeds now — the channels found through Telegram link back to it. Reading them still needs a purpose the site does not state.

### Quota, priced on the measurements

Poll loop per day: 96·⌈N/50⌉ + listings (about 0.84·(N + U·N), measured) + readings (96·⌈17·U·N/(96·50)⌉).

| Seeds | Poll loop | General pool, ordinary day | General pool, round day |
|---|---|---|---|
| 175 | ≈ 690–730 | ≈ 20 (sections 7, past uploads 16) | + lookups ≤ 170, walk 320–640 |
| 500 | ≈ 1,600–1,850 of 3,000 | ≈ 90 (past uploads 51, sections 20, lookups ~20) | + walk of the round's new seeds ≤ 900 |

- **Where the poll loop runs out:** the reserve lasts to about 850 seeds at the measured rate. Past that: raise the reserve (the general pool keeps at least 5,000), or `videos.batchGetStats` for readings — a change of its own.
- **History** for 325 new seeds: 1,460–2,960 units once, at 4.5–9.1 a seed. The walk may spend 4,000 a day, so a round's new seeds are walked the following night.
- **Search:** 90 calls of 50 results, 4,500 results a day — never the limit. Review is.

### Rounds to 500 English seeds

377 more English seeds are needed. Telegram's yield is measured: 22 English seeds per hundred candidates, so its remaining ranking adds a few dozen. Mentions, featured lists and search are measured on the first round.
- At an English yield of 0.3–0.5 across them: 750–1,250 candidates, 8–13 rounds of a hundred.
- After each round, `discovered_via × status × lang` goes into `docs/PLAN.md` as aggregates, and the mix of the next round follows it.

### Documentation

The wording is proposed; the operator words both pages before archiving.

- **`docs/privacy.html`:**
  - **"What itgraph reads from YouTube":**
    - item 3 becomes:
      > **About channels and videos linked or named elsewhere**, before any of them is in its inventory — in the public Telegram channels itgraph follows, or in the titles and descriptions of its channels' uploads: for a linked video, its ID and the ID of the channel it belongs to; for a linked handle or legacy username, the ID of the channel it names.
    - new:
      > **Featured channels** of the channels in scope: the IDs of the channels each lists in its channel sections, and nothing else about the sections or those channels.
    - new:
      > **Search results**, for search terms chosen by the developer: for each result, the ID of the channel it belongs to, and nothing else.
  - **"How long it is kept":**
    - "The answers to lookups of linked videos, handles and names, the lists of featured channels and the search results are kept in the same tables and deleted 30 days after they were read."
    - "…which channels a video mentions or links to, and how many of the Telegram channels it follows or of the channels in its inventory name a channel — is not stored."
  - **"itgraph's own records":** "how it was found (by hand, through links from Telegram, through mentions or featured channels on YouTube, or through search), the search terms the developer has used and when…"
  - **"How it is used":** "…and to search YouTube for channels about software development and IT, for the same purpose."
  - **"Last updated":** the day of the deploy.
- **`docs/index.html`, "What it does with YouTube":**
  - "Mentions" gains: "When a mentioned or linked channel or video is not in the inventory, itgraph asks YouTube which channel it belongs to, and nothing else."
  - new:
    > **Featured channels.** For channels in scope, itgraph reads which other channels each features on its channel page — to find channels to review.
  - new:
    > **Search.** itgraph searches YouTube for recent videos and channels about software development, with search terms the developer chooses, and keeps only which channel each result belongs to — to find channels to review.
  - Methods unchanged. The status panel's month.
- **`docs/PLAN.md`:**
  - "Order of changes": row 5's text;
  - "Left to change 5's explore": each item closed, with the figures above;
  - "The graph is thin": the featured probe and the type values;
  - "The budget is quota": the measured upload rate and walk cost, the search pool and per-bucket exhaustion;
  - "Policy before engineering": the readings above;
  - the history-walk paragraph: "limited amounts" at 500;
  - a short "Rounds to 500" with the yield table, kept up to date by the operator.
- **`src/itgraph/README.md` (Russian):** the round as a runbook — commands in order, what each spends, what is never scheduled and why, the first round's comparison of search types, and the yield query.
- **`src/itgraph/CLAUDE.md`:** rows for every new module; amended rows for `yt/refresh.py`, `yt/client.py`, `db/yt_quota.py`, `derive/yt_references.py`, `db/yt_references.py`, `yt_review.py`.
- **Root `CLAUDE.md`, the YouTube exception:** "…such as which channels a video mentions, how many Telegram families link a channel, **or how many seeds feature a channel**, worked out when needed and never stored".

## Risks / Trade-offs

- [A strict reading of III.E.2 or III.E.4.h(ii)] → Nothing joined is stored and no number beyond change 4's is derived. Removing the three queues leaves tables that empty themselves within 30 days.
- [Google reports a spent search bucket as something other than `quotaExceeded`] → The client stops the command without marking any bucket, which cannot stop the poll loop. The first run's journal shows what came back.
- [The section type values change again, or a shelf moves under another type] → The type is not read.
- [A featured shelf the API does not return] → Checked on one seed, two of two. The first full read is compared with the inventory: seeds whose lists are empty are counted in the report.
- [Search finds Shorts farms and content farms] → Rejected at review with the existing reasons. `videoDuration` is held back until the yield shows the cost.
- [The first round's review is large: four queues] → `--top` per queue; the operator fills one file of about a hundred.
- [A file exported before the deploy is refused] → Deploy with no review file out.
- [Lookups of YouTube names expire and are paid for again monthly] → About 170 units at most, falling as channels enter the inventory.
- [Featured lists of seeds that stopped being seeds linger up to 30 days] → Never read: readers take seeds only.
- [Russian seeds keep arriving from Telegram and cost the poll loop] → The operator chooses each round's mix. Telegram rounds are optional under the English-only count.

## Migration Plan

1. **Two revisions,** verified on a `_test` database only — `upgrade head`, `downgrade -1` twice, `upgrade head`, then autogenerate shows no difference:
   - **Enum values, alone, with an empty downgrade, as `5d2b8e17c4a9`:** `yt_discovery_source` + `mention`, `featured`, `search`; `yt_command` + `mention-lookup`, `search`; `yt_quota_pool` + `search`.
   - **Types and tables:**
     - type `yt_quota_bucket` (`main`, `search`); `yt_quota_exhausted.bucket` NOT NULL DEFAULT `main`, primary key `(day, bucket)`;
     - type `yt_search_type` (`video`, `channel`);
     - `yt_raw_channel_sections(channel_id PK → yt_channels ON DELETE CASCADE, payload jsonb, fetched_at)`, index on `fetched_at`;
     - `yt_search_queries(id, query, type, first_asked_at, last_asked_at, UNIQUE (query, type))`;
     - `yt_search_runs(id, query_id → yt_search_queries, asked_at, published_after NULL, pages)`, index on `asked_at`;
     - `yt_raw_search_results(run_id → yt_search_runs ON DELETE CASCADE, position, channel_id with the id-shape check, PRIMARY KEY (run_id, position))`.

     The downgrade deletes the `search` exhaustion rows, restores the primary key and drops the rest.
   - `alembic upgrade` on the working database takes its full dump first, as always.
2. `db/backup.py`: `EXCLUDED_DATA` gains `yt_search_runs`; `INVENTORY_TABLES` gains `yt_search_queries`.
3. Deploy with no review file out. No new job and no new setting in `deploy/`.
4. **The first round, by the operator, through `scripts/on-host.sh`:**
   1. `yt refresh`, or wait for the job: every seed's sections, about 175 units. Then `yt featured-candidates`.
   2. `yt mention-lookup --limit 5`, `yt quota`; then `yt mention-lookup` until nothing awaits. Then `yt mentions`: the featured edges are there. Then `yt mention-candidates`, and `--min-seeds 1`.
   3. `yt search --from-file - --limit 5` with five queries; `yt quota` shows the search pool. Then about twenty queries, once `--type video` and once `--type channel`. Then `yt search-candidates`.
   4. `--add` from each queue, up to about a hundred in all; `yt refresh`; `yt review-export` → review → `yt review-apply -`.
   5. The yield query, `discovered_via × status × lang`, into `docs/PLAN.md`. The search type with the better English yield becomes the default for later rounds.

Rollback: remove the commands and the refresh step. The new `yt_raw_*` tables and the runs empty themselves within 30 days, and the second revision's downgrade drops them. Enum values stay. Records already added stay and are reviewed like any other.

## Assumptions

Confirmed by the operator on 2026-11-01 (explore, questions 1–13, "all as recommended", and two answers after the probe):
- separate queues, one number each; no rating across sources;
- every pending kind looked up, the cut after resolution; references to their own channel dropped; lookups not fed into `yt mentions`;
- `--min-seeds 2` for mentions, 1 for featured lists;
- featured lists read by `yt refresh` for seeds, 25 days, stored 30; featured as references of the graph;
- the section type not read; any section's channels are featured; the probe's two channels match the page;
- search: queries from the operator per run; `type=video` by default with 30 days of uploads; both types compared in the first round; 50 results a page; `relevanceLanguage=en`; no `regionCode`; channel ids only; no repeat within 30 days without `--force`; round-robin by YouTube's position; no count;
- distinct seeds as they are, no YouTube families;
- `brandPartner` not read, without a probe; shortened links not expanded; bare `@` rule unchanged; Telegram links counted only;
- a hundred candidates a round; featured in the refresh job; lookups, search, candidates and review by hand;
- the poll reserve at 3,000; a year of history, revisited at 1,000 seeds or 150 thousand videos;
- sources `mention`, `featured`, `search`; a `source` column in the review file, with the query for search; no counts in it;
- every record excluded, rejected channels never back;
- five commands, the 4a shape; none of them scheduled;
- no notice under III.D.3; the pages updated with the date;
- 500 counts English seeds only; no Russian-language search; query history in the inventory dump; no automatic filtering before review.

Not covered by the requirements, decided in this design and open to veto:
- the search bucket's cap carries the same 10% margin;
- a spent main bucket leaves search running;
- a request whose method's bucket differs from its pool's is refused before booking;
- a forced video search asks only since the run in date;
- a query with no results counts as a run;
- query text trimmed and its whitespace collapsed, case kept;
- runs left out of dumps, queries in the daily inventory dump;
- the review file's `source` column last, with the earliest run's query; old files refused;
- the order of the refresh: channels, sections, past uploads;
- a seed answered 404 or with no sections stored with an empty list;
- a seed answered `403 channelSectionForbidden` stored with an empty list too, decided in implementation;
- the lookup pass shared by both lookup commands in `yt/lookup_pass.py`, decided in implementation;
- `featured-candidates --add` refuses while a seed lacks a list;
- bare `@` re-measured once the inventory reaches 300 records;
- the proposed page wording.

Not covered by the requirements, open to measurement on the first round:
- how Google answers a spent search bucket;
- whether `fields` trims a search result to the channel id;
- the English yield of each source, and of each search type;
- how many of the 1,188 pending videos resolve to their own channel.

## Open Questions

None that change the specs or the tasks.
