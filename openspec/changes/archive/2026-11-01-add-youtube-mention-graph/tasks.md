## 1. Reading a text

- [x] 1.1 Add `derive/yt_references.py` with the reader of one text: NFC first, then the forms of design.md ("The forms").
  - **Followed:** handle links (percent-decoded, lowercased), channel id links, legacy name links (lowercased), and video ids from every video form.
  - **Hosts:** `youtube.com`, `www.`, `m.`, `music.`, `youtube-nocookie.com` for `/embed/` and `/v/`, `youtu.be`; scheme optional; trailing path and query ignored.
  - **Counted, never followed:** custom URLs, vanity URLs, playlist pages, shortened links (the design's list as one constant) and Telegram links (by number, no name kept).

  Add its row to `src/itgraph/CLAUDE.md`. Verify with tests for every scenario of "Channel Links", "Video Links" and "Forms Counted, Never Followed", on synthetic text only.
- [x] 1.2 Add the handle grammar and bare mentions to the same module.
  - **Grammar:** Unicode letters and digits, with `_ - . ·` inside but not at the ends; trailing separators taken as punctuation; 3–30 characters; lowercased.
  - **Bare `@`:** not after a letter, a digit or one of `_ . % + - / @`, and not before `/`. Bare tokens are returned for a closed-set match, never as pending.

  Verify with tests for every scenario of "Handle Grammar" and "Bare Mentions Name Only Known Handles", and further traps: `@Override`, `@media`, `@types/node`, `Twitter: @fake_tw.`, a mention ending a sentence, a handle in Cyrillic and one in Han.
- [x] 1.3 Add the synthetic fixtures under `tests/fixtures/`: titles and descriptions carrying every form and trap above. Ids are invented and spelt as fakes (`UCfake…`, `fakevideo…`), and no real description is used. Verify `prek run gitleaks --all-files` passes with no exclusion.

## 2. From forms to edges and pending targets

- [x] 2.1 Add the pure resolution to `derive/yt_references.py`. Input: each source video's forms and the known sets (handle → channel ids, channel ids in the inventory, stored video → channel). Output:
  - edges carrying the referencing video, its channel and `publishedAt`, the target channel, the target video and the form;
  - pending targets (handle, legacy name, channel id, video) with their seeds and referencing videos;
  - the ambiguous handles with their channel ids;
  - the not-followed counts.

  It deduplicates per video with the precedence `id link` > `handle link` > `mention` and drops self-references. Verify with tests for every scenario of "Known Targets Become Edges", "Unknown Targets Are Pending", "One Reference Per Target Per Video", "No Self-References" and "An Ambiguous Handle Names No Channel".
- [x] 2.2 Add the ordering: distinct seeds descending, then the target; and the `--min-seeds` and `--limit` bounds as pure functions. Verify with tests for "The most-named target first", "Ties are ordered the same way every time" and "Bounded by evidence"; and that nothing but the seed count is computed per target ("No Measure Over The Graph").

## 3. Loading

- [x] 3.1 Add `db/yt_references.py`, reading in one `REPEATABLE READ` transaction:
  - for videos whose channel is in `YT_REFERENCE_SOURCE_STATUSES = (SEED,)`: `video_id`, `channel_id`, and the payload's title, description and `publishedAt` only, streamed;
  - every payload's `customUrl`;
  - every `yt_channels` id;
  - every stored video's channel.

  Add its row to `src/itgraph/CLAUDE.md`. Verify with database tests for every scenario of "Sources Are Seeds' Videos", and that a channel description linking another channel adds nothing.
- [x] 3.2 Verify "References Live With Their Payloads" with database tests:
  - a payload replaced with a description that no longer links a channel loses the reference;
  - an expired payload's pending handle is gone after the expiry `_run_yt` runs;
  - a reading leaves every table's row count and every payload unchanged.

## 4. The report

- [x] 4.1 Add `itgraph yt mentions [--min-seeds N] [--limit N]` to `cli.py` through `_run_yt`, with the layout in design.md ("The report"): `--min-seeds` 1, `--limit` 50 per list, 0 lifts it; refuse a negative value before the database. Verify with CLI tests:
  - every scenario of "Mentions Report";
  - with no API key configured, it completes and no HTTP client is built ("No Network");
  - the Telegram count is shown and no Telegram name appears in the output;
  - `yt mentions --help` opens no database.
- [x] 4.2 Log counts at info and ids at debug, nothing read from a payload. Verify with a `caplog` test at debug level over fixtures full of handles, titles and links: no record contains any of them ("Logs Name Ids Only").
- [x] 4.3 Document `yt mentions` in `src/itgraph/README.md` (Russian):
  - what it reads, and that it writes nothing;
  - what "not followed" counts;
  - why bare mentions count only for handles in the inventory;
  - why it is never scheduled.

  Verify the README against `yt mentions --help`.

## 5. The walk switched on

- [x] 5.1 Set `youtube_backfill_enabled: true` in `deploy/group_vars/all/main.yml`, its comment saying it was turned on with change 4. Rename `test_main_yml_leaves_the_walk_off` to `test_main_yml_turns_the_walk_on` and assert `True`. Verify with the deploy tests: the rendered manifests with the example values plus the key carry `itgraph-yt-backfill` and `YOUTUBE_BACKFILL_ENABLED: "true"`; `make ansible-lint` passes.
- [x] 5.2 Add a deploy test that no rendered CronJob's container or init container has `mentions` among its args, with the walk on and a key ("The Report Is Not Scheduled"). Verify it fails when such a job is added to the template by hand, then passes once it is removed.
- [x] 5.3 Update the backfill section of `src/itgraph/README.md`: the switch is on from change 4; the first refresh of the walked year comes on 2026-11-27, at about 154 units; a deploy after 2026-11-29 means a re-walk of about 430 units. Verify the dates and figures against design.md ("The walk switched on").

## 6. Project documents

- [x] 6.1 `docs/index.html` and `docs/privacy.html`: the wording proposed in design.md ("Documentation"), as the operator settles it, and "Last updated" set to the day the change is deployed. Verify the pages state that mentions are worked out from uploads new and past and not stored, and that no field was added to the privacy page's lists.
- [x] 6.2 `docs/PLAN.md`: the corrections listed in design.md ("Documentation"):
  - `brandPartner`;
  - the amendment's scope and PageRank;
  - the ordering as the operator's reading;
  - bare mentions;
  - the switch;
  - open threads for change 5;
  - change 4's row.

  Nothing derived from the Telegram inventory goes in. Verify every figure against the measured table in design.md.
- [x] 6.3 Root `CLAUDE.md`: the clause in the YouTube exception, as in design.md. Verify it reads as one sentence with the paragraph around it.

## 7. Integration

- [x] 7.1 Run `make validate`; fix everything it reports. Not done while it is red.
- [x] 7.2 Run `openspec validate add-youtube-mention-graph --strict`; fix everything it reports.

## Workflow follow-up

- The operator deploys with the playbook by 2026-11-29: no migration, the walk switched on.
- First run, by the operator: `scripts/on-host.sh yt backfill-status` (enabled, 105 walked) and `scripts/on-host.sh yt mentions`, compared roughly with design.md's measured table. A surprise is a fix first.
- On or after 2026-11-27: `yt quota` shows the refresh's burst, and `yt backfill-status` the year still there.
- Change 5's explore takes the open threads: a one-unit `brandPartner` probe, a sampled probe of shortened links, re-measuring bare mentions against the grown inventory.
- `/opsx:verify`, then `/opsx:archive`. Commits go to `master`.
