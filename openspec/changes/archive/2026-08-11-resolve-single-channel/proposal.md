## Why

`itgraph resolve` can be bounded but not aimed. The by-id queue is `resolved_at IS NULL` ordered by `tg_id`, so `--limit 1` resolves the *smallest* id awaiting resolution, never the one the operator is looking at. There is no way to spend exactly one request on exactly one channel.

That is the shape of two ordinary situations:

- **A failure that is provisional.** Resolving a bare id needs an `access_hash` the session may not hold yet, so a failure is a cache miss, not a verdict — a later `backfill` can make the same id resolvable. Today the only way to retry it is `--retry-failed`, which unblocks *every* past failure at once — in both queues, including the one that spends `contacts.resolveUsername` — and then works them in id order. Checking whether one channel became resolvable means a run that spends its requests on everything ahead of it first.
- **A channel the operator is looking at right now.** A bare id showing up in the graph, in a report, in `itgraph show` — the question "what is this channel" is one request, and the command that spends it cannot be asked.

The by-id queue costs no rationed request, so this is not about quota. It is about being able to answer a question about one channel without running a pass over the inventory.

## What Changes

- `itgraph resolve` gains an optional positional argument `TG_ID`. Given one, the run resolves that channel and nothing else: one request, and the mention queue is not touched at all.
- **The argument narrows the queue; it does not redefine it.** A named id must be a channel that is in the inventory and awaiting resolution. An id the inventory does not hold is refused before connecting to Telegram; a channel already resolved is refused as having nothing to do; a channel that failed before is refused unless `--retry-failed` is given — the same rule that governs the queue when it is worked whole.
- `--limit` and `--min-sources` are refused alongside `TG_ID`: one bounds a run that is already exactly one request, the other orders a queue that is not being worked. `--delay` and `--retry-failed` stay meaningful and are accepted.
- The summary line and the FloodWait halt behave as they do now — a run of one is still a run.

Out of scope:

- **Resolving an id the inventory does not hold.** That would make `resolve` a third door into the inventory beside `dump-dialogs` and `add`, and a typo'd id would create a channel row rather than an error. Adding channels is `add`'s job.
- **Addressing the channel by `@username`.** A username lookup is `contacts.resolveUsername` — the rationed request — and putting it behind the same argument would make a one-character difference in the argument the difference between a free request and a quota-bearing one. This argument takes an id.
- **Aiming the mention queue.** A pending username is already addressable by evidence through `--min-sources`, and the queue's order is the whole point of how it is worked.

## Capabilities

### New Capabilities

None. This narrows an existing command to one target.

### Modified Capabilities

- `forward-graph`: **Reference Resolution** gains the requirement that the by-id queue can be narrowed to a single named channel, and states what a named id that is not in the queue does.

## Impact

- `src/itgraph/tg/resolve.py` — `resolve_inventory` takes `tg_id`; when set, the mention queue is skipped and the by-id queue is one row.
- `src/itgraph/db/channels.py` — `channels_awaiting_resolution` grows a `tg_id` filter, so the queue's own rules (`resolved_at IS NULL`, `retry_failed`) decide whether the named channel is in it. No second code path answering the same question differently.
- `src/itgraph/cli.py` — the `TG_ID` argument, the two option conflicts, and the not-in-queue refusals.
- Tests: `test_resolve.py`, `test_cli.py`.
- `src/itgraph/README.md` — the `resolve` section's option table, and a line on what the argument is for.
- No schema change, no migration, no new request type. Default behaviour with no argument is unchanged.
