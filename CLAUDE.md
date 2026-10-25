# CLAUDE.md
Analytics over the IT-media graph: who reposts, mentions and comments on whom. Telegram first, later YouTube / Threads / Instagram.

Roadmap and phase breakdown live in `docs/PLAN.md`. Read it before proposing architectural changes — most "obvious improvements" are already scheduled for a later phase, or were deliberately rejected.

## Hard rules
1. **Never run project code via Python directly.** Always `uv run <cmd>` — no bare `python`, no `pip`, no activating a venv. Dependencies change via `uv add` / `uv add --dev`, never by hand-editing `pyproject.toml`. Ansible tooling is also run via `uv run`.
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
- **YouTube's raw layer is the exception, and the policy is why.** Everything read with the API key is Non-Authorized Data, kept at most 30 calendar days (YouTube API Services Developer Policies III.E.4.b, III.E.4.d). A payload in `yt_raw_*` is overwritten in place by a refresh, deleted once 30 days pass without one, and deleted when YouTube stops returning the object; a statistics reading is appended, never updated, and deleted 30 days after it was taken. API data lives only in `yt_raw_*` — no copied titles or handles elsewhere, none of it in backups — so expiring it stays one `DELETE`. The rest of the invariant holds: the collector only stores payloads, and everything derived is re-runnable from them. The reason does not carry over: re-fetching fifty channels costs one unit, so on YouTube the expensive mistake is keeping data, not losing it.
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
- the **vault password** (`deploy/.vault_pass`), or `deploy/group_vars/all/secrets.vault.yml` in plaintext. The vault is committed encrypted, and the repository is public: the password is all that stands between anyone and the bot token, the api hash and the proxy endpoint, and rotating it does not re-protect the ciphertext already in history. Pre-commit refuses both, so a hook failure there is never fixed by skipping the hook
- the **YouTube API key** (`YOUTUBE_API_KEY`), on the same footing as the api hash. Whoever holds it spends the quota of the one API project the policies allow (III.D.1.c) — a second project to escape that is not an option, any more than a second Telegram account is
- **review files** from `itgraph yt review-export` (`*.review.tsv`). They hold YouTube API data that has to be gone within 30 days, so they live outside the repository and are deleted once applied
- any dump of the user's own subscriptions or real collected channel data
- commenter user IDs or any other personal data; store hashed, keep out of git

Test fixtures are synthetic or anonymized. There is no such thing as a "small real sample, just for a test".

- The operator's own private dialogs, work chats and legacy group chats are out of scope. Only publicly addressable channels enter the inventory; history is fetched only for channels with status `seed` or `accepted`.
- Nothing derived from the operator's own subscriptions is publishable. Exports select an explicit column list filtered to `status = 'seed'`; never `SELECT *`, and never include `discovered_via`, `reject_reason`, `reject_note` or `kind_note`.

## Requirements discipline
A gap or a contradiction in the requirements is **a question for the user**, never a guess in the code. A guess pinned down by a green test is worse than no code at all: it looks like a solution.

Before starting on a feature:

1. Ask about everything that admits more than one reading — and wait for the answers.
2. List the assumptions you made explicitly. Mark any assumption the user has not confirmed as **"not covered by the requirements"** in the artifacts.

When the prompt and the actual behaviour of a library or of the code disagree, do not pick a side silently. Check the sources, show the evidence, and record the user's decision.

## Workflow
- Standard / infrastructure tasks (collector, schema, API, bot): implement via OpenSpec: `/opsx:explore` -> write change artifacts -> `/opsx:apply` -> `/opsx:verify` -> `/opsx:archive`.
- Exploratory analytics (clustering, edge weights, engagement baselines): no spec. Iterate in `notebooks/`, promote to `src/` only once the approach is settled.
- **Commit to `master`. Never create a branch unless asked for one.** One person works on this repository, so a branch buys no review and costs a merge on every change. If something is risky enough to want isolating, say so and ask — do not decide it by branching.

## Style
- ruff and mypy settings are deliberate. Do not loosen config or sprinkle `# type: ignore` to make an error disappear — fix the code, or ask.
- Code, identifiers and comments in English. Docs and commit messages may be Russian.