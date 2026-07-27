## 1. Schema: a third collection command

- [x] 1.1 Add `METADATA = "metadata"` to `CollectionCommand` in `db/models.py`, and rewrite its docstring — it currently says both commands share `contacts.resolveUsername`, which is exactly what this change makes false
- [x] 1.2 Generate the Alembic revision and hand-edit it down to `ALTER TYPE collection_command ADD VALUE 'metadata'` and nothing else: Postgres refuses to *use* a new enum value in the transaction that added it, so the revision must not write a row with it
- [x] 1.3 Make the downgrade an explicit no-op with a comment saying why — Postgres has no `ALTER TYPE ... DROP VALUE`, and an unused value strands nothing
- [x] 1.4 Verify the revision on a scratch database whose name ends in `_test`, never the working one; read `alembic upgrade --sql` before running anything

## 2. The metadata fetch stops resolving a username

- [x] 2.1 `tg/full_channel.py`: `fetch_full_channel` takes an input peer instead of a username, and no longer calls `client.get_entity`
- [x] 2.2 Read the channel's own id, title and username out of `result.chats` — the entry matching `result.full_chat.id`, the way `_discussion_chat` already reads the linked chat out of the same list
- [x] 2.3 `ChannelMetadata` drops `entity` and carries the identity instead; update every caller
- [x] 2.4 Rewrite the module docstring: its explanation of why resolution is by username, and why the session file is worth keeping, is half wrong after 2.1
- [x] 2.5 `tests/fakes.py`: `FakeTelegramClient.__call__` keys on `request.channel.id`, but an input peer carries `channel_id` — fix the fake to match the shape production now sends
- [x] 2.6 `tests/test_full_channel.py`: assert the pass resolves no username (`client.resolved == []`) and that identity comes from the response

## 3. The history walk stops fetching metadata

- [x] 3.1 `tg/backfill.py`: collapse `_resolve_peer` to a session-cache lookup — delete the freshness check, the `fetch_full_channel` branch and the fallback. **Corrected during implementation:** `client.get_input_entity` was the wrong seam — on a miss it falls through to `contacts.resolveUsername` itself, so the quota is spent before any error is raised. The lookup goes to `client.session.get_input_entity`, which stops at the cache. Renamed `cached_peer`, and it needs no pacing because it makes no network call
- [x] 3.2 Catch the cache miss where it happens and call `record_skip(session, tg_id, "no cached peer")`. **This one is a trap:** `get_input_entity` raises a bare `ValueError`, `classify` files a bare `ValueError` as `PERMANENT`, and a permanent failure drops the channel out of `channels_in_scope` for good — so a miss that reaches the per-channel handler silently retires the channel
- [x] 3.3 Drop `refresh_metadata` from `backfill_channel` and `backfill_channels`
- [x] 3.4 Drop `--refresh-metadata` from the `backfill` CLI command; its meaning moves to the new command in 4.3
- [x] 3.5 Update the module docstrings in `tg/backfill.py` — the walk no longer has a metadata branch to explain
- [x] 3.6 Tests: a channel with no stored metadata is walked with no `get_entity` and no request; a cache miss lands in `skipped` rather than `failed`, and the channel is still in scope on the next run. Also moved where a walk discovers an unreachable channel — the metadata request used to be the reachability probe, so `FakeTelegramClient` now raises from `iter_messages` (globally via `raises`, per-channel via the new `raises_for`), which is where Telegram would say it

## 4. The metadata pass becomes its own command

- [x] 4.1 A set-based query for in-scope channels whose stored metadata is absent or past the freshness window — `metadata_age` answers one channel at a time, which is the wrong shape for a pass
- [x] 4.2 New `tg/metadata.py`: the pass itself — one channel at a time, `pace` before each request, through `waiting_out_floods`, with a `FloodRecorder` on `CollectionCommand.METADATA`, a `--limit` budget, and a summary carrying `halt` the way `ResolveSummary` does
- [x] 4.3 `cli.py`: `itgraph metadata` with `--limit`, `--delay` and the refresh flag inherited from `backfill`; body short, logic in the module
- [x] 4.4 Add the `tg/metadata.py` row to the module table in `src/itgraph/CLAUDE.md`
- [x] 4.5 Tests: bounded by `--limit`; fresh channels are skipped; no username is resolved; a halt stops the pass and still reports what was committed
- [x] 4.6 Test the isolation the whole change is for: a metadata pass halted by a rate limit leaves a following backfill able to walk every in-scope channel

## 5. Stale metadata is reported, not fetched

- [x] 5.1 Count in-scope channels waiting on the pass, read before the walk starts, the way `count_deferred_chats` already is
- [x] 5.2 `RunSummary` carries the count and `line()` prints it in its own clause, so an operator who never runs `itgraph metadata` is told on every backfill
- [x] 5.3 Test: the count is correct and costs no request

## 6. Close out

- [x] 6.1 `make validate` green — lint, mypy, pytest
- [x] 6.2 `docs/PLAN.md`: record which methods carry daily quotas and which command is allowed to spend them; the plan currently treats FloodWait as one undifferentiated backfill concern
- [x] 6.3 `openspec validate decouple-channel-metadata-from-backfill --type change` green
