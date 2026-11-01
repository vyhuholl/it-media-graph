## Context

See proposal.md for why. This section holds only what shapes the approach.

### Facts from the documentation, checked on 2026-11-01

| Fact | Source |
|---|---|
| `videos.list`: 1 unit a call; `id` is "a comma-separated list of the YouTube video ID(s)", with no maximum stated. Nothing says how a deleted or private id is answered; the one 404 is `videoNotFound`, "The video that you are trying to retrieve cannot be found." | developers.google.com/youtube/v3/docs/videos/list (last updated 2026-10-15) |
| `channels.list`: 1 unit a call; filters "specify exactly one of the following parameters"; `forHandle` takes "a YouTube handle", which "can be prepended with an @ symbol"; `forUsername` one username; nothing on case or encoding. `id` is among the accepted parts | developers.google.com/youtube/v3/docs/channels/list (last updated 2026-10-15) |
| `fields`: `a,b` selects several, `a/b` a nested property, `a(b,c)` a group; described as saving bandwidth, nothing about quota | developers.google.com/youtube/v3/getting-started |
| Settled by earlier first runs: 50 ids per `videos.list` call (655 videos, no error, change 3); a handle or an id with no channel answered as 404 or as empty `items`, both read as not found (change 1) | archived designs of changes 1 and 3 |
| Not settled: how `videos.list` answers a batch holding a dead id (every id in change 3's first pass was live); whether `forHandle` resolves a non-ASCII handle | — |

### Policy text, verbatim (Developer Policies, last updated 2026-10-15)

- **III.D.3:** "If YouTube approves the application, you must use the additional quota granted only for the approved use case. If your API Client's use case changes, to use any allocated quota for the new use case, you must notify YouTube of the change by resubmitting an API Compliance Audit and receive approval for the application." `docs/PLAN.md` quotes this without its last clause.
- **III.E.2.a:** "Do not aggregate API Data except that you may only aggregate API Data relating to YouTube channels that are under the same content owner as recognized by YouTube pursuant to content licensing agreement(s) between YouTube and such content owner. Such aggregated API Data must only be viewable by that content owner."
- **III.E.2.b:** "Do not aggregate API Data or otherwise use API Data or YouTube API Services to gain insights into YouTube's usage, revenue, or any other aspects of YouTube's business." The section's example ends: "The API Client must not combine API Data from the different content owners."
- **III.E.4.d:** "API Clients may temporarily store limited amounts of Non-Authorized Data for as long as is necessary for the purposes of the API Client but not longer than 30 calendar days. As in section (III.E.4.c) immediately above, this means that after 30 calendar days, the API Client must either delete or refresh the stored data."
- **III.E.4.h:** "Your API Clients must not (i) replace API Data with similar, independently calculated data, or (ii) access or use API Data to create new or derived data or metrics. To the extent your API Clients display any information, data or metrics not based on API Data alongside API Data, your API Clients must include a clear and prominent disclosure there that such information, data and metrics are not from YouTube and are part of your own product."

### What the code already has

- `derive/yt_references.py`: change 4's grammar. Its one-link reader (`_youtube_link`) reads a single URL into a channel id, handle, legacy name or video, or counts it as a custom URL or a playlist page. `normalize_handle` applies NFC, drops trailing separators, enforces 3–30 characters and lowercases.
- `derive/references.py`: Telegram's entity reader, with the UTF-16 slice (`_slice_utf16`) that keeps an offset correct after an emoji.
- `db/views.py`: `channel_families`. The family of any Telegram channel is `COALESCE(family_key, tg_id)`.
- `yt/videos.by_ids` takes an `Asked` shape and asks a whole batch answered 404 again id by id. `yt/channels` has one fixed shape, `by_handle` and `by_username`.
- `db/yt_channels.create_channels(…, discovered_via=…)` creates records and never touches existing ones.
- `cli._expire_youtube_data`: the expiry every YouTube command runs first.
- `db/backup.EXCLUDED_DATA = ("yt_raw_*", …)`.
- `db/yt_quota.POOL_OF` and `units_to_leave`: the floor applies to `backfill` alone.

### Figures

The Telegram-side counts this design was priced on come from the operator's subscriptions. They are not recorded here or anywhere in the repository. The quota is stated by formula and order of magnitude.

## Goals / Non-Goals

**Goals:**
- A reviewable queue of YouTube channels the Telegram crowd links to, ordered by distinct families, with one networked step that resumes and every other step free and repeatable.
- Nothing joined from Telegram and the API stored; the API's answers kept at most 30 days and nowhere else.
- A lookup layer change 5 can feed its own names into.

**Non-Goals:**
- A stored ranking or a stored family → channel relation.
- Feeding lookups into `yt mentions` (change 4's `read_known`).
- Any reading of a link preview beyond its URL.
- Resolving playlists.

## Decisions

### Two commands: the network in one

```
raw_messages ⋈ channels(seed, not a chat) ⋈ channel_families
   │  offline, no Telegram session
   │  pure: Url entities (UTF-16 slice) + TextUrl.url + media.webpage.url
   │        → change 4's one-link reader
   ▼
links: (family, kind, name, post date)                  in memory, never stored
   │  answered without a request: channel ids; yt_raw_videos with a channel;
   │  a handle one yt_raw_channels payload carries; yt_raw_lookups in date
   ▼
yt tg-lookup [--limit N]        ═══ network, general pool, no floor ═══
   videos.list   part=snippet  fields=items(id,snippet/channelId)   50 a unit
   channels.list part=id       fields=items(id)  forHandle | forUsername   1 a unit
   → yt_raw_lookups, committed per answer
   ▼
yt tg-candidates [--top 100] [--min-families 2] [--add]      offline
   rank, report; --add → yt_channels(candidate, telegram), no payload
   ▼
yt refresh (daily job or by hand) → the new records' payloads, 2 units a hundred
   ▼
yt review-export → edit → yt review-apply
```

Alternative: one networked command, extracting, looking up, ranking and adding, with `--dry-run`. Rejected for three reasons:
- every look at the ranking costs the whole lookup again;
- a run stopped by `--limit`, the pool or a failure loses everything;
- change 5 would get the code but no store.

The two-command shape is change 4's "read on demand" with one difference. The API side is not stored yet, so it gets a `yt_raw_*` home, and the join stays on demand.

### The answers live in `yt_raw_lookups`

```
yt_raw_lookups
  kind        yt_lookup_kind      video | handle | legacy
  value       text                video id, case kept | normalize_handle() | lowercased legacy name
  channel_id  text NULL           NULL: YouTube named no channel
  fetched_at  timestamptz
  PRIMARY KEY (kind, value)
  INDEX (fetched_at)
  CHECK (channel_id IS NULL OR channel_id ~ '^UC[0-9A-Za-z_-]{22}$')
  CHECK (kind <> 'video' OR value ~ '^[0-9A-Za-z_-]{11}$')
```

Its properties:
- **No foreign key:** the owners are almost never in the inventory.
- **Never refreshed:** a name in date is not asked again, and after 30 days the row is removed by the shared expiry.
- **Kept out of dumps** by the existing `yt_raw_*` exclusion.
- **"Not found" is stored too,** so a dead video from an old post is not paid for on every run. It expires on the same clock.

Rejected:
- **`yt_raw_videos` with a trimmed payload.** It breaks three readers of that table:
  1. the walk's `known_video_ids` and `ON CONFLICT DO NOTHING` would skip a seed's video stored without title or description, and the year would lack it;
  2. `past_uploads_due_for_refresh` filters on `publishedAt`, which the trimmed payload lacks, so the row would never be refreshed;
  3. change 4's `read_known` maps every stored video to its channel, so `yt mentions` would silently grow edges to channels outside the inventory.

  Storing only videos of channels outside the inventory does not help: such a channel becomes a seed at review, and (1)–(2) return.
- **Memory only.** See the alternative in the decision above.

A video can sit in both homes for a while: looked up, and later stored by the walk once its channel is a seed. Each home expires on its own clock within 30 days, and removing either removes what it holds. `youtube-data-retention`'s "API Data Has One Home" names the lookup as a home of its own for that reason.

### What is read from a post

- **Three places:**
  - `MessageEntityUrl`, sliced in UTF-16 units — the slice becomes public in `derive/references.py`;
  - `MessageEntityTextUrl.url`;
  - `media.webpage.url` where the preview carries one; a pending or empty preview without a URL contributes nothing.
- **No pattern over plain text.** Telegram marks links server-side, and an entity's bounds are exact, so trailing punctuation never arises.
- **Each URL goes through change 4's one-link reader,** made public in `derive/yt_references.py`. It is extended to return a custom URL's name, because here the name comes from Telegram text, not from the API.
- **Not read from the preview:** title, description, author and site name. Those are YouTube's metadata as Telegram fetched them, and identifying a channel by them would be taking API-like data outside the API (the spirit of III.E.6 and III.I.14).
- **Bare `@name`:** never read, whether Telegram marked it as a mention or not. On Telegram it is a username.
- **A SQL prefilter, found necessary in implementation.** A seed's history is hundreds of thousands of posts, and only a few per cent mention YouTube. The loader streams only posts whose text, entities or preview URL match `youtu` or a shortener host (`PREFILTER`). That pattern is a superset of what the reader can find, and a test holds the two together. The totals are counted over every own post regardless.

### Whose posts: seeds' own

- **Statuses:** `seed` only, never a discussion chat (`is_chat`). This is narrower than the forward graph's `candidate`/`seed`/`maybe` on purpose: evidence about YouTube should come from the reviewed crowd.
- **Own posts only:** a message with `fwd_from` is skipped, whoever it came from. The link in a post is the author's choice, and the forwarder endorsed the post, not the link. A footer "my YouTube" under a post that spread would otherwise collect every forwarding family — the "one source drags in its junk" failure `docs/PLAN.md` warns about, in another shape. If the author is a seed, its own post is read anyway.

Rejected:
- counting the forwarder's family;
- crediting the original author's family.

### Families

Each post's family is `COALESCE(channel_families.family_key, tg_id)`, read in the same transaction as the posts. A YouTube channel's evidence is the set of distinct families whose own posts link it — by id, handle, legacy name or any of its videos.

### Lookups: shapes, order, bound, pool

- **Shapes:** a new `Asked` value `OWNER` (`part=snippet`, `fields=items(id,snippet/channelId)`) for `yt/videos.by_ids`, and an id-only shape (`part=id`, `fields=items(id)`) for `yt/channels.by_handle` and `by_username`. The parts cost the same unit and read the least the privacy page then has to list.
- **Order:** names with the most distinct families first, then by kind and name, so a limited run spends on what matters most. Videos go fifty to a call in that order.
- **Bound:** `--limit` counts requests, as `yt add` does; a run stopped by it, by the pool or by a failure keeps what it committed.
- **Pool:** general, `units_to_leave` = 0. The history walk's floor exists to leave this kind of command room, so it does not apply to it. A new `YtCommand` value `tg-lookup` attributes the spending in the journal.
- **The 404 fallback** of `by_ids` is kept unchanged (see Risks).
- **Request URLs kept out of the log, found necessary in implementation.** httpx logs every URL at INFO, and a `forHandle` URL carries the handle. `yt/client.request_urls_unlogged` raises the `httpx` logger to WARNING for the pass, `--verbose` included, and restores it afterwards. Other commands' URLs carry ids only and are untouched.
- **Answered without a request:**
  - a channel id;
  - a video whose stored payload names its channel;
  - a handle exactly one stored channel payload carries — two carriers mean the current owner is asked;
  - a lookup in date.
- **Reusable:** `yt/lookups.py` and `db/yt_lookups.py` take names, not Telegram posts. Change 5 feeds its pending handles, legacy names and videos into the same functions and the same table.

### Ranking

- **Excluded:** every channel id with a record in `yt_channels`, whatever its status, gone included. Records are never deleted, so a rejected channel never returns, and each `--add` brings the next ranked channels.
- **Order:** distinct families, most first; then the newest own seed post linking the channel, newest first — Telegram data, and the "recency" `docs/PLAN.md` puts in candidate scoring; then channel id.
- **Bounds:** `--min-families` defaults to 2, so a single family cannot put a channel in the queue; `--top` defaults to 100. If fewer channels qualify, fewer are shown and added.
- **No other number** is used to order or cut.

### The report

- **Totals:** seed posts read, posts with a YouTube link, families with one.
- **The top `--top` rows:** rank, channel id, a handle Telegram wrote for it — the alphabetically first if several, so the row does not depend on the order posts are read in — else a legacy name, none if it was linked only by id or videos; family count; the newest link's date.
- **The rest:** one line, ranked channels beyond the top by family count.
- **What could not be counted:**
  - links awaiting a lookup, with a pointer to `yt tg-lookup`;
  - links looked up as not found;
  - every custom URL name with its family count — these names are Telegram text, so they can be shown for a person to look up by hand and add with `yt add`;
  - the number of playlist pages and shortened links.
- **No data read from the API.** The family count is not API data, and III.E.4.h's second sentence asks for a disclosure wherever such a number is shown alongside API data. Showing none avoids the case. The review file stays as it is, without a family column, for the same reason.
- **Standard output only.** Log records carry ids.

### `--add`

- **Effect:** creates the shown top as records through `create_channels(…, discovered_via=telegram)`; nothing else is written.
- **Refusal:** while any link awaits a lookup, `--add` refuses, because a partial lookup would bias the cut toward channels linked by ids and stored videos.
- **No API data written.** The payloads come from `yt refresh`, which fetches every record whose payload is absent, 2 units a hundred, so `tg-candidates` stays offline.
- **Not through `yt add`:** `yt add --via telegram` was rejected so that only the ranking can record `telegram`.

### Policy readings

Change 4 is the precedent: by the operator's reading of 2026-11-01, one number per target — how many distinct seeds name it — may order the review queue, never stored or shown as a score. How this change compares:

| | Change 4 | This change |
|---|---|---|
| What is counted | references parsed from descriptions — API data | seed families' own posts — Telegram data |
| What the API contributes | the counted facts themselves | identity only: video → channel, handle → id |
| Stored | nothing | the raw answers, at most 30 days |
| Use | ordering the review queue | ordering and cutting the review queue into records |

- **III.E.4.h(ii) and III.E.2.** API Data is used less here than in the precedent, so the operator's reading for change 4 covers this change a fortiori. Recorded as the operator's reading of 2026-11-01. A strict reading of (ii) — grouping Telegram data by `channelId` as "use API Data to create new data" — carries the precedent's risk, no more. III.E.2 is not engaged: what is counted is not API Data, and each count belongs to one channel.
- **Records created by `--add`** hold a channel id and its provenance, like `manual` ones. The policies say nothing about identifiers; treating the id as the operator's record is the operator's reading, as in change 1.
- **III.D.3:** no notice. No allocated quota is used, the methods are already named on the site, and the purpose is the stated one, "finding channels to review". Decided by the operator on 2026-11-01, as for changes 3 and 4.
- **III.E.4.d, "limited amounts":** a lookup holds a name, a channel id or none, and a moment.

### Documentation

- **`docs/index.html`:**
  - after "Mentions":
    > **Links from Telegram.** When posts of the public Telegram channels itgraph follows link to YouTube channels or videos, itgraph asks YouTube which channel each linked video, handle or name belongs to, and nothing else — to find channels to review. It keeps that answer for at most 30 days and works out the rest when it is needed.
  - Methods:
    > `channels.list` for the inventory, to notice new uploads, and to find which channel a linked handle belongs to; `playlistItems.list` and `videos.list` for new and past uploads, and for the statistics of new ones; `videos.list` also to find which channel a linked video belongs to; `search.list` and `channelSections.list` for finding channels to review.
- **`docs/privacy.html`:**
  - "What itgraph reads from YouTube", a third item:
    > **About channels and videos linked from the public Telegram channels** itgraph follows, before any of them is in its inventory: for a linked video, its ID and the ID of the channel it belongs to; for a linked handle or legacy username, the ID of the channel it names.
  - "How long it is kept", the first item: "The answers to those lookups are kept in the same tables and deleted 30 days after they were read." And "…which channels a video mentions or links to, and how many of the Telegram channels it follows link to a channel — is not stored."
  - "itgraph's own records": "the channel ID, how it was found (by hand, or through links from Telegram), and the reviewer's labels…"
  - "How it is used": "…and to find which YouTube channels the public Telegram channels itgraph follows link to, for the same purpose."
  - "Telegram": "That part uses no YouTube data" stays true. Add: "Links to YouTube in those posts are used to find YouTube channels to review, as described above."
  - "Last updated": the day the change is deployed.
- **`docs/PLAN.md`:**
  - "Order of changes": a row `4a | add-youtube-candidates-from-telegram`, placed after 4, so changes 5–7 keep their numbers everywhere they are cited;
  - "Policy before engineering": the readings above, and III.D.3 quoted in full;
  - "The graph is thin": Telegram's links as the first source of YouTube candidates.
- **`src/itgraph/README.md` (Russian):** the two commands, the first run, why neither is scheduled, `yt refresh` after `--add`.
- **`src/itgraph/CLAUDE.md`:**
  - rows for `derive/tg_youtube.py`, `db/yt_telegram_links.py`, `yt/lookups.py`, `yt/tg_lookup.py` and `db/yt_lookups.py`;
  - the rows for `derive/yt_references.py`, `yt/videos.py` and `yt/channels.py` amended.
- **Root `CLAUDE.md`, the YouTube exception:** "…such as which channels a video mentions, **or how many Telegram families link a channel**, worked out when needed and never stored".

## Risks / Trade-offs

- [`videos.list` answers a whole batch 404 when one id is dead, and the fallback asks fifty one by one] → Old posts link many dead videos, so this could multiply the video cost by up to fifty and reach the pool's cap. The first run is `--limit 5`, read in `yt quota`'s journal. If whole batches come back not found, bisecting the batch replaces asking one by one, as a fix of its own. The lookups already stored survive either way.
- [`forHandle` does not resolve a non-ASCII handle] → It is recorded as not found and shown in the counts. The first run settles it.
- [A handle reassigned since the post] → Handles date from late 2022, so older posts carry none. The review reads what the handle resolved to, as for any import.
- [A partial lookup biases the cut] → `--add` refuses while anything awaits a lookup.
- [A lookup expires between `tg-lookup` and `--add`] → `--add` refuses; a repeat of `tg-lookup` pays only for what expired.
- [The ranking leans Russian-language] → `--add` again brings the next hundred; the review file shows each channel's declared language and country.
- [A strict reading of III.E.4.h(ii)] → Nothing joined is stored. Removing both commands leaves one table that empties itself within 30 days.
- [The report shows what the operator's subscriptions link to] → Standard output of manual commands only; the deploy test checks that no job runs them.
- [The inventory grows by up to a hundred records a run, refreshed for good] → 2 units a hundred a refresh cycle.
- [A video's channel held in two homes for a while] → Each expires within 30 days; the walk never reads lookups, and lookups never read the walk's rows except to skip a request.

## Migration Plan

1. **Two revisions,** verified on a `_test` database — upgrade, downgrade, upgrade:
   - `ALTER TYPE yt_discovery_source ADD VALUE IF NOT EXISTS 'telegram'` and `ALTER TYPE yt_command ADD VALUE IF NOT EXISTS 'tg-lookup'`, alone, with an empty downgrade, as in `5d2b8e17c4a9`;
   - type `yt_lookup_kind` and table `yt_raw_lookups`, with a downgrade that drops both.

   `alembic upgrade` on the working database takes its full dump first, as always.
2. Deploy. No setting, no job.
3. **First run, by the operator, through `scripts/on-host.sh`:**
   1. `yt tg-lookup --limit 5`, then `yt quota`: how the five requests ended, and whether any batch came back not found as a whole;
   2. `yt tg-lookup` until nothing awaits a lookup;
   3. `yt tg-candidates`: read the ranking;
   4. `yt tg-candidates --add`;
   5. `yt refresh`;
   6. `yt review-export` → edit → `yt review-apply -`.

Rollback: remove the commands. The table empties itself within 30 days and can be dropped by the second revision's downgrade. The enum values stay, as before. Records already added stay too, since records are never deleted, and are reviewed like any other.

## Assumptions

Confirmed by the operator on 2026-11-01 (explore, questions 1–12, "all as recommended"):
- two commands, `yt tg-lookup` (network) and `yt tg-candidates` (offline, `--add`);
- answers in `yt_raw_lookups`, 30 days, not refreshed, not dumped; nothing joined stored; `telegram` records by the precedent of `manual`;
- the minimal request shapes, and the site's wording above with the date;
- no notice under III.D.3;
- sources: seeds that are not chats, own posts only; link previews by URL only;
- every record in `yt_channels`, gone included, excluded; top 100 among unknown channels;
- change 4's grammar; custom URL names shown; playlists and shorteners counted;
- general pool, no floor, `--limit`;
- discovery source `telegram`; which families linked is not stored;
- the reading of III.E.4.h(ii) and III.E.2 by the precedent of change 4, and no API data in the report or new columns in the review file;
- `--top 100` per run, the rest in one line;
- run by hand, no scheduled job;
- tie-break by the newest link, then id; `--min-families 2`; a video link counts for its channel;
- lookups not fed into `yt mentions`; `yt add` keeps refusing video links;
- figures in artifacts as formulas and orders of magnitude;
- command names `yt tg-lookup` and `yt tg-candidates`;
- channel ids not checked before `--add`; the refresh marks a dead one gone.

Not covered by the requirements, decided in this design and open to veto:
- a forwarded post is skipped whatever its origin, a user or a hidden sender included;
- no "+N via forwards" column — asked in explore without a recommendation, defaulted to not shown;
- `--add` refuses while any link awaits a lookup;
- lookups ordered by distinct families;
- a handle carried by two records is looked up anew;
- a stored video without a channel in its payload is looked up;
- the report's layout, and showing the alphabetically first handle Telegram wrote;
- custom URL names lowercased;
- the PLAN row numbered `4a`.

Not covered by the requirements, open to measurement on the first run:
- how `videos.list` answers a batch holding a dead id;
- whether `forHandle` resolves a non-ASCII handle;
- how much of the ranking is English-language.

## Open Questions

None that change the specs or the tasks.
