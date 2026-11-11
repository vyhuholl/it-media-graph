## 1. Schema

- [x] 1.1 Add to `db/models.py`:
  - **enum members:** `YtDiscoverySource.MENTION`, `FEATURED`, `SEARCH`; `YtCommand.MENTION_LOOKUP = "mention-lookup"`, `SEARCH = "search"`; `YtQuotaPool.SEARCH`;
  - **new enums:** `YtQuotaBucket` (`main`, `search`) and `YtSearchType` (`video`, `channel`);
  - **`YtQuotaExhausted.bucket`**, part of the primary key;
  - **models:** `YtRawChannelSections`, `YtSearchQuery`, `YtSearchRun` and `YtRawSearchResult`, laid out as in design.md's Migration Plan, each docstring saying whose data it holds and why it is or is not dumped.

  Verify `make typecheck` passes.
- [x] 1.2 Write two Alembic revisions:
  - **first:** the enum values alone, with the empty downgrade and the reason `5d2b8e17c4a9` gives;
  - **second:** the types, the bucket column and primary key, and the four tables. Its downgrade deletes `search` exhaustion rows, restores the primary key and drops the rest.

  Verify on a `_test` database only: `upgrade head`, `downgrade -1` twice, `upgrade head`, then autogenerate reports no difference.
- [x] Verify with database tests:
  - malformed channel ids in sections' owners and search results are refused at the database level;
  - the inventory dump holds `yt_search_queries`' rows.

## 2. The ledger and the client

- [x] 2.1 In `db/yt_quota.py`:
  - `POOL_OF` gains `MENTION_LOOKUP → general` and `SEARCH → search`;
  - a pool → bucket table;
  - `pool_cap(SEARCH)` from the new setting `youtube_search_daily_quota` (default 100, validated ≥ 1) less `youtube_quota_margin`;
  - exhaustion recorded and checked per bucket;
  - `quota_report` shows the search pool and exhaustion per bucket.

  Verify with database tests for every scenario of the modified "Spending Caps", "Quota Exhaustion" and "Quota Report", and the added "Search Pool".
- [x] 2.2 In `yt/client.py`:
  - `COSTS` gains `channelSections.list` and `search.list`;
  - a method → bucket table, `search.list → search`;
  - a request whose method's bucket differs from its command's pool's bucket is refused before booking;
  - `quotaExceeded` records exhaustion of the request's bucket.

  Update the rows for both modules in `src/itgraph/CLAUDE.md`, and the quota section of `src/itgraph/README.md`: a third pool, and `quotaExceeded` per bucket. Verify with mocked-HTTP tests:
  - a `search.list` answered `quotaExceeded` leaves a following `watch` request sent;
  - a `channels.list` answered `quotaExceeded` leaves a following search sent;
  - a search booked under a general-pool command is refused unsent.

## 3. Featured lists

- [x] 3.1 Add `yt/sections.py`: `channelSections.list` for one channel id, `part=contentDetails`, `fields=items(contentDetails/channels)`, URL-encoded by the client. 404 means no sections. Add `db/yt_sections.py`:
  - store or replace a seed's trimmed response with `fetched_at`;
  - read the lists in date of seeds not marked gone;
  - name the seeds due;

  Add both rows to `src/itgraph/CLAUDE.md`. Verify:
  - mocked-HTTP tests for "Any Section's Channels Are Featured" — a fixture with eight sections of mixed, lowercase and undocumented types, one listing two ids — and for the declared-fields scenario "Channel sections are asked for their channel ids only";
- [x] 3.2 In `yt/refresh.py`, after the channel payloads and before past uploads, read the sections of every seed due, committing each. Update the row in `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Featured Lists Are Read With The Refresh", and "A restore is completed by a refresh" (lists read again).
- [x] 3.3 In `derive/yt_references.py` and `db/yt_references.py`:
  - seeds' lists in date are read in the same `REPEATABLE READ` transaction;
  - each id is a reference of the new form `featured`, with no video — an edge when known, a pending id otherwise — one per seed and target, the own channel dropped;
  - `Edge.video_id` becomes optional;
  - the report's first line counts the lists read.

  Update both rows in `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of the added "Featured Channels Are References" and the modified "Sources Are Seeds' Videos", "Known Targets Become Edges", "Unknown Targets Are Pending" and "References Live With Their Payloads"; the existing `yt_references` tests pass unchanged.
- [x] 3.4 Add `derive/yt_featured.py` (pure): featured evidence by distinct seeds, the own id dropped; the ranking outside the inventory; the report's lines. Add the CLI command `itgraph yt featured-candidates [--top 100] [--min-seeds 1] [--add]`: thin, offline; `--add` refuses while a seed not marked gone lacks a list in date, and otherwise records `featured`. Add the module's row to `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Featured Evidence", "Featured Ranking", "Featured Candidates Report", "Featured Candidates Are Added On Request", "Nothing Joined From Featured Lists Is Stored" and "Featured Logs Name Ids Only".

## 4. Mentions

- [x] 4.1 Add `yt/mention_lookup.py`, the pass:
  - the pending handles, legacy names and videos that seeds' videos reference — not featured ids — that no stored payload or lookup in date answers;
  - ordered by distinct seeds, then kind and name; videos fifty to a call in that order;
  - each answer committed through `db/yt_lookups.store_lookups`;
  - `YtCommand.MENTION_LOOKUP`, `--limit`, under `request_urls_unlogged`.

  Add `itgraph yt mention-lookup [--limit N]` in `cli.py`, thin, its report shaped like `tg-lookup`'s. Add the row to `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Mention Lookup Command" and the modified "Lookup Spending" and "Lookup Report", and a debug-level log test with no handle or link in any record.
- [x] 4.2 Add `derive/yt_mention_candidates.py` (pure). From one reading of the mention graph and the lookups in date:
  - each video-level reference names a channel, awaits a lookup, or is not found;
  - a reference to its own channel is dropped;
  - distinct seeds per channel;
  - the ranking outside the inventory (seeds, then id);

  Add the row to `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Evidence Is The Seeds' Videos", "A Reference Names A Channel Through What Is Stored", "A Reference To Its Own Channel Is Dropped", "Distinct Seeds Are The Unit Of Evidence" and "Mention Ranking".
- [x] 4.3 Add `itgraph yt mention-candidates [--top 100] [--min-seeds 2] [--add]` in `cli.py`: thin and offline. `--add` refuses while any reference awaits a lookup, naming the count and `yt mention-lookup`; otherwise it records `mention`. Verify with CLI tests for every scenario of "Mention Candidates Report", "Mention Candidates Are Added On Request", "Nothing Joined From Mentions Is Stored" and "Mention Candidates Logs Name Ids Only".

## 5. Search

- [x] 5.1 Add `yt/search.py`: one `search.list` page. `part=snippet`, `q`, `type`, `maxResults=50`, `relevanceLanguage=en`, `publishedAfter` for videos, `pageToken`, and the fields of design.md per type. Answers channel ids in order and the next token. Verify with mocked-HTTP tests for "What A Search Asks" and the declared-fields scenario "A search reads each result's channel id only".
- [x] 5.2 Add `db/yt_search.py`:
  - upsert a query (trimmed, whitespace collapsed) with its first and last asked moments;
  - write a run together with its first page's results, and append later pages;
  - read runs in date;

- [x] 5.3 Add the pass and the command `itgraph yt search [QUERY…] [--from-file PATH|-] [--type video|channel] [--pages 1] [--force] [--limit N]`:
  - input read as `yt add` reads it;
  - a (query, type) with a run in date skipped without `--force`;
  - the publication window as specified;
  - the search pool;
  - each page committed;
  - a summary of asked, skipped, results and requests;
  - logs by run id under `request_urls_unlogged`.

Add the rows to `src/itgraph/CLAUDE.md`.
- [x] 5.4 Add `derive/yt_search.py` (pure): round-robin over runs in date by position, skipping known and taken channels; the report's lines. Add `itgraph yt search-candidates [--top 100] [--add]`, offline, recording `search`. Verify with tests for every scenario of "Search Ranking", "Search Candidates Report" and "Search Candidates Are Added On Request".

## 6. Provenance and the review file

- [x] 6.1 In `yt_review.py`, add the read-only `source` column, last: the discovery source, and for `search` the earliest run in date's query; no count. Update the README's review section. Verify with tests for "The file says how a channel was found", "Only a queue records its own source" (including that `yt add` has no option recording `mention`, `featured` or `search`), and that a file with the previous column line is refused by the existing check.

## 7. Deploy, pages, plan

- [x] 7.1 Extend `tests/test_deploy_manifests.py`: with an API key, no rendered job runs `yt mention-lookup`, `yt mention-candidates`, `yt featured-candidates`, `yt search` or `yt search-candidates`; the refresh job is unchanged. Verify the test fails if a job running any of them is added to a fixture.
- [x] 7.2 Update `docs/privacy.html` and `docs/index.html` with design.md's wording, "Last updated" on the day of the deploy. Verify the pages render locally, and that every field the shapes of 3.1, 4.1 and 5.1 request appears on the privacy page.
- [x] 7.3 Update `docs/PLAN.md` as design.md's Documentation lists, and the root `CLAUDE.md`'s YouTube exception. Verify neither carries a Telegram-side figure or anything about the host.
- [x] 7.4 Write the round as a runbook in `src/itgraph/README.md` (Russian):
  - the five commands in the order of design.md's Migration Plan;
  - what each spends;
  - why none is scheduled;
  - the first round's comparison of search types;
  - the yield query.

  Verify every command line matches its `--help`.

## 8. Integration

- [x] 8.1 Run `make validate` and fix everything it reports; run `prek run --all-files`. Verify both are green.
- [x] 8.2 Run `openspec validate add-youtube-discovery --strict`. Verify it is valid.

## Workflow follow-up

- Deploy with no review file out; the operator runs the first round from design.md's Migration Plan and records:
  - what a spent search bucket returned, if it was reached;
  - whether `fields` trimmed the search results;
  - the yield per source and per search type.
- `/opsx:verify`, then `/opsx:archive`.
