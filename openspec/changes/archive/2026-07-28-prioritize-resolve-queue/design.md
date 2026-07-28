## Context

`itgraph resolve` works two queues. The one keyed by channel id is cheap — it goes through the session's cached `access_hash` into `channels.getChannels` and spends nothing rationed. The one keyed by username is not: every row is a `contacts.resolveUsername`, the tightest daily quota in the project, with no batch form and no substitute. 2888 rows is about two weeks.

Two weeks is not the problem. The problem is which 2888. Measured over the raw layer with the project's own parser:

```
2888 pending usernames
├── 2513  (87%)  mentioned by exactly one channel
├──  240         by two
├──   64         by three
└──   68         by four or more          (most: 24 channels)
                 ─────────────────────────
                 372 mentioned by ≥ 2  →  under two days of quota
```

`pending_mentions_to_resolve` orders by `first_seen_at, username`. Arrival order is a fine tie-break and a poor priority: the 372 usernames that carry the graph are spread through 2513 that resolve into degree-one vertices, so two days of budget buys an arbitrary slice rather than the head.

The count is not a stored fact. `pending_mentions` holds the username, when it was first seen, and how resolution went — never who mentioned it. `derive` has the source channel id in hand at the moment it queues a username ([`derive/edges.py`](../../../src/itgraph/derive/edges.py), the loop over `extract_references`) and throws it away. So this change is mostly about not throwing it away.

## Goals / Non-Goals

**Goals**

- Spend the daily resolution budget on the usernames with the most independent evidence behind them.
- Make "how many channels mention this" a stored, cheap fact rather than a full re-parse of the raw layer.
- Keep `derive` exactly as re-runnable as it is now: a second pass over unchanged data must still write nothing.

**Non-Goals**

- Making the queue shorter or the method cheaper. Neither is possible; this changes order, not cost.
- Ordering the by-id queue. It spends no quota, so its order is free either way.
- Deleting the tail. Evidence accumulates — today's single mention is next month's third.

## Decisions

### 1. Store pairs, not a counter

A new table, one row per `(username, source channel)`:

```
pending_mention_sources
  username    text    ─┬─ PK
  channel_id  bigint  ─┘
  FK username → pending_mentions.username  ON DELETE CASCADE
```

*Why not a `mentioned_by` integer on `pending_mentions`?* Because `derive` must stay idempotent, and an increment cannot be. Re-running derivation over unchanged raw messages currently writes nothing — that property is stated in the spec and is what makes the pass safe to repeat at any time. A counter would need either "have I already counted this message" bookkeeping (which is a join table with extra steps) or a full recount on every pass. A pair inserted `ON CONFLICT DO NOTHING` is idempotent by construction, and the count falls out of a `COUNT(*)` — the composite primary key already makes it a distinct count, so no `DISTINCT` is needed.

*Cost:* the measurement implies roughly 3 600 rows for the current corpus. It grows with distinct pairs, not with messages.

*Alternative considered:* derive the count from `edges` instead. It does not work — the whole point of a pending mention is that no edge exists yet, because the endpoint has no id.

### 2. The channel id is stored without a foreign key onto `channels`

The source is always a channel the inventory knows — it is a channel backfill walked — so a foreign key would hold today. It is still omitted: `channels` rows are never deleted, so the constraint would never fire, and adding it would make `truncate_derived` order-dependent for no benefit. The `pending_mentions` foreign key is the one that earns its place, because that table *is* truncated and its rows *are* deleted on resolution.

### 3. Ordering, and what happens to a tie

```
ORDER BY  source_count DESC,  first_seen_at,  username
```

The existing order becomes the tie-break, so a bounded run remains deterministic within a sitting — which is what `--limit` needs to mean anything.

Across sittings the order *can* shift: a later `derive` may add a source and lift a username past others. That is the intended behaviour, not a regression — new evidence should reprioritise — but it means `--limit` no longer guarantees "the same rows in the same order across runs" the way the id queue does. Worth saying out loud because the id queue's docstring promises exactly that.

The count is obtained by an outer join to a grouped subquery rather than a correlated subquery per row, so `--min-sources` can filter on the same expression it orders by.

### 4. `--min-sources N` bounds by evidence, `--limit` by budget

They compose: `--limit 200 --min-sources 2` means "spend today's two hundred on the head". `--min-sources` defaults to none, so the tail is still reachable and the default behaviour is only reordered, never narrowed.

A username with no rows in the new table counts as zero, via `COALESCE`, and sorts last.

### 5. The empty-table trap gets a warning, not a migration

The table starts empty. Until `derive` runs again, every pending username has zero sources — so `--min-sources 2` returns nothing and looks broken, and plain `resolve` silently falls back to arrival order.

Backfilling it in the migration is not an option: the counts come from parsing message payloads, and parsing belongs to `derive`, not to a schema change. Putting the project's reference parser inside an Alembic revision would freeze a copy of it in a file that must keep working forever.

So `resolve` checks and says so: if the mention queue is non-empty and the sources table is empty, it logs that `itgraph derive` has not run since the change and that ordering is therefore arrival order. One query, no request, and it turns a silent surprise into a sentence.

### 6. `truncate_derived` names the new table

`TRUNCATE TABLE edges, pending_mentions` becomes `TRUNCATE TABLE edges, pending_mentions, pending_mention_sources`. Explicitly, rather than adding `CASCADE`: `CASCADE` would also truncate anything else that ever gains a foreign key here, which is precisely the kind of quiet reach the function's docstring warns against.

### 7. A pending mention whose channel already exists is skipped, and stops being created

Storing the sources turned up something the counts had been hiding. 365 rows of the queue — 13% of it — name a username that is **already a channel in the inventory**, every one of them discovered by forward and resolved by id.

The mechanism is an asymmetry between the two resolution paths. `_resolve_pending` deletes the pending row when a username becomes a channel; `_resolve_channel` sets a username on a channel it resolved by id and deletes nothing, because it is not looking at that queue. So a channel found both ways — mentioned by name in one message, forwarded from in another — resolves by the cheap path and leaves the expensive path's row behind for good.

Derivation is already right about this: it sees the username in the channel index, writes a mention edge, and does not queue it. That is why these rows have zero sources, and it is what made them visible at all.

What is wrong is the queue. Left alone it would spend 365 `contacts.resolveUsername` calls — most of two days at the observed daily rate — on lookups that can only return channels the inventory already has. `create_resolved_channel` would refresh the existing row and `delete_pending_mention` would finally clear it, so the run self-heals; it just pays two days for the privilege. On a change whose entire subject is not wasting that request, this cannot be left in.

Two fixes, doing different jobs:

- **The queue excludes any username that matches an existing channel.** One `NOT EXISTS` against `channels`, matched case-insensitively because pending usernames are stored normalised and channel usernames are stored as Telegram spells them. This is the load-bearing one: it protects the quota immediately, for the 365 that exist today and any that slip through later.
- **`_resolve_channel` deletes the pending row its own success made redundant.** This is hygiene rather than protection — it stops new ones accruing, and puts the deletion at the point where the inconsistency is created.

The 365 rows themselves are left in place. They are invisible to the queue, they cost nothing, and `derive --rebuild` removes them whenever it next runs: it truncates the queue and re-derives, and derivation will not re-queue a username the index knows. Deleting them eagerly would mean a data migration to fix rows that a normal operation already cleans.

*Alternative considered:* have `derive` delete pending mentions that have become known. It holds the index, so it is the cheapest place to notice — but `truncate_derived` is documented as the single path in the whole change that deletes derived data, and quietly adding a second one is how that guarantee stops being true.

## Risks / Trade-offs

**A channel could become its own source.** If a channel mentions itself by a username the derivation index does not know, the pair records it as mentioning itself, inflating its own count by one. The existing self-reference guard cannot fire, because it needs a resolved id to compare against. → Accepted: the effect is one source on one username, and it corrects itself the moment the username resolves and the row cascades away.

**The tail becomes effectively invisible.** With ordering by evidence, 2513 single-mention usernames sit behind 372 others more or less permanently. → That is the intent, but it should be a choice rather than an accident: `--min-sources` makes the head explicit, and leaving it unset still reaches the tail eventually.

**Ordering shifts between sittings.** See decision 3. → Documented rather than prevented; the alternative is freezing a stale priority.

**Row growth is unbounded in principle.** Every new source of every pending username is a row. → Bounded in practice by the corpus: pairs, not messages, and the rows cascade away on resolution.

## Migration Plan

One Alembic revision creating one table. No existing column changes, no data moves, nothing to backfill in the revision itself.

Operator sequence:

1. `uv run alembic upgrade head` — takes a full dump first, as every upgrade on the working database does.
2. `uv run itgraph derive` — refills the sources from the raw layer. No network, no `--rebuild`, no re-fetching; this is the ordinary re-runnable pass.
3. `uv run itgraph resolve --limit 200 --min-sources 2` — the head, at about two hundred a day for two days.
4. Afterwards, plain `resolve --limit 200` works the tail in evidence order.

Rollback is a revert plus `alembic downgrade -1`, which drops the table. Nothing else depends on it, and the counts are re-derivable from the raw layer at any time — which is the property that makes dropping it safe.

## Open Questions

- Should `--min-sources` have a non-zero default once the head is worked through? Leaning no: a default that silently hides rows is worse than an explicit flag, and the ordering already does the prioritising.
- Should the sources table record *which* message mentioned the username, not just which channel? It would make the evidence auditable, but the raw layer already answers that and the table would grow with messages rather than pairs.
- Is "distinct channels" the right weight, or should a channel that mentions a username ten times count for more than one that mentions it once? Distinct channels is deliberately the conservative choice — repetition within one channel is one channel's opinion — but it is worth revisiting against the finished graph.
