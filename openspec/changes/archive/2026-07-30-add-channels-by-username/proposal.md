## Why

The inventory has two doors and neither one opens by hand. `dump-dialogs` imports what the collecting account is subscribed to; derivation discovers channels by reference and `resolve` turns those handles into identities. There is no way to say "this channel, by name, I already know I want it".

So the only route today is to subscribe from a Telegram client and run `dump-dialogs`. That is the wrong shape in three separate ways:

- It spends a `channels.joinChannel` per channel — the action this project's own collection rules name as the strongest ban signal there is, and the one thing `dump-dialogs` was careful never to do.
- **The join buys nothing.** Opening a channel by username in any client already costs a `contacts.resolveUsername`; the join is added on top of a request that was going to be spent anyway. Subscribing is strictly more expensive than resolving, not a way around the cost.
- It drags in the whole public dialog list of whichever account did the subscribing. Adding 100 channels from a personal account means importing every public channel that account follows, and each one lands as a `candidate` to be reviewed by hand.

The immediate case is around 100 channels to add deliberately. The quota makes that a real constraint rather than a formality: `contacts.resolveUsername` has no batch form and a daily ceiling of roughly two hundred (measured in `prioritize-resolve-queue`), so 100 channels is about half a day's budget of the same method — shared with `itgraph resolve`, which is working a queue of its own. It is also the method that produces day-long FloodWaits when a run is greedy. A command that loops over a 100-line file at speed would earn exactly that. Bounded, paced and resumable is not polish here; it is the whole point.

## What Changes

- **`itgraph add`** — new command. Takes usernames as arguments or `--from-file` for a list, and for each one resolves the username by public lookup and creates an inventory record stamped `manual`, already resolved. It joins nothing, subscribes to nothing, and reads no dialog list.
- **A username already in the inventory costs no request.** Checked in one query before any lookup. This is the resume mechanism and the quota guard in the same move: a run stopped at channel 60 re-run from the same file picks up at 61.
- **`--limit` bounds a run** by number of requests, so a session can spend a fixed slice of the day's quota and stop while the rest of the file waits.
- The pass obeys every collection rule the networked passes already obey: requests one at a time, a fresh pacing gap before each, a FloodWait waited out rather than circumvented, a FloodWait past the halt threshold stopping the run with what it achieved committed, and the rate limit recorded in `flood_events`.
- **`--seed` and `--kind` may review in the same pass, but only for usernames given as arguments.** Adding two or three channels the operator is looking at should not need a second command; a hundred-line file that nobody has re-read is exactly where a typo would be accepted into scope unseen, so the flags are refused with `--from-file`. The default stays `candidate`.
- **A recent rate limit on the same method is reported before the run starts.** `flood_events` knows whether `contacts.resolveUsername` has been limited in the last day; what it cannot know is which account was limited. So the run says what is recorded and proceeds, rather than refusing on a guess.
- **`--failures-out PATH` writes the failures as the next run's input** — the same format `--from-file` reads, with the reason as a trailing comment. Optional, no default, and nothing is written when nothing failed.
- **An existing record is never re-reviewed.** `add` may fill in identity, never overwrite a status, kind or rejection — the inventory's standing rule that no import path costs the operator a review they already did. A username already present is reported as already known, and `mark` remains the only way to change a decision.
- **A username that is not a channel creates nothing.** A user or a bot is reported as such, the same judgement `resolve` already makes, since this graph holds channels only.
- **A pending mention the addition makes redundant is cleared**, matching what resolution by id already does — so the mention queue does not later spend a request re-learning a channel `add` has just created.

Out of scope:

- **Joining anything.** Not a lesser version of subscribing: a route into the inventory that never joins is the point of the change.
- **Invite links and private channels.** `t.me/+…` needs `messages.checkChatInvite` and answers only for channels the account was let into. Public usernames only.
- **Reading Telethon's session entity cache to skip the lookup.** The cache does store usernames, and a personal account's session would likely answer for a good share of a hand-written list for free. Telethon deliberately does not use it for `get_entity`, because a cached username silently misses a rename and resolves to whoever holds the name now. Worth revisiting as a measured optimisation; not worth quietly resolving the wrong channel to save requests in the first version.
- **Any change to `resolve`'s two queues.** Their order was settled in `prioritize-resolve-queue` and this adds no case to it.

## Capabilities

### New Capabilities

None. This adds a third way into an inventory that already exists.

### Modified Capabilities

- `channel-inventory`: gains a **Manual Addition** requirement — a channel may enter the inventory by username, resolved and recorded without being joined, subscribed to or imported from a dialog list; the pass is bounded, paced, resumable, and never overwrites a review.

## Impact

- **No migration for the inventory.** `DiscoverySource.MANUAL` was declared when the enum was written and has had no producer since ([`models.py:121`](../../../src/itgraph/db/models.py#L121)); this is it. No new table, no new column.
- **One enum migration for the flood record.** `CollectionCommand` has `backfill`, `resolve` and `metadata`, and a limit hit by `add` belongs to none of them. Filing it under `resolve` would keep the method honest and the attribution false, in the one table whose stated purpose is making the command-to-method mapping checkable — and with `add` and `resolve` spending the same rationed method in the same days, which command burned the quota is precisely the question being asked. Adding a value to a PG enum is cheap but is still a migration, and so is verified on a scratch database like any other.
- `src/itgraph/tg/manual.py` — new module: the pass itself. Reuses `waiting_out_floods`, `FloodRecorder` and `pace` rather than restating them.
- `src/itgraph/cli.py` — the command; parsing only, per the CLI convention.
- `src/itgraph/db/channels.py` — reuses `create_resolved_channel`; gains a bulk "which of these usernames does the inventory already hold" query, matched case-insensitively the way `find_channel` matches.
- `src/itgraph/db/edges.py` — reuses `delete_pending_mention`. The mention queue already excludes usernames whose channel exists, so no requirement of `forward-graph` changes.
- Tests: `test_manual.py` (new), `test_cli.py`, `test_channels.py`. No network, Telethon mocked, usernames in fixtures obviously synthetic.
- `README.md` — a section for the command, and the note that adding channels never requires subscribing to them.
- `src/itgraph/CLAUDE.md` — a row for the new module in the layout table.
