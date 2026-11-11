## Why

The task asks for 500 or more English-language IT channels on YouTube. There are 123 English seeds, and the source that brought the last batch cannot bring the rest. The hundred candidates from Telegram yielded 70 seeds, but only 22 of them English: the Telegram crowd is Russian-speaking. The rest of its ranking is thinner than its top, and at that rate would add a few dozen English seeds. Decided by the operator on 2026-11-01: the 500 counts English seeds only.

What remains is what `docs/PLAN.md` scheduled for this change:
- **YouTube's own references:** 1,345 pending targets in the seeds' descriptions, waiting since change 4 for a lookup;
- **featured channels:** a one-unit probe on 2026-11-01 showed that `channelSections.list` returns a channel's featured shelf, and that it matches the page;
- **search:** the one source that can supply English channels at the rate the target needs.

Each of the three feeds the same review loop, so the loop is built here too: candidates → review → seeds polled and walked → new descriptions and featured lists → candidates.

## What Changes

- **YouTube's references resolved.** `itgraph yt mention-lookup` looks up the pending handles, legacy names and videos of the mention graph, through the lookups and the `yt_raw_lookups` table of change 4a:
  - most distinct seeds first;
  - from the general pool, with no floor and a `--limit`.

  `itgraph yt mention-candidates` is offline. It names the channel behind each reference, drops a reference that resolves to its own channel, and ranks the channels outside the inventory by how many distinct seeds reference them: two by default, then by id. With `--add`, the shown top becomes `candidate` records with discovery source `mention`. It refuses while any reference awaits a lookup.
- **Featured channels:**
  - **What is read:** the channel ids each seed lists in its channel sections, through `channelSections.list` with `contentDetails/channels` alone — the section's type is not read;
  - **When:** `yt refresh` reads them for every seed whose list is absent or older than the refresh age, a unit per seed;

  The mention graph takes them as channel-level references of the form `featured`. `itgraph yt featured-candidates` ranks the channels outside the inventory by distinct seeds featuring them, one by default. `--add` records `featured`.
- **Search.** `itgraph yt search` runs the operator's queries, given as arguments or on standard input:
  - one page by default, and only the id of each result's channel is read;

`itgraph yt search-candidates` takes the channels outside the inventory round-robin across the runs, in YouTube's own order, with no count. `--add` records `search`.
- **Search has its own bucket, and the ledger learns about it:**
  - a new pool `search`, capped at 100 calls less the margin;
  - `quotaExceeded` ends the quota day for the bucket the request was charged to, not for every command, so a spent search bucket cannot stop the poll loop;
  - `channelSections.list` and `search.list` get their prices.
- **One record per channel, never proposed twice.** No queue proposes a channel already in the inventory, whatever its status. Each queue has its own report and its own `--add`. The review file gains a read-only `source` column: how the channel was found, and for search the query.
- **Not scheduled:** every new report and `--add`. Featured lists are read by the daily refresh job. Lookups and search are run by hand through `scripts/on-host.sh`.
- **Closed without new code**, each recorded in `docs/PLAN.md`:
  - `brandPartner` is not read: it records a sponsorship, not a reference, and a probe cannot tell whether an API key receives it;
  - shortened links are not expanded: about half of them are affiliate, Google's own or sponsor links, every expansion would register a click with a third party, and the pages would have to name those third parties;
  - bare `@` mentions still count only against the inventory, now re-measured;
  - Telegram links in descriptions are counted only.
- **Documentation:**
  - `docs/privacy.html`: lookups of names from descriptions, featured lists, search results, how long each is kept, provenance, the purpose, and the date;
  - `docs/index.html`: the Featured and Search items, and lookups under Mentions;
  - `src/itgraph/README.md`: the round as a runbook;
  - `src/itgraph/CLAUDE.md`, and one clause in the root `CLAUDE.md`.

## Capabilities

### New Capabilities

- `youtube-mention-candidates`: the review queue built from the seeds' own references once looked up — what names a channel, what is dropped, how channels are ranked and cut, the report and `--add`; nothing joined is stored.
- `youtube-featured-channels`: the channel ids seeds list in their channel sections — the request, when it is made, where the answer lives — and the review queue built from them.

### Modified Capabilities

- `youtube-lookups`: a second lookup command over the mention graph's pending names, its order by distinct seeds; spending order stated per source.
- `youtube-mention-graph`: featured lists are a source; a featured channel is a channel-level reference; an edge names a referencing video only where there is one.
- `youtube-api-access`: the search pool and its cap; exhaustion per bucket; the quota report shows both; declared fields for the featured and search requests.
- `youtube-inventory`: discovery sources `mention`, `featured`, `search`; the review file's `source` column.

## Impact

- **Code:**
  - new `yt/sections.py` and `db/yt_sections.py` (featured lists);
  - new `yt/search.py`, `db/yt_search.py` and `derive/yt_search.py` (search and its queue);
  - new `yt/mention_lookup.py` and `derive/yt_mention_candidates.py`;
  - `derive/yt_references.py` and `db/yt_references.py` read featured lists;
  - `yt/refresh.py` reads sections;
  - `yt/client.py` and `db/yt_quota.py` handle the bucket and the costs;
  - `yt_review.py` gains a column;
  - five commands in the `yt` sub-app.
- **Schema:** two revisions, verified on a `_test` database.
  - **Enum values:** `mention`, `featured`, `search`; `mention-lookup`, `search`; pool `search`.
  - **Tables and types:** `yt_raw_channel_sections`, `yt_search_queries`, `yt_search_runs`, `yt_raw_search_results`; the bucket on `yt_quota_exhausted`.
- **Quota:**
  - general pool: the first lookup of YouTube's references is about 170 units, featured lists about 7 a day at 175 seeds and 20 at 500;
  - poll loop at 500 seeds: 1,600–1,850 a day of its 3,000;
  - search: its own bucket of 100 calls a day, outside the 10,000.
- **Deploy:** no new job; the refresh job also reads featured lists. A setting for the search bucket's size, default 100.
- **External:** no new method; both are named on the site. Both pages of the site are updated, with the date, before archiving.
