## 1. Schema

- [x] 1.1 `db/models.py`: `AlertKind` enum with `REPOST_CASCADE`. Declare only what exists — the rate kinds belong to the change that implements them, and an enum value nothing can produce is a promise in a type
- [x] 1.2 `db/models.py`: `Alert` — `id`, `kind`, `channel_id`, `msg_id`, `band`, `value`, `raised_at`, `delivered_at`, `delivery`, `attempts`, `last_error`. Composite FK onto `raw_messages`, as `MessageMetric` has: an alert about a post nothing collected is not a thing that should be representable
- [x] 1.3 `UniqueConstraint("kind", "channel_id", "msg_id", "band")`. **This constraint is the escalation logic** — do not add a counter or a "already told them" flag beside it. A post at band 2 raises one row, at band 3 a second, and standing still raises nothing, because the tuple is the same. Say so in the docstring, because the next reader's instinct will be to add bookkeeping
- [x] 1.4 Index on `delivered_at` where null, or a partial index — the bot's one access path is "what is outstanding", and it asks on every tick forever
- [x] 1.5 `db/models.py`: `AlertFeedback` — `alert_id` primary key and FK, `verdict` enum, `given_at`. Keyed by alert rather than appending, so a changed mind replaces rather than accumulates; an unanswered alert has no row, which is not the same as a neutral verdict
- [x] 1.6 Alembic revision for both tables and both enums. New enum *types*, not new values on an existing one, so the `ADD VALUE` transaction trap does not apply here — but read the `watch` revision before assuming that
- [x] 1.7 A second revision granting the bot role: `SELECT` on what rendering reads, `INSERT`/`UPDATE` on the two alert tables, nothing on `channels`, `raw_messages`, `message_metrics`, `edges`, `backfill_state`, `poll_state`. **The revision must not create the role's password.** A password in a migration is a committed secret, which is the one thing this project refuses outright; the revision grants to a role name and the operator creates the role. Postgres has no `CREATE ROLE IF NOT EXISTS`, so guard it in a `DO` block or document creation as a prerequisite and let the grant fail loudly
- [x] 1.8 Verify both revisions on a scratch database whose name ends in `_test`; read `alembic upgrade --sql` first. The working database takes a full dump before the upgrade

## 2. Detecting a cascade

- [x] 2.1 `alerts/cascade.py`: pure function — edge rows plus a family mapping in, posts that crossed a band out. No database, no clock of its own; every input passed in, as `affiliation/signals.py` does
- [x] 2.2 Count **distinct families**, not reposts. One family carrying a post five times is one, which is the whole difference between measuring travel and measuring enthusiasm
- [x] 2.3 Exclude the publishing channel's own family — a network distributing itself is not a cascade. Same exclusion `notebooks/export_graph.py` and `notebooks/anomalous_posts.py` already apply
- [x] 2.4 Collapse album parts to one post, keyed on `(channel_id, grouped_id)` and never on `grouped_id` alone. The id carried forward is the first part's, which is what a `t.me` link to an album resolves to. **Note the asymmetry:** `edges.grouped_id` belongs to the *referencing* message, so it is not the column to group on here — the album being alerted about is the referenced post, and its grouping comes from `raw_messages`
- [x] 2.5 Ignore an edge whose repost predates the post it refers to. Clock skew and bad `dst_published_at` both produce these, and a negative age silently passes any window test
- [x] 2.6 `alerts/run.py`: the pass — load the window, detect, insert `ON CONFLICT DO NOTHING`, report. The load-detect-store-report shell `affiliation/run.py` already has
- [x] 2.7 Report the age of the newest edge considered. An alerting system whose healthy state is silence must make "nothing happened" distinguishable from "nothing was derived", and this is the cheap half of that
- [x] 2.8 `tests/test_cascade.py`: distinct families; one family repeatedly is one; own family excluded; albums are one alert; window boundaries both ways; an edge predating its post; a first run over a year of old edges raises nothing **without** any record of what was already handled — the window has to do that structurally

## 3. The queue

- [x] 3.1 `db/alerts.py`: raise alerts in a batch, `ON CONFLICT DO NOTHING`, returning how many were new
- [x] 3.2 `pg_notify` after the insert, in the same transaction — Postgres delivers at commit, so a notification can never arrive before the row it announces
- [x] 3.3 Claim outstanding alerts with `SELECT ... FOR UPDATE SKIP LOCKED`. Deliberately not a process lease: the resource is an outbound API that can be called from anywhere, so what has to be prevented is one *row* being sent twice, not two processes existing
- [x] 3.4 **Do not hold the transaction open across the send.** Claim and commit, send, then mark delivered and commit. Wrapping an HTTP call in a row lock is the obvious shape and the wrong one; the honest trade is at-least-once delivery with a one-message duplicate window if the process dies between sending and recording, because Telegram offers no idempotency key that could buy exactly-once
- [x] 3.5 A failed send increments `attempts` and records `last_error`, leaving `delivered_at` null so the next tick retries. Back off a row that keeps failing rather than retrying it at full rate
- [x] 3.6 Cap accounting: how many alerts were sent directly in the current period. Read from the rows — `delivered_at` and `delivery` already say it — rather than kept in a counter that could disagree with them
- [x] 3.7 Record and read feedback, keyed by alert so a changed verdict replaces the earlier one
- [x] 3.8 `tests/test_alerts_db.py`: dedup by the constraint; the same post at a higher band is a second row; two concurrent claimers never take one row; a failed send stays outstanding; the cap is counted from rows

## 4. The bot

- [x] 4.1 `bot/render.py`: an alert → a message. Post link, channel, how old the post is, which families carried it, and how many. **One message per post, never one per reposter.** Pure, so it is testable without aiogram
- [x] 4.2 Evidence is read at render time from `edges`, not from the alert row. The digest read in the morning will therefore show fresher numbers than the moment the alert fired — that is intended, and the docstring should say so before someone "fixes" it
- [x] 4.3 `bot/app.py`: the aiogram application, the configured recipient, and the two loops — wake on notification, and poll on an interval regardless. **The poll is the correctness mechanism and the notification is the latency optimisation.** `NOTIFY` is not durable: a bot that was down never learns. Write that in the module docstring, because the poll looks like redundancy to tidy away
- [x] 4.4 Ignore any message from a chat that is not the configured one, before any handler runs
- [x] 4.5 `bot/handlers.py`: the feedback buttons and `/status`. No mute — that is volume management, and designing suppression against one alert a day means any rule looks right because nothing tests it
- [x] 4.6 `/status`: when the pass last ran, alerts raised, outstanding, repeatedly failing, and how stale the derived edges are. This is what makes a quiet bot distinguishable from a broken one, which matters more here than anywhere else in the project
- [x] 4.7 The digest: everything held by the cap or by quiet hours, sent at the configured hour, stating how many it covers. A digest covering nothing is not sent
- [x] 4.8 Quiet hours for the bot. Generalize `schedule.in_quiet_hours` to take a window rather than reading the collector's settings directly, and keep the collector's call site unchanged. They mean different things — one is about not making requests, the other about not making noise — and they coincide today only because both are the operator's night
- [x] 4.9 `tests/test_bot_render.py` and `tests/test_bot.py`, with aiogram mocked and no network. A fake bot token spelled out in words, never plausible-looking hex — `tests/` is not excluded from the gitleaks hook, and a realistic fake trips it for good reason

## 5. CLI and configuration

- [x] 5.1 `config.py`: bot token as `SecretStr`, operator chat id, bands, window, direct cap, digest hour, bot quiet hours, poll interval. Defaults for bands and window taken from the measurement — `(2, 3)` and 6 hours — with the measured rates in the comment, so the next reader can tell a chosen number from an invented one
- [x] 5.2 `cli.py`: `itgraph alerts` — run the detection pass once, report what it raised and how stale its evidence was. Takes no session lease
- [x] 5.3 `cli.py`: `itgraph bot` — run until stopped, SIGINT/SIGTERM handled as `watch` does. Takes no session lease, and a test should assert that it runs while the lease is held
- [x] 5.4 `pyproject.toml`: `[dependency-groups] bot = ["aiogram"]` via `uv add --group bot aiogram`, never by hand-editing
- [x] 5.5 `tests/test_cli.py`: the pass reports what it raised; the bot starts while the session lease is held; neither command connects to Telegram

## 6. Documentation

- [x] 6.1 Root `CLAUDE.md`: the bot token joins the never-commit list, beside `.env` and the session files
- [x] 6.2 `src/itgraph/CLAUDE.md`: rows for `alerts/cascade.py`, `alerts/run.py`, `db/alerts.py`, `bot/app.py`, `bot/render.py`, `bot/handlers.py`, plus the two subpackage lines
- [x] 6.3 `src/itgraph/README.md`: running the pass and the bot, creating the bot's database role, and — stated plainly — that alerts are only as fresh as the last `itgraph derive`, which is 11 seconds and therefore a schedule rather than a feature
- [x] 6.4 Record the expected volume in the README: about one alert a day until the scoring change lands. An operator who does not know that will conclude the bot is broken in week one
- [x] 6.5 `docs/PLAN.md`: the measured cascade rates beside the claim that post-level virality is the realtime product — it says which half of that claim carries the weight

## 7. Close out

- [x] 7.1 `make validate` green — lint, mypy, pytest, coverage at or above the floor
- [x] 7.2 `openspec validate add-alert-bot` green