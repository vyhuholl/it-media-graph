# CLAUDE.md
Analytics over the IT-media graph: who reposts, mentions and comments on whom. Telegram first, later YouTube / Threads / Instagram.

Roadmap and phase breakdown live in `docs/PLAN.md`. Read it before proposing architectural changes — most "obvious improvements" are already scheduled for a later phase, or were deliberately rejected.

## Hard rules
1. **Never run Python directly.** Always `uv run <cmd>` — no bare `python`, no `pip`, no activating a venv. Dependencies change via `uv add` / `uv add --dev`, never by hand-editing `pyproject.toml`. Ansible tooling is also run via `uv run`.
2. **Run `make validate` after every change that touches code.** Fix what it reports before declaring the task done. Never report success on a red validate.
3. **Never commit secrets or personal data.** See "Never commit" below. If it looks like a secret has to live in a file, stop and ask.

## Commands
```bash
make lint          # ruff check --fix + ruff format
make typecheck     # mypy src/
make test          # pytest
make ansible-lint  # ansible-lint over deploy/
make validate      # lint + typecheck + test + ansible-lint   <- after every change

uv run alembic revision --autogenerate -m "..."
uv run alembic upgrade head
uv run itgraph --help    # CLI entrypoint (typer)
```

## Architecture invariants
- **Collection is MTProto (Telethon), from a user account.** The Bot API cannot read channel history, so aiogram is never an option for collection. aiogram is used only for the alert bot that sends notifications to the user.
- **The raw layer is immutable.** The collector writes raw payloads to `raw_messages` and does nothing else. All parsing, enrichment and metrics derive from it and must be re-runnable from scratch. Never move parsing logic into the collector — re-fetching history is expensive and risks the account.
- **Postgres only.** No graph database. Graph work loads edges into `igraph`/`networkx` in memory.
- **Schema changes only through an Alembic migration.** Never edit tables directly.
- **The inventory is backed up, and the backups are read back.** `db/backup.py` dumps the hand-reviewed tables often and the whole database weekly, into `~/itgraph-backups` — outside the repository, because a dump carries the operator's own subscriptions. Every dump is verified with `pg_restore --list` before it counts, and pruning only ever follows a good one. An `alembic upgrade` on the working database takes a full dump first. Restoring is exercised, not assumed: see `src/itgraph/README.md`.
- **A migration is verified on a scratch database, never on the working one.** `alembic downgrade` drops tables, and the URL comes from the environment, so the safe command and the destructive one are keystroke-identical. `db/guard.py` refuses a downgrade unless the database name ends in `_test` — point `DATABASE_URL` at a scratch database, or use `alembic downgrade --sql` to read what it would do. The `ITGRAPH_ALLOW_DESTRUCTIVE=1` override exists for deliberate use after a backup; reaching for it to make an error go away is the mistake it is named after.

## Telegram collection rules
- Handle `FloodWaitError` with honest backoff. Never work around a limit by switching sessions or accounts — that is what escalates to a ban.
- **Never join channels.** Public channels are read by username via `get_entity` + `iter_messages`. Mass joining is the single strongest ban signal.
- The collector must be resumable: persist `offset_id` per channel, so an interrupted backfill continues instead of restarting.

## Testing
- **No network in tests.** Telethon is mocked; fixtures are saved, anonymized payloads under `tests/fixtures/`.
- Real `api_id` / `api_hash` / session files never appear in tests. There is no CI to add them to, and adding one to run the collector "for real" is not a reason to bring it back.
- Fake credentials are spelled out in words (`test-api-hash`, `fake-…`), never as plausible-looking hex. `tests/` is deliberately **not** excluded from the gitleaks hook, so a realistic fake trips it — and the fix is to make the value obviously fake, not to add an exclude, a `# gitleaks:allow`, or a fingerprint allowlist. A fake that looks real also costs a human reviewer the ability to tell the difference.
- Tests use separate databases on the same Postgres instance, created and dropped by a fixture in `conftest.py`. That fixture refuses to run against a database whose name does not end in `_test` — never weaken the check, per-worker names included.
- `make test` runs eight workers (`pytest-xdist`), one database each (`itgraph_gw0_test`, …). The schema is built once per run and the tables are emptied with `TRUNCATE` between tests — building it per test was two thirds of the suite's wall clock. **Nothing in the suite may alter the schema**, because it now outlives the test that changed it; a test that needs its own schema has to build its own database. `make test WORKERS=0` runs in one process, which is how a failure is read.
- **There is no CI.** One person works on this repository, so pre-commit is the whole gate: ruff and ansible-lint on commit, mypy and pytest on push. Nothing runs after a push — a hook skipped with `--no-verify` is not caught anywhere later.

## Never commit
- `.env`, `*.session`, `*.session-journal` — a session file is full account access
- the Telegram **bot token**. Unlike the api hash it plausibly ends up on a machine you do not own, which is why the bot also gets its own database role: the role is what bounds the damage when the token leaks, and a token in a migration or a fixture defeats both
- the **proxy password** (`PROXY_PASSWORD`), on the same footing as the api hash. It buys someone else a shared exit billed to the operator, and — worse — an address the collector's traffic is known to come from
- any dump of the user's own subscriptions or real collected channel data
- commenter user IDs or any other personal data; store hashed, keep out of git

Test fixtures are synthetic or anonymized. There is no such thing as a "small real sample, just for a test".

- The operator's own private dialogs, work chats and legacy group chats are out of scope. Only publicly addressable channels enter the inventory; history is fetched only for channels with status `seed` or `accepted`.
- Nothing derived from the operator's own subscriptions is publishable. Exports select an explicit column list filtered to `status = 'seed'`; never `SELECT *`, and never include `discovered_via`, `reject_reason`, `reject_note` or `kind_note`.


## Workflow
- Standard / infrastructure tasks (collector, schema, API, bot): write an OpenSpec change proposal first, then implement.
- Exploratory analytics (clustering, edge weights, engagement baselines): no spec. Iterate in `notebooks/`, promote to `src/` only once the approach is settled.
- **Commit to `master`. Never create a branch unless asked for one.** One person works on this repository, so a branch buys no review and costs a merge on every change. If something is risky enough to want isolating, say so and ask — do not decide it by branching.

## Style
- ruff and mypy settings are deliberate. Do not loosen config or sprinkle `# type: ignore` to make an error disappear — fix the code, or ask.
- Code, identifiers and comments in English. Docs and commit messages may be Russian.