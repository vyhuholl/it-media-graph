# src/itgraph
Collection and storage layer for the IT-media channel graph: the Telegram client, raw ingestion, database models, and the CLI that drives them.

Local conventions. Project-wide rules live in the root `AGENTS.md`.

## Conventions
- Read settings via `from itgraph.config import settings`. Nothing else reads `os.environ` as configuration — building a child process's environment, as `db/backup.py` does for the PostgreSQL clients it spawns, is plumbing, not settings.
- New CLI command: argument parsing in `cli.py`, logic in its own module. Keep command bodies short enough to read at a glance.
- DB sessions come from the factory in `db/`; no module-level engine, no globals.
- Anything that parses, derives or aggregates lives outside `tg/`.
- `logging` for diagnostics, typer's `echo` for user-facing CLI output. No `print`.
- New subpackage → create an empty `__init__.py`.