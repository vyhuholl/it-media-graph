# CLAUDE.md

Analytics over the IT-media graph: who reposts, mentions and comments on whom. Telegram first, later YouTube / Threads / Instagram.

Roadmap and phase breakdown live in `docs/PLAN.md`. Read it before proposing architectural changes — most "obvious improvements" are already scheduled for a later phase, or were deliberately rejected.

## Hard rules

1. **Never run Python directly.** Always `uv run <cmd>` — no bare `python`, no `pip`, no activating a venv. Dependencies change via `uv add` / `uv add --dev`, never by hand-editing `pyproject.toml`.
2. **Run `make validate` after every change that touches code.** Fix what it reports before declaring the task done. Never report success on a red validate.
3. **Never commit secrets or personal data.** See "Never commit" below. If it looks like a secret has to live in a file, stop and ask.

## Commands

```bash
make lint          # ruff check --fix + ruff format
make lint-check    # non-mutating variant, used by CI
make typecheck     # mypy src/
make test          # pytest
make validate      # lint + typecheck + test   <- after every change

uv run alembic revision --autogenerate -m "..."
uv run alembic upgrade head
uv run itgraph --help    # CLI entrypoint (typer)
```

## Architecture invariants

- **Collection is MTProto (Telethon), from a user account.** The Bot API cannot read channel history, so aiogram is never an option for collection. aiogram is used only for the alert bot that sends notifications to the user.
- **The raw layer is immutable.** The collector writes raw payloads to `raw_messages` and does nothing else. All parsing, enrichment and metrics derive from it and must be re-runnable from scratch. Never move parsing logic into the collector — re-fetching history is expensive and risks the account.
- **Postgres only.** No graph database. Graph work loads edges into `igraph`/`networkx` in memory.
- **Schema changes only through an Alembic migration.** Never edit tables directly.

## Telegram collection rules

- Handle `FloodWaitError` with honest backoff. Never work around a limit by switching sessions or accounts — that is what escalates to a ban.
- **Never join channels.** Public channels are read by username via `get_entity` + `iter_messages`. Mass joining is the single strongest ban signal.
- The collector must be resumable: persist `offset_id` per channel, so an interrupted backfill continues instead of restarting.

## Testing

- **No network in tests.** Telethon is mocked; fixtures are saved, anonymized payloads under `tests/fixtures/`.
- Real `api_id` / `api_hash` / session files never appear in tests or in CI. Do not add them to GitHub Secrets "just to test the collector for real".
- Migrations are exercised in CI against a throwaway Postgres — keep them working.

## Never commit

- `.env`, `*.session`, `*.session-journal` — a session file is full account access
- any dump of the user's own subscriptions or real collected channel data
- commenter user IDs or any other personal data; store hashed, keep out of git

Test fixtures are synthetic or anonymized. There is no such thing as a "small real sample, just for a test".

## Workflow

- Standard / infrastructure tasks (collector, schema, API, bot): write an OpenSpec change proposal first, then implement.
- Exploratory analytics (clustering, edge weights, engagement baselines): no spec. Iterate in `notebooks/`, promote to `src/` only once the approach is settled.

## Style

- ruff and mypy settings are deliberate. Do not loosen config or sprinkle `# type: ignore` to make an error disappear — fix the code, or ask.
- Code, identifiers and comments in English. Docs and commit messages may be Russian.