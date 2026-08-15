# src/itgraph
Collection and storage layer for the IT-media channel graph: the Telegram client, raw ingestion, database models, and the CLI that drives them.

Module map and local conventions. Project-wide rules live in the root `CLAUDE.md`.

## Layout
| Path | Owns |
|---|---|
| `config.py` | pydantic-settings; the single `settings` object |
| `cli.py` | typer app — thin command wrappers only |
| `usernames.py` | Operator-supplied entries → usernames; refuses what cannot be looked up, before anything is spent |
| `schedule.py` | When a post is read again and a channel looked at; pure arithmetic. A missed sample is skipped, never replayed |
| `tg/client.py` | Telethon client lifecycle; the only place a `TelegramClient` is built |
| `tg/dialogs.py` | The account's own subscriptions → inventory rows |
| `tg/manual.py` | Channels named by hand → inventory rows; resolves, never joins |
| `tg/payload.py` | Telethon objects → JSON-safe payloads; the only place that touches payload shape |
| `tg/pacing.py` | How long to wait before a request; the only random source in the project |
| `tg/floods.py` | Which method a rate limit named, and writing it down without endangering the run |
| `tg/full_channel.py` | One channel's extended information, and the linked chat it resolves |
| `tg/metadata.py` | The metadata pass: which channels are due extended information, on its own quota budget |
| `tg/backfill.py` | The history walk: pacing, resumption, FloodWait, failure classification. Spends no quota-bearing request. Also holds `waiting_out_floods`, the one seam every request in the project passes through — and so the one place a request deadline lives |
| `tg/resolve.py` | The resolution pass: username and title for channels found by reference |
| `tg/watch.py` | The poll loop: new posts and fresh counters from one request, forever. Spends no quota-bearing request; absorbs a rate limit and a lost connection rather than exiting, and exits only when it has stopped making progress at all |
| `tg/` | MTProto collection: fetch and store raw payloads |
| `derive/references.py` | Pure parsing of a payload — or of plain text — into the channels it references |
| `derive/edges.py` | The derivation pass: raw messages → edges; touches no network |
| `derive/metrics.py` | Pure reading of a payload into the four counters; absent is never zero |
| `derive/` | Deriving graph data from the raw layer |
| `affiliation/signals.py` | The five signals that suggest two channels share an author; pure functions over mappings. Holds a second, deliberately looser handle pattern — it may not be pointed at `derive/references.py`, and the docstring says why |
| `affiliation/detect.py` | Merging the signals into one ranked candidate list; the parameters and their validation |
| `affiliation/run.py` | The detection pass: load, score, store, report. Touches no network |
| `affiliation/` | Recognizing that several channels have one author. Proposes; never decides |
| `alerts/cascade.py` | Which posts are travelling: distinct unaffiliated families, pure functions over edge rows |
| `alerts/run.py` | The detection pass: load, detect, store, report. Touches no network and no snapshots |
| `alerts/` | Deciding that something is worth telling the operator. Writes the queue; sends nothing |
| `scoring/curves.py` | How a metric accrues with a post's age, and the two factors that join a curve to history; pure |
| `scoring/score.py` | What a post should have reached by now, and how far past it went; pure |
| `scoring/refresh.py` | The baseline refresh: fit, store, publish as one run. A refresh replaces rather than accumulates |
| `scoring/run.py` | The scoring pass: load, score, raise, report. Replay is this same pass with an earlier moment |
| `scoring/` | Recognizing that a post is doing unusually well for its channel and its age |
| `bot/render.py` | An alert as a message a person reads; pure, so the wording is testable |
| `bot/app.py` | Delivery: claim, send, mark. The poll is correctness, the notification is speed |
| `bot/handlers.py` | The aiogram binding — the only module that imports aiogram or holds the token |
| `bot/` | Telling the operator. Bot API only: no session, no lease, no collection writes |
| `db/session.py` | `Database`: engine + session factory |
| `db/models.py` | SQLAlchemy models, `Base` |
| `db/channels.py` | The channel inventory: upsert, review, listing, resolution state, and the only place a family is confirmed, rejected or withdrawn |
| `db/affiliation.py` | The affiliation tables: detection runs, candidate pairs and their evidence; reads families out of the `channel_families` view |
| `db/views.py` | Views. `channel_families` — which channels share an author, as the connected components of the confirmed pairs |
| `db/edges.py` | The derived tables: `edges`, `pending_mentions` and the sources that order them |
| `db/raw.py` | Writes into the raw layer; nothing here reads a payload |
| `db/metrics.py` | Writes into the snapshot layer: append-only readings of a post's counters |
| `db/alerts.py` | The alert queue: raising, claiming, delivering, and what the operator thought |
| `db/baselines.py` | The baseline tables: what a post of a given age is expected to reach, and the run it was measured in |
| `db/poll.py` | The poll queue: which channel is due, and when it is due again. Timing only |
| `db/floods.py` | The record of rate limits: one row per wait, and the two questions asked of it |
| `db/backfill.py` | Which channels to walk, how far each got, and why one stopped |
| `db/backup.py` | Dumps, the schedule that picks which, and the pruning |
| `db/session_lease.py` | The exclusive claim on the session file, as a Postgres advisory lock |
| `db/guard.py` | Refuses a destructive migration against a non-scratch database |
| `db/migrations/` | Alembic revisions (async template) |

## Conventions
- Read settings via `from itgraph.config import settings`. Nothing else touches `os.environ`.
- New CLI command: argument parsing in `cli.py`, logic in its own module. Keep command bodies short enough to read at a glance.
- DB sessions come from the factory in `db/`; no module-level engine, no globals.
- Anything that parses, derives or aggregates lives outside `tg/`.
- `logging` for diagnostics, typer's `echo` for user-facing CLI output. No `print`.
- New subpackage → create an empty `__init__.py` and add a row to the table above.