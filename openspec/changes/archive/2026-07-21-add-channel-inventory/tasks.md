## 1. Configuration

- [x] 1.1 Add `config.py` with pydantic-settings: `DATABASE_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH`. Export a single `settings` instance.
- [x] 1.2 Extend `.env.example` with the new keys, placeholder values only.

## 2. Storage

- [x] 2.1 Define the four enums and the `channels` model in `db/models.py`, including the `rejected_has_reason` check constraint.
- [x] 2.2 Add the async session factory in `db/session.py`.
- [x] 2.3 Initialise Alembic from the async template, generate and review the first migration. Verify both `upgrade head` and `downgrade base` run clean.
- [x] 2.4 Add the upsert helper: refresh username and title on conflict, leave provenance and every review field untouched.

## 3. Telegram client

- [x] 3.1 Add `tg/client.py`: build a `TelegramClient` from settings, connect using the existing session, expose it as an async context manager.
- [x] 3.2 Exit non-zero with a pointer to the README when the session is missing or unauthorized. Never prompt for phone, code or password.

## 4. Subscription import

- [x] 4.1 Add `tg/dialogs.py`: iterate dialogs, keep broadcast channels and groups, map each entity to upsert arguments with `discovered_via='own_subscriptions'`.
- [x] 4.2 Wire `itgraph dump-dialogs` in `cli.py`, reporting inserted and updated counts.

## 5. Review commands

- [x] 5.1 Add `itgraph mark <tg_id>` with `--seed [--kind]`, `--maybe`, `--reject --reason [--note]`. Sets `reviewed_at`; fails on an unknown id and on a rejection with no reason.
- [x] 5.2 Add `itgraph channels` with a `--status` filter, and a per-status count summary when no filter is given.

## 6. Tests

- [x] 6.1 Add `conftest.py`: session-scoped test database, created and dropped, with the guard that refuses any database whose name does not end in `_test`.
- [x] 6.2 Add anonymized dialog fixtures under `tests/fixtures/`. No real channels.
- [x] 6.3 Test the import: first run populates the inventory; a second run over a reviewed inventory refreshes titles while leaving status, kind, reason and `reviewed_at` untouched; a channel no longer in the dialog list is retained.
- [x] 6.4 Test `mark`: every outcome, an unknown id, a rejection without a reason, and that the check constraint refuses a bad write.

## 7. Wrap-up

- [x] 7.1 Run `make validate` and fix everything it reports. Do not close the change while validate is red.
- [x] 7.2 Update `README.md` **in Russian**: the one-off Telegram authorization bootstrap, starting Postgres via docker compose, applying migrations, and the three new commands with a short example of the review loop.