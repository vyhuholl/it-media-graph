## Context

Roughly three months of history for 200 channels sits in `raw_messages`. This change turns it into edges and, in doing so, produces the first candidates the operator did not already subscribe to.

The shape of this change is dictated by one fact: a stored payload names the channels it references, but does not describe them. A forward carries a bare numeric id; a mention carries a bare username. Neither can become an edge endpoint without a lookup that only Telegram can answer. Everything below follows from keeping that lookup out of the derivation itself.

## Decisions

### Derivation is pure, resolution is not

`itgraph derive` reads the database and writes the database. `itgraph resolve` talks to Telegram. They are separate commands because they have nothing in common operationally: derivation is fast, repeatable and safe to run in a loop while developing; resolution is slow, rate-limited, and carries the account risk that governs every networked part of this project.

The practical consequence is that the graph completes over two or three alternating runs — derive, resolve, derive — rather than one. This is the intended workflow, not a limitation to engineer around.

### Forwards reference an id, mentions reference a username

The two discovery paths are asymmetric, and the asymmetry decides where each one's unresolved state lives.

A forward's `from_id` is a `PeerChannel` carrying `channel_id`. That is the primary key of `channels`, so a row can be created immediately, with every other field empty. The edge can be written in the same pass.

A mention carries only `@username`. There is no id, so no row can be created and no edge can be written. Pending usernames therefore go to their own table, `pending_mentions`, keyed by the username. Resolution turns them into channel rows, and the *next* derivation run writes the edges. Mention edges lag one cycle behind forward edges by construction.

### `access_hash` makes "unresolvable" a property of the session, not the channel

Resolving a bare `channel_id` requires an `access_hash`, which is issued per account and never appears in a stored payload. Telethon caches hashes into its session file as it encounters entities during collection, so most referenced channels are resolvable — from that session, on that account.

This means an unresolvable id is a cache miss, and a later backfill that encounters the same channel may make it resolvable. So resolution records an attempt count and a timestamp rather than a permanent verdict: routine runs skip previously failed ids, and an explicit flag retries them. Treating the first failure as final would quietly truncate the graph.

Resolving a username needs no hash — it is a public lookup — so mentions do not have this problem.

### Ids are stored bare, everywhere

`PeerChannel.channel_id` is already the bare form used as the primary key of `channels`. No `-100` prefix is added or stripped anywhere. A `t.me/c/<id>/<msg>` link also carries the bare id, which makes it an id-shaped reference rather than a username-shaped one.

### Edges have a natural key and are inserted, not replaced

Unique on `(src_channel_id, msg_id, kind, dst_channel_id)`, written with `ON CONFLICT DO NOTHING`. Re-running derivation over unchanged raw data is therefore a no-op rather than a rewrite, and a run interrupted half-way can simply be repeated.

`derive --rebuild` truncates the derived tables first. Only that path removes edges whose source data is gone; the default path never deletes.

### Endpoints are created before the edges that need them

Each batch inserts any missing channel rows, then the edges, in one transaction. The foreign keys on `edges` are therefore always satisfiable, and a killed process leaves no edge pointing at a channel that does not exist.

### Which peer the edge points at

`fwd_from.from_id` — the original author — is the target. `saved_from_peer`, which names the intermediate place the message was copied from, is ignored: it describes the path a forward travelled rather than a relationship between two channels. If forward chains turn out to matter, they are re-derivable from the same payloads.

Forwards from individuals and forwards whose origin is hidden by the author's privacy
settings produce nothing. Neither has a channel on the far end.

### The graph holds no personal data

Only channel-to-channel edges are written. User ids appearing in `fwd_from` and in signed posts are dropped at this boundary and stay in the raw layer, which is never exported. This is what keeps the derived tables safe to visualize and share, and it is worth preserving deliberately as the graph grows.

### Parsing happens in Python, not in SQL

Peer shapes, entity types and link forms need branching that reads badly as `jsonb` expressions. Messages are streamed with a server-side cursor and edges written in batches. At this volume the whole pass is minutes, so clarity wins over doing it in the database.

## Volume expectations

Around 100k messages after a three-month backfill. Forwards are sparse — a low percentage of messages — so the edge count is in the thousands, not the hundreds of thousands. The candidate channels discovered will likely outnumber the seeds severalfold, most of them out of scope: that is expected, and triaging them is the next change's problem.

## Alternatives considered

- **Resolving inline during derivation.** Rejected: it makes the fast, repeatable pass slow, networked and non-repeatable, and couples the whole graph rebuild to rate limits.
- **A nullable-target edge carrying a username.** Rejected: it puts unresolved state in the table that analysis reads, and every consumer would have to filter it out forever. `pending_mentions` keeps it out of the way.
- **Truncate-and-rebuild as the only mode.** Honest for disposable data, and rejected as the default because a unique key gives the same repeatability without discarding work on every run. Kept as `--rebuild`.
- **Storing `saved_from_peer` as a second edge kind.** Deferred rather than rejected — re-derivable whenever it becomes interesting.

## Deferred

- Edge weights, time decay and clustering: the analysis this feeds.
- Distinguishing `@username` mentions from `t.me` links.
- Forward chains via `saved_from_peer`.
- External links in channel descriptions, and language detection — both derivations over payloads already stored, both free to add later.
- `getChannelRecommendations` as a third discovery source.

## Testing

Derivation is tested entirely against fixtures; nothing in it touches the network.

The fixtures need one payload per peer and reference shape, because the branching is where this change will break: a forward from a channel, from a user, with a hidden origin, and from the channel itself; an `@username` mention of a channel and of a person; `t.me/name`, `t.me/name/123`, `t.me/c/<id>/<msg>`, and an invite link, which resolves to
nothing.

Resolution is tested with a mocked client covering a cached id, an uncached id, a username that turns out to be a user rather than a channel, and a FloodWait.