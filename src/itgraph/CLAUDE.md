# src/itgraph
Collection and storage layer for the IT-media channel graph: the Telegram client, raw ingestion, database models, and the CLI that drives them.

Module map and local conventions. Project-wide rules live in the root `CLAUDE.md`.

## Layout
| Path | Owns |
|---|---|
| `config.py` | pydantic-settings; the single `settings` object |
| `cli.py` | typer app — thin command wrappers only |
| `usernames.py` | Operator-supplied entries → usernames; refuses what cannot be looked up, before anything is spent |
| `yt_review.py` | The review file: rendered for a text editor, read back strictly — every mistake at once with its line, the export moment and the 30-day expiry in the header; a read-only `source` column last — the discovery source, and a search's query — never a count |
| `yt_entries.py` | The same door for YouTube: pasted handles, links and ids → the three lookups `channels.list` can make; `/c/`, vanity and video links refused together, before anything is spent |
| `schedule.py` | When a post is read again and a channel looked at; pure arithmetic. A missed sample is skipped, never replayed |
| `yt_policy.py` | What the YouTube Developer Policies fix, as constants: the 30-day retention. Not settings — they are not ours to raise |
| `yt_quota.py` | The YouTube quota day (Pacific date, 23 or 25 hours twice a year), its start and reset, and the caps of the general and search pools; pure arithmetic |
| `yt_schedule.py` | When a YouTube video is read again and a channel listed: age from a broadcast's actual start, upcoming broadcasts checked not read, the pool spread across the quota day, listing backoff; pure. Shares `schedule`'s rule, not its numbers |
| `tg/client.py` | Telethon client lifecycle; the only place a `TelegramClient` is built |
| `tg/dialogs.py` | The account's own subscriptions → inventory rows |
| `tg/manual.py` | Channels named by hand → inventory rows; resolves, never joins |
| `tg/payload.py` | Telethon objects → JSON-safe payloads; the only place that touches payload shape, and the one rule for what names a person, removed before anything is stored. Its copy in SQL is in the revision `fb31bd0b6656`; `tests/people_cases.py` holds them, and the restore's read-back, equal |
| `tg/pacing.py` | How long to wait before a request; the only random source in the project |
| `tg/floods.py` | Which method a rate limit named, and writing it down without endangering the run |
| `tg/full_channel.py` | One channel's extended information, and the linked chat it resolves |
| `tg/metadata.py` | The metadata pass: which channels are due extended information, on its own quota budget |
| `tg/backfill.py` | The history walk: pacing, resumption, FloodWait, failure classification. Spends no quota-bearing request. Also holds `waiting_out_floods`, the one seam every request in the project passes through — and so the one place a request deadline lives |
| `tg/resolve.py` | The resolution pass: username and title for channels found by reference |
| `tg/watch.py` | The poll loop: new posts and fresh counters from one request, forever. Spends no quota-bearing request; absorbs a rate limit and a lost connection rather than exiting, and exits only when it has stopped making progress at all |
| `tg/` | MTProto collection: fetch and store raw payloads |
| `yt/errors.py` | YouTube failures a caller names without importing the HTTP client |
| `yt/client.py` | The only `httpx.AsyncClient`. Key in a header, never a URL; every request booked in the ledger before it is sent; what each answer means. Each method's price and Google bucket (`search.list` its own); a method whose bucket is not its command's pool's is refused unbooked; `quotaExceeded` ends that bucket's day only. `request_urls_unlogged` keeps httpx's per-request URL out of the log for a command whose URLs carry names or queries |
| `yt/channels.py` | `channels.list` by ids (fifty a call; a 404 batch asked again id by id), by handle, by legacy name; the parts and `fields` requested — exactly the privacy policy's list. A lookup's handle or legacy name asks for the id alone |
| `yt/uploads.py` | `playlistItems.list`: a channel's newest fifty uploads for the poll loop, and page after page for the history walk with the next token and no item count; ids and dates only; a playlist not found or not accessible is that channel's failure |
| `yt/videos.py` | `videos.list` by ids, fifty a call whatever the channels; a 404 batch asked again id by id; the parts and `fields` the privacy policy lists. `OWNER` asks a linked video for its id and channel id alone |
| `yt/lookups.py` | Which channel a linked name belongs to: videos fifty a call, a handle or legacy name one a call, each the channel id alone; `None` is "YouTube named none". Names in, ids out — nothing Telegram's, so the mentions' lookups ask the same questions |
| `yt/search.py` | One `search.list` page, a unit of the search bucket: 50 results, English relevance, videos bounded by `publishedAfter` or channels; each result's channel id and the next token, nothing else — an unreadable one kept as `None` so positions stay YouTube's |
| `yt/search_pass.py` | The search pass: each query skipped while a run of it is in date unless forced; a forced video search asks since that run; pages committed one by one, the run written with its first; the search pool alone; logs run ids and counts, httpx's URLs (which carry the query) kept out |
| `yt/sections.py` | `channelSections.list` for one channel, a unit a call: the channel ids each section lists and nothing else — not the type, which the API spells unlike its reference. 404 and `channelSectionForbidden` are an empty list, so one channel cannot stop a refresh |
| `yt/lookup_pass.py` | Looking up names in the order given — videos batched in that order, a handle or legacy name one a call — each answer committed before the next request; general pool, no floor, `--limit`; the report every lookup command prints. Logs counts only, httpx's URLs kept out |
| `yt/tg_lookup.py` | The Telegram lookup pass: every name the Telegram seeds link that nothing stored answers, most families first, through `yt/lookup_pass.py`. No Telegram session |
| `yt/mention_lookup.py` | The mentions' lookup pass: every handle, legacy name and video seeds' videos reference that nothing stored answers, most distinct seeds first, through `yt/lookup_pass.py`. Channel ids and featured ids are never looked up |
| `yt/add.py` | The import pass: entries → ids → records, unreviewed. What the inventory already answers costs nothing; every call committed before the next, so the same input resumes |
| `yt/refresh.py` | The refresh pass: every payload due, fifty a call, whatever the status; gone and back; then every seed's featured list due, a unit a seed; then past uploads; progress committed per call. Logs ids only |
| `yt/watch.py` | One pass of the YouTube poll loop, under its lock: every seed asked for, uploads listed when the video count moved or the daily check is due, due videos read fifty a request; a moved count is a debt until its videos are stored; a spent pool ends a pass quietly. Logs ids only |
| `yt/backfill.py` | The history walk, under its lock: each seed's uploads paged back to a depth, stopping on the page that crosses it; the newest-first order counted rather than trusted; what the poll loop owns left to it, a broadcast yet to start handed to it; payloads without counters until acceptance; each page committed, no page token kept; the general pool spent down to a floor. Logs ids only |
| `yt/` | YouTube Data API collection: fetch channels, uploads and videos, and store them |
| `derive/references.py` | Pure parsing of a payload — or of plain text — into the channels it references. Its UTF-16 slice is shared with `derive/tg_youtube.py` |
| `derive/edges.py` | The derivation pass: raw messages → edges; touches no network |
| `derive/metrics.py` | Pure reading of a payload into the four counters; absent is never zero |
| `derive/yt_videos.py` | Pure reading of a YouTube video payload: the facts the schedule runs on, and the three counters a reading records; absent is never zero, and no format is inferred |
| `derive/yt_references.py` | Pure reading of a YouTube video's title and description: channel and video links followed, custom URLs, playlists, shorteners and Telegram counted, bare `@` only against the inventory's handles; then edges and pending targets, one per target per video, no self-references — and a seed's featured list as references of the seed itself, form `featured`, no video, seeds linking and featuring kept apart in the evidence; then the report's lines, ordered by distinct seeds — the only number worked out per target. YouTube's handle grammar, not Telegram's — `derive/references.py` would read `@fake.seed.two` as `fake`. Its one-link reader, `read_link`, is shared with `derive/tg_youtube.py` and returns a custom URL's name, which `yt mentions` never shows — out of a description it is API data. Nothing it returns is ever stored |
| `derive/yt_mention_candidates.py` | Pure reading of the mention queue: each video-level reference named through what is stored and the lookups in date — a channel, none, or awaiting; one naming its own channel dropped; distinct seeds per channel; what awaits, most seeds first; the ranking outside the inventory (seeds, then id) and the report's lines, channel ids only. Featured lists are not evidence here. Nothing it returns is ever stored |
| `derive/yt_search.py` | Pure parts of search: queries read one a line (`#` only at a line's start, so "C#" is a query), trimmed, collapsed, each once; the search queue — channels without a record, round-robin across runs in YouTube's order, no count — and the report's lines, channel ids and queries only. Nothing it returns is ever stored |
| `derive/yt_featured.py` | Pure reading of a seed's featured list — every section's channel ids, type unread, each once, shape-checked; then the featured queue: distinct seeds per channel, own channel dropped, the ranking outside the inventory (seeds, then id) and the report's lines, channel ids only. Nothing it returns is ever stored |
| `derive/tg_youtube.py` | Pure reading of the Telegram seeds' YouTube links: a post's marked URLs, URLs behind text and preview URL only — never plain text, a preview's metadata or `@name` — by `derive/yt_references.read_link`; forwarded posts skipped; then each YouTube channel's distinct seed families against what is stored (inventory, payloads, lookups in date); the ranking outside the inventory and the report, which shows no API data. `PREFILTER` is the superset the loader filters on in SQL. Nothing it returns is ever stored |
| `derive/` | Deriving graph data from the raw layer |
| `affiliation/signals.py` | The five signals that suggest two channels share an author; pure functions over mappings. Holds a second, deliberately looser handle pattern — it may not be pointed at `derive/references.py`, and the docstring says why |
| `affiliation/detect.py` | Merging the signals into one ranked candidate list; the parameters and their validation |
| `affiliation/run.py` | The detection pass: load, score, store, report. Touches no network |
| `affiliation/` | Recognizing that several channels have one author. Proposes; never decides |
| `alerts/cascade.py` | Which posts are travelling: distinct unaffiliated families, pure functions over edge rows |
| `alerts/run.py` | The detection pass: load, detect, store, report. Touches no network and no snapshots |
| `alerts/` | Deciding that something is worth telling the operator. Writes the queue; sends nothing |
| `scoring/curves.py` | How a metric accrues with a post's age, the factors that join a curve to history, and a channel's own reference from its newest posts; for comments, a mean with the zeros kept and a curve as a ratio of sums; pure |
| `scoring/score.py` | What a post should have reached by now, and how far past it went; pure. Comments go to `counts.py`, and only where the run measured their dispersion |
| `scoring/counts.py` | Comments as counts: the negative binomial's upper tail by the regularised incomplete beta, its z with a ±40 ceiling, and the dispersion fitted by likelihood; pure, no numpy or scipy |
| `scoring/fold.py` | What counts as one thing: a burst of messages is one post, a channel's spikes inside a window are one event; pure |
| `scoring/refresh.py` | The baseline refresh: fit, store, publish as one run. A refresh replaces rather than accumulates. Comments are fitted as counts: no mature fallback, a ratio-of-sums curve, and a dispersion per kind by likelihood beside the spread |
| `scoring/run.py` | The scoring pass: load, score, raise, report. Replay is this same pass with an earlier moment. A comment count under `alert_spike_comments_min` is set aside before the post's best is chosen; a run without a comment dispersion is reported as not scoring comments |
| `scoring/` | Recognizing that a post is doing unusually well for its channel and its age |
| `bot/render.py` | An alert as a message a person reads, Telegram's and YouTube's; pure, so the wording is testable. Everything from a post, channel or video escaped; every alert under Telegram's 4,096; a digest in parts that each fit. A YouTube alert names YouTube, dates YouTube's counts and itgraph's score apart, and says the score is itgraph's |
| `bot/destinations.py` | Where alerts go: a chat, and a topic or none, built from settings alone; each with its own cap, quiet window and digest. Pure |
| `bot/queues.py` | The two queues behind the one interface delivery needs: which rows a destination claims, how an alert reads, what is excluded. Telegram's one chat; YouTube's topic per route |
| `bot/app.py` | Delivery, the same for every queue and destination: claim, send, mark; hold what quiet hours or the cap set aside, so it never blocks what comes after; the digest in parts. The poll is correctness |
| `bot/status.py` | What `/status` says, per platform: the chat is told about its own |
| `bot/handlers.py` | The aiogram binding — the only module that imports aiogram or holds the token. Answers only in configured chats, in any topic; refuses at start a chat it cannot reach; answers and strips the old feedback buttons |
| `bot/` | Telling the operator. Bot API only: no session, no lease, no collection writes |
| `metrics/series.py` | What the shared monitoring reads, worked out at scrape time from what is recorded: never a counter in another process, never the big tables, never API data; label values are constants and enums alone. The alert series per platform |
| `metrics/serve.py` | The exporter's listener: `GET /metrics` on 9090 and nothing else, with no framework; 503 when the database does not answer |
| `metrics/` | Telling the shared monitoring how collection is doing. Read-only, under a role that can read nothing else |
| `db/session.py` | `Database`: engine + session factory |
| `db/models.py` | SQLAlchemy models, `Base` |
| `db/channels.py` | The channel inventory: upsert, review, listing, resolution state, and the only place a family is confirmed, rejected or withdrawn |
| `db/affiliation.py` | The affiliation tables: detection runs, candidate pairs and their evidence; reads families out of the `channel_families` view |
| `db/views.py` | Views. `channel_families` — which channels share an author, as the connected components of the confirmed pairs |
| `db/edges.py` | The derived tables: `edges`, `pending_mentions` and the sources that order them |
| `db/raw.py` | Writes into the raw layer; nothing here reads a payload |
| `db/metrics.py` | Writes into the snapshot layer: append-only readings of a post's counters |
| `db/alerts.py` | Telegram's alert queue: raising, claiming, holding, delivering. A held alert is the digest's alone; one that failed is claimed after those never tried. A comment spike keeps its count and the expectation it was scored against |
| `db/yt_alerts.py` | YouTube's alert queue: claiming per route through the channel's current label, excluding what no route takes, holding, delivering; the evidence a message reads, refused past 30 days. Raises nothing — scoring will — and copies nothing YouTube said onto an alert |
| `db/baselines.py` | The baseline tables: what a post of a given age is expected to reach, and the run it was measured in. Comment readings keep their zeros and are read as posts are scored, an album by its first part and a burst by its first message; the comment dispersion is stored per kind |
| `db/poll.py` | The poll queue: which channel is due, and when it is due again. Timing only |
| `db/floods.py` | The record of rate limits: one row per wait, and the two questions asked of it |
| `db/yt_channels.py` | The YouTube inventory: records (the operator's, never deleted) and payloads (YouTube's, 30 days at most); creation, expiry, gone and back, what is due for refresh, review, listing |
| `db/yt_videos.py` | The poll loop's videos: payloads with their schedule, readings appended beside them, what is due, forgetting a video whole, expiry — 30 days, or 36 months for readings once acceptance is recorded in `yt_policy` |
| `db/yt_watch.py` | The poll loop's own records: listings owed, made and failed, one row per pass, and the lock — an advisory `bigint` key that never meets Telegram's session lease |
| `db/yt_references.py` | The mention graph read out of the stored payloads in one `REPEATABLE READ` transaction: seeds' videos and seeds' featured lists in date as sources, every payload's handle, every record's id and every stored video's channel as what is known. Reads and writes nothing — a handle from a description lives only in its payload, so the graph is never stored |
| `db/yt_telegram_links.py` | The Telegram seeds' YouTube links read in one `REPEATABLE READ` transaction: own posts of seeds that are not chats, streamed through `PREFILTER`, with their families from `channel_families`; the totals over every own post; and what names a channel without a request — the inventory, stored payloads' handles, stored videos, lookups in date. Reads and writes nothing — which families link a channel is never stored |
| `db/yt_lookups.py` | The lookups: which channel YouTube said a linked name belongs to, found or not, keyed by `YtLookupKind` and name; replaced, never added to; read only while in date, removed at 30 days by the shared expiry. Never refreshed, never dumped |
| `db/yt_search.py` | Searches: the operator's queries (kept, dumped daily), runs (no API data, not dumped, 30 days) and results (API data, 30 days); whether a query is due read from the runs in date, never from the query's dates, so a restore cannot hide a lost answer |
| `db/yt_sections.py` | The seeds' featured lists in `yt_raw_channel_sections`: stored or replaced per seed, the seeds due (no list, or older than the refresh age), the lists in date of seeds not gone, how many seeds lack one, expiry at 30 days. Never dumped |
| `db/yt_backfill.py` | The history walk's own records: who is due, what each walk counted (pages, videos, items out of order, undated, not returned), forgetting a walk whose videos nothing keeps, and the lock — a `bigint` key of its own that meets neither the poll loop's nor Telegram's lease |
| `db/yt_quota.py` | The YouTube quota ledger: booking before sending, in a transaction that commits first; the pool a command spends from, by table — the poll loop its reserve, search its own pool, everything else the general pool, no borrowing; the bucket each pool is carved from; the journal; exhaustion per bucket and day; the day's report. Units are never returned |
| `db/backfill.py` | Which channels to walk, how far each got, and why one stopped |
| `db/backup.py` | Dumps, the schedule that picks which, and the pruning — full dumps by age (four weeks, never fewer than four), because the pre-migration dump shares their tier; the PostgreSQL clients run through `docker exec` into the Compose container or, without one, from the image against `DATABASE_URL` |
| `db/offsite.py` | The off-site pass: every verified dump, `age`-encrypted on the host, uploaded to B2's native API under a name that is never reused; a transient failure tried again at a new upload URL, with backoff; a copy counts only once the store confirms the checksum. Logs names, never keys |
| `db/session_lease.py` | The exclusive claim on the session file, as a Postgres advisory lock |
| `db/guard.py` | Refuses a destructive migration against a non-scratch database |
| `db/migrations/` | Alembic revisions (async template) |

## Conventions
- Read settings via `from itgraph.config import settings`. Nothing else reads `os.environ` as configuration — building a child process's environment, as `db/backup.py` does for the PostgreSQL clients it spawns, is plumbing, not settings.
- New CLI command: argument parsing in `cli.py`, logic in its own module. Keep command bodies short enough to read at a glance.
- DB sessions come from the factory in `db/`; no module-level engine, no globals.
- Anything that parses, derives or aggregates lives outside `tg/`.
- `logging` for diagnostics, typer's `echo` for user-facing CLI output. No `print`.
- New subpackage → create an empty `__init__.py` and add a row to the table above.