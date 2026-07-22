# src/itgraph
Collection and storage layer for the IT-media channel graph: the Telegram client,
raw ingestion, database models, and the CLI that drives them.

Module map and local conventions. Project-wide rules live in the root `CLAUDE.md`.

## Layout
| Path | Owns |
|---|---|
| `config.py` | pydantic-settings; the single `settings` object |
| `cli.py` | typer app — thin command wrappers only |
| `tg/client.py` | Telethon client lifecycle; the only place a `TelegramClient` is built |
| `tg/dialogs.py` | The account's own subscriptions → inventory rows |
| `tg/payload.py` | Telethon objects → JSON-safe payloads; the only place that touches payload shape |
| `tg/full_channel.py` | The per-channel metadata pass, and the linked chat it resolves |
| `tg/backfill.py` | The history walk: pacing, resumption, FloodWait, failure classification |
| `tg/` | MTProto collection: fetch and store raw payloads |
| `db/session.py` | `Database`: engine + session factory |
| `db/models.py` | SQLAlchemy models, `Base` |
| `db/channels.py` | The channel inventory: upsert, review, listing |
| `db/raw.py` | Writes into the raw layer; nothing here reads a payload |
| `db/backfill.py` | Which channels to walk, how far each got, and why one stopped |
| `db/backup.py` | Dumps, the schedule that picks which, and the pruning |
| `db/guard.py` | Refuses a destructive migration against a non-scratch database |
| `db/migrations/` | Alembic revisions (async template) |

## Conventions
- Read settings via `from itgraph.config import settings`. Nothing else touches
  `os.environ`.
- New CLI command: argument parsing in `cli.py`, logic in its own module. Keep
  command bodies short enough to read at a glance.
- DB sessions come from the factory in `db/`; no module-level engine, no globals.
- Anything that parses, derives or aggregates lives outside `tg/`.
- `logging` for diagnostics, typer's `echo` for user-facing CLI output. No `print`.
- New subpackage → create an empty `__init__.py` and add a row to the table above.