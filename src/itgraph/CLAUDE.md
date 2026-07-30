# src/itgraph
Collection and storage layer for the IT-media channel graph: the Telegram client, raw ingestion, database models, and the CLI that drives them.

Module map and local conventions. Project-wide rules live in the root `CLAUDE.md`.

## Layout
| Path | Owns |
|---|---|
| `config.py` | pydantic-settings; the single `settings` object |
| `cli.py` | typer app — thin command wrappers only |
| `usernames.py` | Operator-supplied entries → usernames; refuses what cannot be looked up, before anything is spent |
| `tg/client.py` | Telethon client lifecycle; the only place a `TelegramClient` is built |
| `tg/dialogs.py` | The account's own subscriptions → inventory rows |
| `tg/manual.py` | Channels named by hand → inventory rows; resolves, never joins |
| `tg/payload.py` | Telethon objects → JSON-safe payloads; the only place that touches payload shape |
| `tg/pacing.py` | How long to wait before a request; the only random source in the project |
| `tg/floods.py` | Which method a rate limit named, and writing it down without endangering the run |
| `tg/full_channel.py` | One channel's extended information, and the linked chat it resolves |
| `tg/metadata.py` | The metadata pass: which channels are due extended information, on its own quota budget |
| `tg/backfill.py` | The history walk: pacing, resumption, FloodWait, failure classification. Spends no quota-bearing request |
| `tg/resolve.py` | The resolution pass: username and title for channels found by reference |
| `tg/` | MTProto collection: fetch and store raw payloads |
| `derive/references.py` | Pure parsing of a payload into the channels it references |
| `derive/edges.py` | The derivation pass: raw messages → edges; touches no network |
| `derive/` | Deriving graph data from the raw layer |
| `db/session.py` | `Database`: engine + session factory |
| `db/models.py` | SQLAlchemy models, `Base` |
| `db/channels.py` | The channel inventory: upsert, review, listing, resolution state |
| `db/edges.py` | The derived tables: `edges`, `pending_mentions` and the sources that order them |
| `db/raw.py` | Writes into the raw layer; nothing here reads a payload |
| `db/floods.py` | The record of rate limits: one row per wait, and the two questions asked of it |
| `db/backfill.py` | Which channels to walk, how far each got, and why one stopped |
| `db/backup.py` | Dumps, the schedule that picks which, and the pruning |
| `db/guard.py` | Refuses a destructive migration against a non-scratch database |
| `db/migrations/` | Alembic revisions (async template) |

## Conventions
- Read settings via `from itgraph.config import settings`. Nothing else touches `os.environ`.
- New CLI command: argument parsing in `cli.py`, logic in its own module. Keep command bodies short enough to read at a glance.
- DB sessions come from the factory in `db/`; no module-level engine, no globals.
- Anything that parses, derives or aggregates lives outside `tg/`.
- `logging` for diagnostics, typer's `echo` for user-facing CLI output. No `print`.
- New subpackage → create an empty `__init__.py` and add a row to the table above.