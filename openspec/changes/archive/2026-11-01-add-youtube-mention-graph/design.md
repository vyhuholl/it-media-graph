## Context

Fourth YouTube change. It inherits the conventions of changes 1–3 unchanged:
- API data only in `yt_raw_*`;
- expiry first in every `yt` command through `_run_yt`;
- ids only in logs;
- `scripts/on-host.sh` for every run on the host.

Motivation is in `proposal.md`; requirements are in `specs/youtube-mention-graph/spec.md`.

**Reused:**
- `_run_yt`, so the report reads only what expiry has left;
- the status allowlist pattern of `derive/edges.DERIVATION_SOURCE_STATUSES`;
- `derive/yt_videos.moment_of` for the publication time.

**Not carried over from Telegram's derivation:**
- **The stored `edges`, `pending_mentions` and `pending_mention_sources`, and `--rebuild`.** A handle from a description is API data, and nothing worked out from it may outlive its payload.
- **Discovery by id.** Telegram creates a channel row for every forward. Here no reference creates a record.
- **`derive/references.py`.** Its handle grammar is Telegram's: `[a-z][a-z0-9_]{3,31}`, no dot, no hyphen. It would cut `@fake.seed.two` to `fake`, which is somebody else.
- **`yt_entries._handle`.** It is loose on purpose, because the API judges what an operator pastes. An open set read from text needs the opposite (see "Bare mentions").

**The application.** Its text is not kept. What it promised is in `docs/PLAN.md` ("Filed on 2026-10-25"), `docs/index.html` and `docs/privacy.html`, and those are the requirements here. No answer yet.

### Facts checked against the documentation, 2026-11-01

| Fact | Source |
|---|---|
| III.E.2: "Do not aggregate API Data except that you may only aggregate API Data relating to YouTube channels that are under the same content owner as recognized by YouTube"; its example ends "The API Client must not combine API Data from the different content owners." "Aggregate" is not defined | developers.google.com/youtube/terms/developer-policies (updated 2026-10-15) |
| III.E.4.h: "must not (i) replace API Data with similar, independently calculated data, or (ii) access or use API Data to create new or derived data or metrics" | same |
| III.E.4.d: "temporarily store limited amounts of Non-Authorized Data … not longer than 30 calendar days" | same |
| III.D.3: the notice is tied to using "any allocated quota for the new use case" | same |
| The amendment: "Policy III E.4.b, E.4.c, and E.4.d are amended to allow accepted API Clients to store metrics". What it permits: "You may rank channel performance or track views between different channels"; "You may use analysis to assign descriptive sub-genres or tags to videos and channels". It does not mention III.E.2, III.E.4.h, or relationships between channels | …/terms/derived-metrics-policy (updated 2026-09-14) |
| Handles: "Is between 3-30 characters", except Han/Hangul 1–10 and Ethiopic/Hiragana/Katakana 2–20; letters or numbers of 75 supported languages; "underscores (_), hyphens (-), periods (.), Latin middle dots (·)", not at the start or end; not "URL-like or phone number-like"; "Handles aren't case-sensitive." Nothing on when an old handle is released | support.google.com/youtube/answer/11585688 |
| Mentions: a creator adds another channel's name or handle to a video's title or description by typing "@" and choosing from a list, and the channel is notified. Nothing says whether an `@word` not chosen from the list links anywhere | support.google.com/youtube/answer/9637404 |
| Channel URLs: `/channel/<id>`, `/@handle`, `/c/<name>`, `/user/<name>`; "All legacy URLs now redirect users to your new channel URL that is based on your handle". `youtube.com/<name>` is not listed | support.google.com/youtube/answer/6180214 |
| `brandPartner`, a part of `videos.list` since 2026-07-07: "details about the brand partner linked to the video for Creator Initiated Brand Partner Access (CI BPA)"; `channelId` is "The external channel ID of the brand partner"; "only channelId is returned in the response". The parts listed as owner-only are `fileDetails`, `processingDetails` and `suggestions`; nothing says whether an API key receives `brandPartner` | …/v3/docs/videos; …/videos/list; …/revision_history |
| `snippet.description`: at most 5,000 bytes | …/v3/docs/videos |

`docs/PLAN.md` calls `brandPartner` "a documented reference from a video to a channel". It is an access grant to an advertiser: the creator lets a brand see the video's statistics and run it as a partnership ad. It answers "who sponsors whom", which is none of the plan's three questions.

### Measured, 2026-11-01

Read-only aggregate queries on the host; no title, description, handle or link was printed.

| Figure | Value |
|---|---|
| Stored videos | 8,315: 7,660 found by the walk (95 seeds), 655 by the poll loop (68 channels); 8,155 with a description |
| Inventory | 105 seeds and 9 rejected, all with a payload |
| `snippet.customUrl` | 114 of 114 begin with `@`, are lowercase and ASCII; 3 to 27 characters; 3 with a separator inside |
| `youtube.com/@…` | 1,062 links in 857 descriptions of 30 seeds, 95 distinct. 216 to the video's own channel, 84 to other seeds, 762 to 84 handles outside the inventory |
| `youtube.com/channel/UC…` | 621 links to 20 distinct ids. 542 (87%) to the own channel; 79 to 9 ids outside the inventory |
| `youtube.com/user/…` | 220 links, 2 seeds |
| `/c/…` and `youtube.com/<name>` | 163 and 251 links, 10 and 13 seeds |
| Video links | 2,477 links to 1,387 distinct videos. 530 are stored: 516 of the own channel, 14 of other seeds. 1,047 are not stored. 408 links carry `list=` |
| Playlist pages | 605 links in 512 descriptions |
| Bare `@` | 2,045 in 709 descriptions of 37 seeds, 353 distinct; 81 shorter than three characters. 12 name a handle in the inventory, 4 of them the own channel's: 5 distinct other channels. In titles: 31, none in the inventory |
| Channel descriptions | 101 of seeds: 4 links to the own channel, 2 to unknown handles, 3 plausible bare handles |
| Evidence | Named by two seeds: 2 of 84 unknown handles, 0 of 9 unknown ids, 5 of 339 unknown bare handles. Named by three or more: none |
| Shorteners | `bit.ly` in 1,209 descriptions (15%), others in 896 (11%) |
| Telegram | 251 links from 4 channels to 25 names |
| Other platforms | `x.com`/`twitter.com` in 2,481 descriptions, GitHub 1,711, Instagram 1,625, LinkedIn 1,048, Bluesky/Threads/Mastodon 648 |
| Listing order | 7 items out of order across 105 walks (`yt backfill-status`) |

One figure did not reconcile. One pattern counted 55 `@` directly before a non-ASCII letter, and the tokenizer in the same run counted none. The two agree on ten scripts under both the C and the en_US locale on a local database. No decision rests on it: unknown bare handles are not followed, every handle in the inventory is ASCII, and the parser here is Python's, tested on non-Latin fixtures.

## Goals / Non-Goals

**Goals:**
- Every reference a seed's video makes to another channel or video, read when asked, so that it can never outlive the payload it came from.
- Change 5's queue, ready to be read: unknown handles, legacy names, channel ids and videos, each with the seeds that name it.
- The year kept fresh from this change on, through the switch.

**Non-Goals:**
- A stored graph. If a reader needs one later, the sketch under "Read on demand" is how it would be stored.
- Any measure over the graph beyond how many distinct seeds name a target.
- Reaching beyond YouTube: Telegram, X and other platforms, and shortened links.

## Decisions

### Read on demand, never stored

Decided by the operator on 2026-11-01. The three options, priced on 8 thousand videos today and 40 thousand at 500 channels:

| | **A:** `yt_raw_*_refs` tables, cascading from the payload | **B: read on demand** | **C:** truncate and rebuild |
|---|---|---|---|
| Payload removed | `ON DELETE CASCADE` | nothing to do | cascade |
| Payload refreshed with another description | stale until reparsed: a `source_fetched_at` column, a sweep in expiry, a reparse | nothing to do | stale until the next rebuild |
| Dumps | covered by `yt_raw_*` | nothing to leave out | as A |
| Migration | yes | none | yes |
| Volume | tens to a few hundred thousand rows | 7 / 36 MB of text, parsed in seconds | as A |
| Change 5 | its queue in SQL | the same function; its memory of handles not found is its own, under the 30-day rule | as A |

B keeps the rule without machinery: what is not stored cannot outlive anything, including an older version of a description.
- **Cheap to defer.** "Anything derivable can be deferred for free" holds here: the payloads are kept fresh for a year, so materializing later loses nothing.
- **The cost.** Each reader parses again, which takes seconds.

If a reader ever needs A, its rows hold handles, so the tables take the `yt_raw_` prefix and the dump exclusion that comes with it. A reference parsed from a replaced version is deleted at the replacement, not kept 30 days from the old fetch.

### What is read, and from whom

- **Sources: seeds' stored videos, title and description,** whichever command stored them. `YT_REFERENCE_SOURCE_STATUSES = (SEED,)` is an allowlist, as on Telegram, so a new status is admitted on purpose. It matches what the walk and the poll loop read.
- **Titles are read** because mentions are a YouTube feature in titles too, and the site already says "titles and descriptions".
- **New uploads are read with past ones.** The purpose is the same; `docs/index.html` gets one sentence.
- **Channel descriptions are not read.** Measured: two new targets from 101 descriptions, against a change to both pages of the site. Their payloads are kept fresh anyway, so adding them later costs nothing.
- **`brandPartner` is not read.** Reading it would change the collector and the privacy page. It is a sponsorship, not a mention (see the facts), and whether an API key receives it at all is unknown. A one-unit probe belongs in change 5's explore.

### The forms

```
 title + description  (NFC first)
   ├─ youtube.com/@h …            ─► handle h      (percent-decoded, lowercased, grammar)
   ├─ youtube.com/channel/UC… …   ─► channel id
   ├─ youtube.com/user/NAME …     ─► legacy name   (lowercased)
   ├─ watch?v= · youtu.be/ · /shorts/ · /live/ · /embed/ · /v/  ─► video id
   ├─ @h, not after [\w.%+/@-], not before '/'   ─► bare handle (closed set only)
   └─ counted, never followed:
        /c/NAME · youtube.com/NAME · /playlist?list= · shorteners · t.me · bare @h outside the inventory
 hosts: youtube.com, www., m., music.; youtube-nocookie.com for /embed/ and /v/; youtu.be
 ignored: scheme, trailing path, list=, t=, si=, sub_confirmation, feature=
```

- **`/c/` and `youtube.com/<name>` are counted.** The API cannot look them up. Guessing that the name is also the handle can land on someone else, which is change 1's reason for refusing them.
- **Playlist pages are counted.** Their owner needs `playlists.list`, which is not a method the site declares. A `watch?v=X&list=Y` link is taken as the video X.
- **Shorteners are counted.** Expanding one is a request to a third party, made from a description, and change 4 has no network. They sit behind about a quarter of the descriptions, which earns them a sampled probe in change 5's explore: how many lead to YouTube at all. The constant holds `bit.ly`, `jb.gg`, `goo.gl`, `goo.gle`, `tinyurl.com`, `t.co`, `buff.ly`, `ow.ly`, `lnkd.in`, `amzn.to`, `rebrand.ly`, `cutt.ly`, `is.gd`, `shorturl.at`, `rb.gy`, `geni.us`, `dub.sh`, `tiny.cc`, `bit.do`, `clck.ru`, `surl.li`, `hubs.ly`, `hubs.la`, `spoti.fi`, `apple.co` and `s.id`.
- **Telegram links are counted, by number only.** Finding Telegram channels through YouTube data is a purpose the site does not state, and mixing names from descriptions into `pending_mentions` would take API data into Telegram's tables and its backups. `docs/PLAN.md` records that four seeds link to 25 Telegram names, as an open thread rather than a plan.

### The handle grammar

YouTube's, from the facts table:
- letters and digits of any script, and the combining marks those scripts write with, checked character by character — not Python's `\w`, which stops at a Devanagari vowel sign and would cut a Hindi handle in two (found while implementing);
- a letter or digit first;
- `_ - . ·` inside;
- 3–30 characters.

Separators at the end are punctuation after the handle, so `youtube.com/@fake.seed.two.` is `fake.seed.two`. A handle link that still fails the grammar references nothing.

- **Normalization.** Text is NFC-normalized, a link's handle percent-decoded, and every handle lowercased. Measured: every `customUrl` is lowercase already, so comparing lowercased handles against it is exact. `casefold()` was not chosen: the inventory and `yt mark` already compare with `lower()`, and two foldings in one system disagree on exactly the cases where it would matter.
- **One length for every script.** 1–2-character Han and Hangul handles are refused. There are none in the data, and one rule beats a table of scripts nobody here reads.
- **A domain-like bare token is not refused separately.** Bare handles are matched only against the inventory, where no handle is URL-like.

### Bare mentions: the inventory only

Decided by the operator on 2026-11-01, by a threshold set before the measurement.
- **The threshold missed.** It was 20% of plausible bare handles naming a channel in the inventory. That was set badly: by distinct handles, even handle links reach only 12%.
- **The relative reading is plain.** Bare `@` names an inventory handle 5–47 times less often than a handle link: 0.6% of mentions against 28% of links, and at most 2.5% of distinct handles against 12%.
- **What they mostly are.** Most bare `@` in this crowd's descriptions are guests' and authors' handles elsewhere. X alone is linked from 30% of descriptions.

So a bare `@h` is a reference only when the inventory carries `h`. That is the rule from `affiliation/signals.py`: a loose pattern is safe against a closed set and nowhere else. A mention outside the inventory is counted, not queued.
- **The closed set grows.** Each channel change 5 adds turns mentions of it into edges without any change here.
- **Unknown mentions** were 339 distinct handles, 334 of them named by one seed. Re-measure when the inventory has grown.

Rejected:
- **A context heuristic** ("Twitter: @x" is not YouTube). It classifies description content, and the amendment lists "content categorization and tagging" among what acceptance permits. Before acceptance that puts it in the same class as language detected from titles, which was refused. It is also brittle: on the test data, the label `x` fired on an `x.com/…` link beside a YouTube handle.
- **A stop-list** of CSS at-rules and annotations. It is needed only for an open set.

### Known targets and pending targets

| Reference | Known without the network | Edge | Otherwise |
|---|---|---|---|
| handle link | a payload's `customUrl` | to that channel | pending handle |
| bare handle | a payload's `customUrl` | to that channel | counted |
| channel id link | a `yt_channels` row, with a payload or not | to that channel | pending id |
| legacy name link | never: no payload carries it | — | pending legacy name |
| video link | a `yt_raw_videos` row | to the video and its channel | pending video |

- **The known handles are every payload's,** whatever the channel's status, because the refresh keeps them all. A rejected channel is a target, as on Telegram: a seed linking it is a fact about the seed.
- **A handle carried by two payloads** gives no edge, and the report names both ids. `yt mark` already refuses such a handle.
- **No inventory record is created**, even for a `/channel/UC…` id that needs no lookup:
  - a record makes `yt refresh` spend on it, so an offline command would spend quota indirectly;
  - records are never deleted, so an id typed wrong in a description would stay forever;
  - Telegram does the same with an id lifted from a link (`derive/edges.py`).
  
  Change 5 creates records, with a discovery source of its own.

### One reference per target per video; no self-references

- **The key** is the referencing video plus the target channel and the target video, or none: one edge per post-level reference, as on Telegram. A pending target is counted by its seeds, and the referencing videos are kept beside them for change 5.
- **Precedence.** A channel reached two ways in one video is recorded once, as the first of `id link`, `handle link`, `mention`. A video and its channel are two references.
- **Self-references** are dropped by the video's own channel id, by its own handle (read from its own payload) and by its own videos. Measured: 87% of channel-id links and 97% of links to stored videos are self.

### Ordering, and nothing beyond it

Decided by the operator on 2026-11-01. The report and change 5's queue sort targets by how many distinct seeds name them, then by target. The count is computed when asked for, never stored, and shown only to the operator.
- **It is triage.** It orders the review queue for the purpose the site states, "find channels to review". The edges decision already brought several owners' descriptions into one graph, and the sort adds no new quantity.
- **The risk** is a strict reading of III.E.2, "must not combine API Data from the different content owners". It is named in `docs/PLAN.md`.
- **At 105 seeds the sort orders almost nothing.** Two seeds is the most any unknown target has.

PageRank, centrality, clusters and weights over YouTube references are out — not deferred to acceptance, because the amendment does not cover them:
- it amends the storage clauses;
- it lists ranking "channel performance" and tagging;
- it says nothing of III.E.2 or of relations between channels.

If they are wanted, the question goes to YouTube in the application's thread. Telegram's plan is unaffected.

### The code

- **`derive/yt_references.py`, pure:**
  - a text in, the forms it names and the counts of what was not followed out;
  - then the forms of each video, with the known sets, in; edges, pending targets with their seeds, the ambiguous handles and the counts out.
  - Testable with no database and no network.
- **`db/yt_references.py`:**
  - streams `video_id`, `channel_id` and the payload's title, description and `publishedAt` for seeds' videos;
  - reads every payload's `customUrl`, every `yt_channels` id, and every stored video's channel;
  - all in one `REPEATABLE READ` transaction, so the known sets and the sources agree.
  - Change 5 calls the same function.
- **`itgraph yt mentions [--min-seeds N] [--limit N]`** in `cli.py`.
- An edge carries the referencing video's `publishedAt`, read from its payload, for the analysis that comes later. Nothing here uses it.

### The report

```
read 8315 videos of 95 seeds: 98 references to 9 known channels, 1140 pending targets
known channels, most seeds first:
  UCfakeSeedTwo000000000bb  @fake.seed.two   2 seeds  UCfakeSeedOne000000000aa UCfake…
pending, most seeds first:
  handle   fake-unknown          2 seeds  UCfake… UCfake…
  video    fakevideo99           1 seed   UCfake…
  legacy   fakelegacy            1 seed   UCfake…
  id       UCfakeUnknown000000000dd  1 seed   UCfake…
not followed: 163 custom URLs, 605 playlist pages, 3104 shortened links, 251 Telegram links, 2033 mentions outside the inventory
ambiguous handles: none
```
(The figures are illustrative.)

- **Bounds.** `--min-seeds` (default 1) and `--limit` (default 50 per list, 0 lifts it).
- **Where it goes.** To standard output for a person. It names handles, as `yt channels` names titles, so no scheduled job runs it: a job's output is kept in the cluster's logs. The deploy test checks that no CronJob's args contain `mentions`.
- **Logs** carry counts at info level and ids at debug level.

### The walk switched on

- `youtube_backfill_enabled: true` in `deploy/group_vars/all/main.yml`, with its comment.
- `test_main_yml_leaves_the_walk_off` becomes `…turns_the_walk_on`.
- The setting's default in the code stays `False`.

Timeline, from the walk of 2026-11-01 07:00 Moscow:

| When | What |
|---|---|
| the deploy | `backfill-status` says enabled; every seed is walked already, so the CronJob makes no request until a seed is added |
| 2026-11-27 05:00 | the refresh takes the walked year for the first time: about 154 units at once, then 6–12 a day |
| 2026-11-29 | the deploy's deadline, one spare run ahead |
| 2026-12-01 05:00 | the last refresh before the walk's payloads expire at about 07:00 |
| later than that | walks forgotten, payloads gone; the next day's CronJob walks every seed again, about 430 units |

### Documentation

The wording is proposed, and the operator words both pages before archiving.

- **`docs/index.html`, "What it does with YouTube":**
  - "Past uploads" keeps its first and last sentences: up to a year listed, titles and descriptions read, no counts. What they are read for moves to a new item:
    > **Mentions.** From the titles and descriptions of those channels' uploads, new and past, itgraph works out which other channels and videos they mention or link to — to map how the channels refer to one another, and to find channels to review. It works this out when it is needed and keeps no copy.
  - The status panel's month, if the operator wants it.
- **`docs/privacy.html`:**
  - "How long it is kept", the first item, gains:
    > What itgraph works out from them — which channels a video mentions or links to — is not stored.
  - "Last updated".
- **`docs/PLAN.md`:**
  - **"The graph is thin":**
    - what `brandPartner` is, replacing "a documented reference";
    - the measured shape of references;
    - bare mentions read against the inventory;
  - **"Policy before engineering":**
    - what the amendment covers: storage and the listed metrics, not III.E.2;
    - PageRank and clusters out for YouTube;
    - the ordering as the operator's reading;
  - **"The history walk…":** switched on with change 4;
  - **open threads for change 5:** a `brandPartner` probe, a sampled probe of shortened links, Telegram links as a purpose not stated;
  - **"Order of changes":** change 4's row.
- **`src/itgraph/README.md` (Russian):**
  - `yt mentions`;
  - what "not followed" counts;
  - why it is never scheduled;
  - the switch turned on.
- **`src/itgraph/CLAUDE.md`:** rows for `derive/yt_references.py` and `db/yt_references.py`.
- **Root `CLAUDE.md`,** the YouTube exception: after "no copied titles or handles elsewhere", add "and what is read out of them, such as which channels a video mentions, is worked out when needed and never stored".

## Risks / Trade-offs

- [Mentions of channels outside the inventory are lost] → Counted in every report. The closed set grows with every review, and the share is re-measured after change 5.
- [A renamed handle in an old description] → An unknown handle, pending. When YouTube releases old handles is undocumented, so change 5 may resolve one to someone else; the review catches it.
- [A gone channel's handle] → Its payload is gone, so links to its handle turn pending. Links to its id remain edges.
- [Parsing again on every reading] → Seconds at 40 thousand videos. If a reader needs more, the stored layout under "Read on demand" applies.
- [The report's output holds API data] → Standard output of a manual command, never scheduled, and the deploy test holds it.
- [The ordering is read as aggregation across owners] → Nothing is stored or shown beyond the count. Dropping the sort changes no table.
- [The deploy slips past 2026-11-29] → A re-walk of about 430 units and a day without history.
- [One- and two-character Han and Hangul handles are refused] → None in the data; the grammar is one constant.
- [The unreconciled count of non-ASCII mentions] → Nothing depends on it; the parser is tested on non-Latin text.

## Migration Plan

1. No schema revision. Nothing new for `db/backup.py`.
2. Deploy with `youtube_backfill_enabled: true` by 2026-11-29.
3. **First run, by the operator:**
   - `scripts/on-host.sh yt backfill-status`: enabled, 105 walked;
   - `scripts/on-host.sh yt mentions`: compared with the measured table, roughly, since the parser is stricter than the measuring patterns. Expect edges from about 84 handle links, 8 mentions and 14 video links to other seeds; about 93 unknown channel targets plus the legacy names; about 1,047 unknown videos.
4. On 2026-11-27 or after: `yt quota` shows the refresh's burst, and `backfill-status` the year still there.

Rollback: switch off and deploy. Walks are then forgotten 30 days after they ended, and the command can be removed with no schema to undo.

## Assumptions

Confirmed by the operator on 2026-11-01 (explore, questions 1–13 and the measurements):
- references worked out on reading and never stored;
- sources: seeds' videos, titles and descriptions, new and past; channel descriptions not read;
- channel links (`/@`, `/channel/`, `/user/`) and video links followed; `/c/`, vanity URLs, playlist pages, shorteners and Telegram links counted only;
- bare `@` against the inventory only;
- no inventory record created here; legacy names always pending;
- one reference per target per video; self-references dropped;
- ordering by distinct seeds as triage; no PageRank, clusters or weights for YouTube;
- `brandPartner` not read here, a probe in change 5;
- the walk switched on in this change's deploy; a re-walk if it is late;
- the site's wording changed for new uploads; no notice under III.D.3.

Not covered by the requirements, decided in this design and open to veto:
- one handle length for every script;
- `lower()` over `casefold()`;
- NFC before matching;
- the precedence `id link` > `handle link` > `mention`;
- the report's layout, `--min-seeds` 1 and `--limit` 50;
- the shortener list;
- the proposed site wording.

Not covered by the requirements, open to measurement:
- whether a mention chosen from YouTube's list keeps its `@handle` text after the channel renames itself;
- whether bare mentions outside the inventory turn out to be YouTube channels once the inventory has grown.

## Open Questions

None that change the specs or the tasks. The `brandPartner` probe and the sampled probe of shortened links belong to change 5's explore.
