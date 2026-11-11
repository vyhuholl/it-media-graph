## 1. Schema

- [x] 1.1 Add the enum members to `db/models.py`:
  - `YtDiscoverySource.TELEGRAM = "telegram"`;
  - `YtCommand.TG_LOOKUP = "tg-lookup"`;
  - a new `YtLookupKind` (`video`, `handle`, `legacy`).

  Add the `YtRawLookup` model for `yt_raw_lookups` as design.md lays it out: primary key `(kind, value)`, nullable `channel_id` with the id-shape check, the video-id check, and an index on `fetched_at`. Its docstring says why it has no foreign key and is never refreshed. Verify `make typecheck` passes.
- [x] 1.2 Write two Alembic revisions.
  - **First:** `ADD VALUE IF NOT EXISTS 'telegram'` to `yt_discovery_source` and `'tg-lookup'` to `yt_command`, alone, with the empty downgrade and the reason `5d2b8e17c4a9` gives.
  - **Second:** type `yt_lookup_kind` and table `yt_raw_lookups`; the downgrade drops both.

  Verify on a `_test` database only: `upgrade head`, `downgrade -1`, `upgrade head`, after which autogenerate reports no difference.
- [x] 1.3 Verify with database tests:
  - a lookup with a malformed channel id or video id is refused at the database level;

## 2. Reading a post

- [x] 2.1 Make the one-link reader in `derive/yt_references.py` public, and have it return a custom URL's name, lowercased, alongside the `NotFollowed` kind. Make `_slice_utf16` in `derive/references.py` public. `yt mentions` keeps counting custom URLs by number only. Update both rows in `src/itgraph/CLAUDE.md`. Verify the existing `yt_references` and `references` tests pass unchanged, plus one test that `yt mentions`' report still names no custom URL.
- [x] 2.2 Add `derive/tg_youtube.py` (pure): one Telegram payload → the YouTube links it carries.
  - **Read:** `MessageEntityUrl` sliced in UTF-16, `MessageEntityTextUrl.url`, `media.webpage.url` when present — each through the one-link reader.
  - **Never read:** plain text without an entity, a preview's other fields, mentions.
  - **Output:** channel ids, handles, legacy names, video ids, custom URL names, and playlist and shortener counts, deduplicated per post.

  Add its row to `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Where A Post's Links Are Read", "Link Forms", "Telegram Mentions Are Not Handles" and "Forms Counted, Never Followed".
- [x] 2.3 Add synthetic Telegram fixtures under `tests/fixtures/` carrying every form and trap:
  - an emoji before an entity;
  - a link behind text;
  - a preview duplicating a text link, and a preview whose title and author name a channel;
  - a percent-encoded Cyrillic handle;
  - `notyoutube.com` and another look-alike host;
  - a trailing separator on a handle;
  - `youtu.be` with `si=`, `/shorts/`, `/live/`, `music.`;
  - `/c/` and vanity URLs, a playlist page, `bit.ly`, `t.me/iv?url=`;
  - a bare `@name` and a `MessageEntityMention`;
  - a forwarded post, and posts of two affiliated seeds.

  Ids and names are spelt as fakes (`UCfake…`, `fakevideo…`, `fake-…`). No real post and no name from any query output is used. Verify `prek run gitleaks --all-files` passes with no exclusion.

## 3. Ranking and the report, pure

- [x] 3.1 Add the naming step to `derive/tg_youtube.py`. Input: each post's family, date and links, and what is known — channel ids in the inventory, handle → channel ids from stored payloads, stored video → channel, lookups in date. Output, per YouTube channel: its families, its newest linking post, and the first handle or legacy name written for it. Alongside: the links awaiting a lookup, the links not found, and the custom URLs with their families. Verify with tests for every scenario of "Families Are The Unit Of Evidence" and "A Link Names A Channel Through What Is Stored".
- [x] 3.2 Add the ranking: channels with no inventory record, at least `min_families` families, ordered by families descending, then newest link descending, then channel id, bounded by `top`. Add the unanswered names ordered by families, for task 5.3. Verify with tests for every scenario of "Ranking", including the tie and the five known statuses.
- [x] 3.3 Add the report's lines: totals, the top rows, the rest in one line, awaiting and not-found counts, custom URLs with families, playlist and shortener counts. No field read from the API. Verify with tests for "A ranked row", "The top and the rest" and "What could not be counted".

## 4. Loading

- [x] 4.1 Add `db/yt_telegram_links.py`, reading in one `REPEATABLE READ` transaction:
  - own posts — no `fwd_from` — of channels with status `seed` that are not chats, streamed as payload, channel and date, with `COALESCE(family_key, tg_id)` from `channel_families`;
  - every `yt_channels` id;
  - every stored payload's `@handle`;
  - every stored video's channel;
  - every lookup in date.

  It writes nothing. Add its row to `src/itgraph/CLAUDE.md`. Verify with database tests for every scenario of "Sources Are Seeds' Own Posts", two confirmed affiliated seeds counted as one family, and "Reporting writes nothing" (row counts unchanged).

## 5. Lookups

- [x] 5.1 Add the request shapes:
  - `OWNER` in `yt/videos.py` (`part=snippet`, `fields=items(id,snippet/channelId)`);
  - an id-only shape for `by_handle` and `by_username` in `yt/channels.py` (`part=id`, `fields=items(id)`), the existing shape staying the default.

  Update their rows in `src/itgraph/CLAUDE.md`. Verify with mocked-HTTP tests for "A linked video is asked for its channel only" and "A linked handle or legacy name is asked for the channel id only", and that the import's, the refresh's and the poll loop's requests are unchanged.
- [x] 5.2 Add `db/yt_lookups.py` and `yt/lookups.py`.
  - **`yt/lookups.py`:** videos → channel, fifty to a call, an absent video recorded as not found, the existing 404 fallback; handle and legacy name → channel id, one a call.

  Both take names, not Telegram posts. Call the purge from `cli._expire_youtube_data`, with a log line by count. Add both rows to `src/itgraph/CLAUDE.md`. Verify:
  - mocked-HTTP tests for every scenario of "Video Lookups" and "Handle And Legacy Name Lookups";
- [x] 5.3 Add `yt/tg_lookup.py`, the pass.
  - **What it asks:** the names nothing answers, worked out by tasks 3.1 and 4.1.
  - **Order:** by families, videos batched in that order.
  - **Spending:** booked as `YtCommand.TG_LOOKUP`, added to `POOL_OF` as general, with `units_to_leave` returning 0 for it; bounded by `--limit`; each answer committed before the next request.
  - **Stops:** a spent pool, `quotaExceeded` or a failure ends the run and keeps what was stored.
  - **Logs:** ids only.

  Verify with tests for every scenario of "What Needs No Request", "Lookup Spending" and "Lookup Logs Name Ids Only".
- [x] Verify with CLI tests for "Lookup Command" (the session held by another process does not refuse it), "Nothing to look up" and "Lookup Report".
- [x] 5.5 Update both pages of the site in the same group as the fields they declare. `docs/index.html`: the "Links from Telegram" item and the Methods line. `docs/privacy.html`: the third "reads" item, lookups in "How long it is kept", the joined count named as not stored, provenance in "own records", the purpose, the Telegram paragraph, and "Last updated" set on the day of the deploy. Use design.md's wording. Verify the pages render — open `docs/index.html` and `docs/privacy.html` locally — and that every field the shapes of 5.1 request appears in the privacy page.

## 6. Candidates

- [x] 6.1 Add the CLI command `itgraph yt tg-candidates [--top 100] [--min-families 2] [--add]` in `cli.py`, thin over tasks 3 and 4.
  - **Without `--add`:** it prints the report and writes nothing.
  - **With `--add`:** it refuses while any link awaits a lookup, naming the count and `yt tg-lookup`. Otherwise it creates the shown top through `create_channels(…, discovered_via=YtDiscoverySource.TELEGRAM)` and reports the ids added.
  - **Never:** a request, or a need for an API key.

  Verify with CLI tests for every scenario of "Candidates Report" and "Candidates Are Added On Request". For "A candidate's data arrives with the refresh": a mocked refresh fetches the new record's payload, and `yt review-export` then lists it.
- [x] 6.2 Verify with a test that `itgraph yt add` records `manual` and has no option recording `telegram` ("Only the ranking of Telegram links records `telegram`").
- [x] 6.3 Extend `tests/test_deploy_manifests.py`: with an API key, no rendered job runs `yt tg-lookup` or `yt tg-candidates` ("Not Scheduled"). Verify the test fails if a job running either is added to a fixture.
- [x] 6.4 Add a log test over the whole ranking at debug level: no username, handle, custom URL or link in any record ("Telegram Links Logs Name Ids Only"). Verify it passes.
- [x] 6.5 Document in `src/itgraph/README.md`, in Russian:
  - both commands and their options;
  - the first run in design.md's order, including `--limit 5` and reading `yt quota`;
  - why neither is scheduled;
  - `yt refresh` after `--add`;
  - what the report never shows.

  Verify the command lines match `uv run itgraph yt tg-lookup --help` and `uv run itgraph yt tg-candidates --help`.
- [x] 6.6 Update `docs/PLAN.md`:
  - the `4a` row in "Order of changes";
  - "The graph is thin": Telegram's links as the first source of YouTube candidates.

  Update the root `CLAUDE.md`'s YouTube exception: "or how many Telegram families link a channel". Verify that neither file carries a figure from the Telegram side or anything about the host.

## 7. Integration

- [x] 7.1 Run `make validate` and fix everything it reports; run `prek run --all-files`. Verify both are green.
- [x] 7.2 Run `openspec validate add-youtube-candidates-from-telegram --strict`. Verify it is valid.

## Workflow follow-up

- Deploy; the operator runs the first run from design.md's Migration Plan through `scripts/on-host.sh`, and records what `videos.list` did with dead ids and whether `forHandle` resolved a non-ASCII handle.
- `/opsx:verify`, then `/opsx:archive`.
