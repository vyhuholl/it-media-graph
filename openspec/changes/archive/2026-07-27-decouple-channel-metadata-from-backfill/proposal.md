## Why

A backfill run over the ~200 seed channels halts on a day-long FloodWait before the history walk gets anywhere. The `flood_events` table names the method: `ResolveUsernameRequest`.

The cause is that the history walk cannot start without a peer, and the only path to one currently runs through `fetch_full_channel`, which calls `get_entity(username)`. Telethon's `get_entity` deliberately ignores its own cache for usernames — it issues `contacts.resolveUsername` every single time, and its docstring warns that flood waits begin "around 50 usernames in a short period". So every channel whose stored extended information is absent or stale costs two quota-bearing requests before a single message is fetched: one `resolveUsername` and one `channels.getFullChannel`. On a first pass every channel is stale, so that is ~400 quota-bearing requests spent to learn descriptions and linked chats that change on the order of months — and the quota runs out long before the ~4000 cheap `messages.getHistory` calls that are the actual point of the run.

The fix is already half-present. `_resolve_peer` knows to take the peer from `get_input_entity` when metadata is fresh, and that path costs nothing: it is answered from the session file's entity cache. What is missing is the recognition that the history walk never needed the metadata pass at all. `GetFullChannelRequest` answers two questions — what the description says and which discussion chat belongs to the channel — and neither is an input to walking history. Once the peer comes from the cache unconditionally, the metadata pass stops being a precondition and becomes an independent, separately-budgeted concern.

Doing this now, rather than alongside the other rate-limit work, is deliberate: it is the only one of the candidate fixes that is pure subtraction. It removes requests without adding a subsystem, a dependency, a table or a new failure mode.

## What Changes

- `fetch_full_channel` takes a peer rather than a username, and no longer calls `get_entity`. The channel's id, title and username come from the `chats` the `GetFullChannelRequest` response already carries, so nothing is lost by not resolving.
- The history walk obtains its peer from the session's entity cache unconditionally, not only when stored metadata is fresh. Walking a channel costs zero quota-bearing requests.
- The metadata pass is decoupled from the walk and gets its own budget, so a run that exhausts the metadata quota still collects history, and a history run is never blocked behind a metadata request. **The placement — a separate CLI command versus a separately-limited phase inside `backfill` — is the open question for `design.md`; a separate command is the current recommendation.**
- A cold entity cache — a channel the session has never seen — becomes an explicit, budgeted path rather than a silent `resolveUsername`. Spending the scarcest request in the project must be a decision the operator can see and bound, not a fallback that fires 200 times.
- **BREAKING** for the operator's habits, not for data: `itgraph backfill` no longer refreshes channel metadata as a side effect. Descriptions and linked chats go stale until the metadata pass is run. Nothing already stored changes, and no migration is required.

Out of scope, and deliberately so:

- Harvesting the `chats` array from history responses to answer the id-resolution queue for free. Measured first: that queue holds 2 rows against 277 pending mentions, so it would save two requests.
- `inputChannelFromMessage` batched through `channels.getChannels`. It addresses the id queue, which is not where the quota goes, and after this change it would be optimising an almost-empty queue.
- The 277-row pending-mention backlog in `itgraph resolve`. `contacts.resolveUsername` has no batch form and no cheap substitute; that queue is a separate problem with a separate answer, and this change does not pretend to touch it.
- Reading channels through the public `t.me/s/` web preview. A different collection mechanism with a different risk profile, and — measured against this problem — it would grow the mention queue rather than shrink it.

## Capabilities

### New Capabilities

None. This changes how an existing capability spends requests, not what the system can do.

### Modified Capabilities

- `message-backfill`: the **Channel Metadata Pass** requirement currently makes the extended-information fetch a step of walking a channel, with the cached peer as an optimisation for the fresh case. It becomes an independent pass with its own budget, and the history walk's peer always comes from the cache. The scenarios "A stale payload is refreshed" and "A channel never seen before is fetched" no longer describe something backfill does. A new requirement states the invariant that earns the change: a history walk issues no request that carries a daily quota.

## Impact

- `src/itgraph/tg/full_channel.py` — signature takes a peer; no `get_entity`; identity read from the response's `chats`.
- `src/itgraph/tg/backfill.py` — `_resolve_peer` collapses to the cached path; the metadata branch and the `refresh_metadata` flag move out with the pass.
- `src/itgraph/cli.py` — a new command if the design lands on one; `backfill`'s `--refresh-metadata` flag moves or goes.
- `src/itgraph/db/raw.py`, `db/channels.py` — unchanged; `store_channel_payload` and `link_discussion_chat` are called from the new location as-is.
- `tests/test_full_channel.py`, `tests/test_backfill.py`, `tests/test_cli.py` — the fakes must start asserting that no `resolveUsername` is issued during a walk, which is the behaviour this change exists to guarantee.
- One Alembic migration, and only one: `CollectionCommand` is a native Postgres enum, and the metadata pass needs its own value so its rate limits do not file under `backfill`. No table, column or stored payload changes shape. No new dependency.
- `docs/PLAN.md` records FloodWait handling as a backfill concern but says nothing about which methods carry quotas; worth a line once this settles.
