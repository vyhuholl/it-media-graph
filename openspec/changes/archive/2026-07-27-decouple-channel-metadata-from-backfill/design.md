## Context

`itgraph backfill` halts on a day-long FloodWait roughly two hundred channels into a first pass. `flood_events` names the method: `ResolveUsernameRequest`.

The mechanism is worth stating precisely, because the fix follows from it directly. Telethon 1.44's `get_entity` does not consult its cache for a username — it issues `contacts.resolveUsername` unconditionally, and says so in a comment at `telethon/client/users.py:336-339`: *"We could check saved usernames… While this would reduce the amount of ResolveUsername calls, it would fail to catch username changes."* Its docstring adds that flood waits begin "around 50 usernames in a short period".

`fetch_full_channel` calls exactly that, once per channel, purely to obtain a peer for `GetFullChannelRequest`. And `GetFullChannelRequest.resolve` (`telethon/tl/functions/channels.py:805`) does:

```python
self.channel = utils.get_input_channel(await client.get_input_entity(self.channel))
```

So the request already resolves its own peer. Handing it a username makes `get_input_entity` fall through its cache lookups to `_get_entity_from_string` and out to the network (`users.py:444-446` → `users.py:563`). Handing it an `InputPeerChannel` short-circuits at the first line of `get_input_entity` (`users.py:419-422`) and costs nothing. The whole `resolveUsername` spend in `backfill` is one argument of the wrong type.

That leaves `GetFullChannelRequest` itself — also quota-bearing, also ~200 per cold pass. And here the observation is structural rather than mechanical: it answers what the description says and which discussion chat belongs to the channel, and **neither is an input to walking history**. The current code makes it a precondition only because it was the thing that happened to return a peer.

Current shape:

```
backfill_channel
 └── _resolve_peer
      ├── metadata fresh?  ── yes ──▶ get_input_entity(username)      0 requests
      │                                (falls back to the branch below on any error)
      └──────────────────── no ───▶ fetch_full_channel(username)
                                     ├── get_entity(username)         resolveUsername   ← quota
                                     └── GetFullChannelRequest        getFullChannel    ← quota
```

Target shape:

```
backfill_channel
 └── peer from the session's entity cache                             0 requests
      └── cache miss ──▶ skip the channel, record why, spend nothing

itgraph metadata   (its own command, its own budget)
 └── GetFullChannelRequest(peer from cache)                           getFullChannel
```

## Goals / Non-Goals

**Goals**

- A history walk issues no request that carries a daily quota. This is the invariant the change exists to establish, and it should be testable as a flat assertion, not inferred from counts.
- `contacts.resolveUsername` is issued by exactly one command. Today it leaks out of `resolve` into `backfill`; concentrating it makes the scarcest budget in the project visible in one place.
- Descriptions and linked chats keep being collected, on a cadence that matches how fast they change (months) rather than how often history is walked (daily).

**Non-Goals**

- Reducing the cost of resolving a username. `contacts.resolveUsername` has no batch form; the 277-row mention backlog is a separate problem with a separate answer.
- Storing `access_hash` in Postgres. Tempting — it would make the peer independent of the session file — but a hash is issued per account, so the column would need an account identity beside it, which is a schema change this change does not need. The session file already is that cache, and it is already backed up.
- Any change to what is stored, or to the raw layer's shape.

## Decisions

### 1. The peer always comes from the session's entity cache

`_resolve_peer` collapses to a single path: `client.get_input_entity(username)`, unconditionally, for every channel. No freshness check, no branch.

*Why not keep the freshness branch?* Because after decision 3 there is nothing on the other side of it. The branch existed to decide between "cheap peer" and "peer as a side effect of the metadata request", and the metadata request is leaving.

*Alternative considered:* keep `fetch_full_channel` in the walk but pass it a cached peer. That fixes `resolveUsername` and leaves `getFullChannel` — 200 quota-bearing requests per cold pass, which is the same order as the wall we are trying to clear. It would move the halt, not remove it.

### 2. Identity comes from the response, not from a second lookup

`fetch_full_channel` takes a peer and returns what the `messages.ChatFull` response already carries. `result.chats` holds the full `Channel` object for the channel itself alongside the linked chat — `_discussion_chat` already walks that list for the chat, so reading the channel out of it is the same operation applied to a different element. `ChannelMetadata.tg_id` comes from `result.full_chat.id`; `ChannelMetadata.entity` disappears, since its only caller was the walk that no longer needs it.

This is what makes decision 1 lossless: nothing about the channel's identity was ever only available from `get_entity`.

### 3. The metadata pass becomes its own command

`itgraph metadata`, with `--limit` and a flag to ignore the freshness window.

*Why a separate command over a separately-budgeted phase inside `backfill`:*

- The two have genuinely different shapes. History is thousands of cheap requests over hours; metadata is ~200 expensive ones, wanted about monthly. A single `--limit` cannot mean both.
- A halt must not be contagious. Today a metadata FloodWait stops the run and the history never gets walked. Two commands means the expensive pass can hit its quota without costing the cheap one.
- It matches the shape the project already has. `resolve` is exactly this: a separate command that spends a scarce, quota-bearing budget on a queue. `metadata` is its sibling, and the symmetry is worth more than the saved keystroke.

*Cost:* one more command an operator can forget. Mitigated by decision 6.

### 4. A cache miss skips the channel and spends nothing

If `get_input_entity(username)` cannot answer from the cache, it falls through to `resolveUsername` (`users.py:444-446`). That fallback must not fire inside `backfill` — it is precisely the behaviour being removed, and it would fire for every channel on a rebuilt session, turning the fix into the bug.

So the miss is detected rather than absorbed: the channel is recorded as skipped, with a reason, alongside the existing `record_skip(session, tg_id, "no username")` path, and the run continues.

*Why this is not a hole in practice:* the entity cache is populated by whichever command brought the channel into the inventory. `dump-dialogs` populates it through `iter_dialogs`; `resolve` populates it by resolving. A channel that is in scope but absent from the cache means the session file was rebuilt — a real event, but a rare one, and the remedy (`dump-dialogs`, or `resolve --retry-failed`) is the operator's call, made with the quota cost visible.

*Alternative considered:* spend the `resolveUsername` behind an explicit `--allow-resolve` flag. Rejected for now — it reintroduces the leak the change is closing, and there is no evidence yet that the skip path is ever hit. If it turns out to be common, the flag is a small follow-up.

### 5. The invariant is asserted, not inferred

`tests/fakes.py` already separates the two: `FakeTelegramClient.resolved` records `get_entity` calls and `input_entities` records `get_input_entity`, with a docstring saying the distinction exists so a test can prove the metadata pass was skipped. The new test is the flat form of that — after a backfill run over any fixture, `client.resolved == []` and `client.requests == []`.

`FakeTelegramClient.__call__` currently does `self.full_channels[request.channel.id]`, which assumes a peer with `.id`. An `InputPeerChannel` has `.channel_id`. The fake needs updating, and that is a real signal rather than test friction: it is the shape change the production code is making.

### 6. Backfill reports stale metadata without fetching any

`RunSummary` gains a count of in-scope channels whose stored metadata is absent or past the freshness window, reported the way `deferred` already is — counted from the database before the walk, costing no request, printed in its own clause. An operator who never runs `itgraph metadata` is told so on every backfill, which is the cheapest available answer to "one more command to forget".

### 7. `--refresh-metadata` moves rather than dies

The flag's meaning — ignore the freshness window — belongs to the new command. `backfill --refresh-metadata` is removed rather than deprecated: it would have nothing to do, and a flag that silently does nothing is worse than one that is gone.

## Risks / Trade-offs

**Descriptions and linked chats go stale until someone runs `metadata`.** → Decision 6 makes the staleness visible on every backfill run. Nothing that currently exists is lost, and the data in question changes on the order of months.

**Linked-chat discovery slows down.** A channel that gains a discussion chat is noticed only on the next metadata pass, not the next backfill. → Accepted: the comments phase that consumes linked chats is not implemented, and the `deferred` counter already reports standalone chats waiting on it.

**`get_input_entity` can return a stale peer.** Telethon's own docstring warns the cached path may use a username that has since changed. → Already the accepted behaviour on the current fresh-metadata path, so this widens exposure rather than creating it. A changed username surfaces as a channel failure, which `classify` handles; a renamed channel is a fact the inventory needs to learn anyway.

**The skip path could be more common than expected**, e.g. after a session rebuild, quietly turning a backfill into a no-op. → The skip is counted in `RunSummary.skipped` and logged per channel, so a run that skips everything says so loudly. Decision 4's alternative is the escape hatch if it happens.

**`GetFullChannelRequest` may itself hit a daily quota at ~200 per pass.** The change does not make that request cheaper — it isolates it. → That is the point: the halt becomes contained in a command whose only job is metadata, and `--limit` lets the operator spread it over days.

## Migration Plan

One migration, for one reason. `flood_events.command` is a native Postgres enum built by `_pg_enum(CollectionCommand, "collection_command")`, and the metadata pass needs a third value. Reusing `backfill` was considered and rejected: the column's own docstring says telling the commands apart is half the reason the record exists, and a pass whose entire purpose is to isolate a quota must not file its rate limits under the command it was isolated from.

The migration is `ALTER TYPE collection_command ADD VALUE 'metadata'`, and it has one sharp edge worth writing down before someone meets it at three in the morning: Postgres will not let a newly added enum value be *used* in the same transaction that added it. Alembic wraps a revision in a transaction by default, so the revision must add the value and nothing else, and the first row using it must come from a later transaction. Nothing else in the schema changes, and no stored payload changes shape.

`CollectionCommand`'s docstring also stops being true — it currently says both commands share `contacts.resolveUsername`, which is the thing this change makes false.

Operator-visible sequence, once this lands:

1. `uv run alembic upgrade head` — takes a full dump first, as every upgrade on the working database does.
2. `itgraph backfill` — now walks history only. The first run reports every in-scope channel as having stale metadata.
3. `itgraph metadata --limit N` — spread over as many sittings as the quota wants. Descriptions and linked chats catch up.
4. Steady state: `backfill` as often as wanted, `metadata` about monthly.

Rollback is a revert of the code. The enum value is left in place rather than dropped: Postgres has no `ALTER TYPE ... DROP VALUE`, removing it means rebuilding the type, and an unused value strands nothing. The downgrade is therefore a no-op with a comment saying why — which `db/guard.py` will refuse to run on the working database anyway.

## Open Questions

- Does `metadata` want its own halt threshold, or does `flood_abort_threshold` serve both? Leaning towards reusing it — a per-command threshold is configuration nobody has asked for.
- Should `itgraph channels` show metadata age per row? It is the natural place to look before deciding to run the pass, but it widens this change's surface into the listing. Probably a follow-up.
- Is the stale-metadata clause in `RunSummary.line()` an `info` or a `warning`? It is normal on a first run and abnormal after months of neglect, and the summary has no way to tell those apart.
