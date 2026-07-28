## Why

`pending_mentions` holds 2888 usernames. Each one costs a `contacts.resolveUsername`, which has no batch form and a daily quota of roughly two hundred, so the queue is about two weeks of `itgraph resolve --limit 200`. Nothing shortens that — the quota counts calls, and there is no cheaper substitute inside MTProto or outside it.

But the queue is not 2888 things worth resolving. Counting distinct source channels per pending username over the raw layer (110 140 messages, the project's own parser, read-only) gives:

| mentioned by | usernames |
|---|---|
| 1 channel | 2513 (87%) |
| 2 channels | 240 |
| 3 channels | 64 |
| 4+ channels | 68 (most, 24 channels) |

Everything mentioned by at least two distinct channels is **372 usernames — under two days of quota**. The remaining 2513 are single references: one channel linked something once. In a graph of who reposts and mentions whom, those resolve into vertices of degree one, which is the least informative thing the inventory can spend its scarcest request on.

The ordering today is `first_seen_at, username` — arrival order. So the 372 that carry the graph are scattered through 2888 that mostly do not, and two days of budget buys an arbitrary 13% slice instead of the part that matters. That is the whole problem: not the queue's length, its order.

The count is not stored anywhere. `pending_mentions` records a username, when it was first seen, and how resolution went — not who mentioned it. Answering "how many channels mention this" currently means re-parsing the entire raw layer, which is why this has to become a stored fact before it can become an ordering.

## What Changes

- A new derived table recording **which channels** mention each pending username — one row per `(username, source channel)` pair, not a counter. A counter cannot survive `derive` being re-runnable: a second pass over unchanged data must write nothing, and an increment always writes. A pair inserted with `ON CONFLICT DO NOTHING` is idempotent by construction, and the count is a `COUNT(DISTINCT)` away.
- `derive` records the pair it already has in hand. The source channel id is in scope at the point a username is queued; nothing new is parsed and no extra pass is made.
- `itgraph resolve` works the mention queue **most-mentioned first**, falling back to arrival order for ties so a bounded run stays deterministic.
- `itgraph resolve --min-sources N` bounds the queue by evidence rather than by count: a way to say "work the head and leave the tail" without counting rows by hand.
- `--rebuild` and resolution both clear the new rows with the pending mention they belong to, so the table cannot outlive what it describes.
- **A username whose channel already exists is never requested.** Recording the sources exposed 365 pending rows — 13% of the queue — whose channel is already in the inventory, resolved by id from a forward. Resolving them would spend 365 `contacts.resolveUsername` calls, most of two days' quota, to re-learn channels the inventory has. The queue skips them, and resolution by id now removes the pending row it makes redundant, so no more accumulate.

Out of scope:

- The channels-by-id queue. It resolves through the session's cached `access_hash` and spends no quota-bearing request, so its order costs nothing.
- Weighting a mention by the mentioning channel's status or size. Every source of a pending mention is a `seed` today — the raw layer only holds channels backfill walked, and backfill walks seeds only — so there is nothing yet to weight *by*. Revisit when the corpus holds more than seeds.
- Dropping the tail. A username mentioned once today may be mentioned twice after the next backfill; the tail is low priority, not noise to delete.

## Capabilities

### New Capabilities

None. This adds a stored fact and an ordering to an existing capability.

### Modified Capabilities

- `forward-graph`: **Mention Edges** gains the requirement that a pending mention records which channel mentioned it — currently the username is queued and the source discarded. **Reference Resolution** gains the ordering: the mention queue is worked by weight of evidence rather than by arrival, and can be bounded by it.

## Impact

- New table + Alembic revision. One table, one foreign key onto `pending_mentions` with `ON DELETE CASCADE`, no change to any existing column.
- `src/itgraph/db/models.py` — the new model.
- `src/itgraph/db/edges.py` — `add_pending_mentions` takes pairs; `pending_mentions_to_resolve` orders and filters by source count; `truncate_derived` names the new table explicitly rather than relying on `CASCADE`.
- `src/itgraph/derive/edges.py` — collect `(source, username)` instead of `username`.
- `src/itgraph/tg/resolve.py`, `src/itgraph/cli.py` — the `--min-sources` option, passed through.
- Tests: `test_derive.py`, `test_resolve.py`, `test_cli.py`.
- **Existing rows carry no sources until `derive` runs again.** The table starts empty, so every pending username looks equally unmentioned until then. A plain `itgraph derive` refills it from the raw layer — no `--rebuild`, no re-fetching.
- `README.md` — the resolve section, which currently describes the queue as arrival-ordered.
