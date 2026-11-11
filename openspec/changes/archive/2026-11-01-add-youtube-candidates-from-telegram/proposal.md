## Why

The YouTube inventory is mostly English-language educational channels from an outside list, plus a few Russian-language ones from the operator's subscriptions. That is too narrow for "what is spreading in the IT crowd right now", and change 4 showed the YouTube graph itself is thin: no unknown target is named by more than two seeds. The Telegram side has what YouTube lacks: hundreds of hand-reviewed seed channels, years of history already collected, and the same crowd linking YouTube constantly. Which YouTube channels those seeds link to is evidence about what the crowd watches. Decided on 2026-11-01: use it before change 5, which looks for candidates in YouTube's own references.

This takes nothing new from Telegram: the posts are in `raw_messages` already, and reading them needs no session. From YouTube it needs one answer per link — which channel a linked video or handle belongs to.

## What Changes

- A pure reader of a Telegram post's YouTube links:
  - **where:** links written in the text (`MessageEntityUrl`), links behind text (`MessageEntityTextUrl`), and the link preview's URL (`media.webpage.url`) — never the preview's title, author or site name;
  - **grammar:** change 4's, for one link at a time — channel, handle, legacy-name and video links;
  - **counted, never followed:** `/c/` and vanity URLs, playlist pages and shortened links;
  - bare `@name` is a Telegram username and is never read as a YouTube handle.
- **Sources:** seed channels' own posts, over the whole collected history. Not discussion chats, not channels in any other status, not forwarded posts.
- **Lookups:**
  - a linked video → its channel, through `videos.list` with only the video id and channel id requested;
  - a linked handle or legacy name → its channel id, through `channels.list` with only the id requested;
  - a channel id needs no request.

They are never refreshed and never dumped. The lookup code and the table are built so that change 5 can reuse both.
- `itgraph yt tg-lookup`: the one networked command. It looks up every linked name nothing stored already answers, from the general pool with no floor, bounded by `--limit`. Each answer is committed as it arrives, so a stopped run resumes.
- `itgraph yt tg-candidates`: offline. It ranks the channels outside the YouTube inventory by how many distinct seed families link to them:
  - at least two families by default;
  - ties broken by the newest linking post, then by channel id.

The report shows the top of the ranking, the rest of it as one line, what is still waiting for a lookup, and the `/c/` and vanity names for a person to look up by hand. With `--add`, it creates the top 100 as `candidate` records with the new discovery source `telegram`, and refuses while any link still awaits a lookup. Their payloads come with the next `yt refresh`. Review is the usual `review-export` / `review-apply`.
- A channel already in the inventory, in any status, gone included, is never proposed again. Since records are never deleted, the next `--add` brings the next hundred.
- Not scheduled: both commands are run by hand through `scripts/on-host.sh`. The report names channels read out of the operator's subscriptions.
- Documentation:
  - `docs/index.html`: a "Links from Telegram" item, and the two new uses of `videos.list` and `channels.list` under Methods;
  - `docs/privacy.html`: the fields read about linked videos and channels, how long lookups are kept, provenance among the operator's records, the purpose, and the date;
  - `src/itgraph/README.md`, `src/itgraph/CLAUDE.md`, and one clause in the root `CLAUDE.md`.

Out of scope:
- resolving YouTube's own pending targets, featured channels and search (change 5);
- feeding lookups into change 4's mention graph (change 5 decides);
- playlists (counted only), shortened links, link-preview metadata;
- accepting video links in `yt add`;
- alerts (6), scoring (7);
- anything about Telegram channels found through YouTube data.

## Capabilities

### New Capabilities

- Change 5 will add its own sources of names to look up.
- `youtube-telegram-links`: which YouTube channels the Telegram seeds link to. Covers the forms read from a post, whose posts count, families as the unit of evidence, the ranking and its report, and the candidates it adds to the inventory — all worked out on reading, never stored.

### Modified Capabilities

- `youtube-inventory`: discovery source gains `telegram`.
- `youtube-api-access`: "Requests Read Only Declared Fields" gains the two lookup requests, and the channel scenario is narrowed to requests for channel data.
- `youtube-data-retention`: lookups are a home of API data of their own.

## Impact

- **Code:**
  - new `derive/tg_youtube.py` (pure: post → links, ranking, report lines);
  - new `db/yt_telegram_links.py` (loading seed posts, families and what is known in one transaction);
  - new `yt/lookups.py` and `db/yt_lookups.py`, plus `yt/tg_lookup.py` (the networked pass);
  - two commands in the `yt` sub-app;
  - `derive/yt_references.py` exposes its one-link reader, `derive/references.py` its UTF-16 slice;
  - `yt/channels.py` gains an id-only request shape, `yt/videos.py` an owner-only one.
- **Schema:** one migration, verified on a `_test` database. It adds `yt_discovery_source` value `telegram`, `yt_command` value `tg-lookup`, type `yt_lookup_kind` and table `yt_raw_lookups`. The dump exclusion already covers `yt_raw_*`.
- **Quota:** from the general pool, no floor. The first run is a unit per 50 linked videos plus a unit per linked handle or legacy name: hundreds of units, under a tenth of the pool. Then 2 units per 100 added candidates on each refresh.
- **Deploy:** no new job, no new setting. Telegram's session and lease are untouched: nothing here speaks MTProto.
- **External:** no new method. Two new uses of `videos.list` and `channels.list`, both for the stated purpose, "finding channels to review". Both pages of the site are updated, with the date, before archiving.
