## Context

The inventory holds 200+ accepted channels. This change fills the raw layer from them and is the first code in the project to hit Telegram at volume, from a collection account created days earlier. Two constraints dominate every decision below: a run must survive being interrupted, and it must never look like a bot in a hurry.

The inventory was imported from the operator's personal account; collection runs from a different one. Nothing in the schema assumes the two are the same, and this section is the only place that mentions it — see "Entity resolution".

## Decisions

### Payloads are stored as `jsonb`, converted once at the edge

Telethon objects expose `.to_dict()`, which yields datetimes and raw bytes that the standard JSON encoder rejects. A single encoder converts datetimes to ISO-8601 strings and bytes to base64, and is the only place that touches payload shape.

`jsonb` rather than `json`: key order and whitespace are lost, which is irrelevant for data and buys indexing and containment queries that the derivation change will need on several hundred thousand rows.

"Verbatim" means no field is dropped, renamed or interpreted — not that the bytes are preserved literally.

### Entity resolution belongs to Telethon's session, not the database

A channel is addressed by `access_hash`, which is issued per account. The inventory was built by a different account, so its hashes would be meaningless here even if they had been stored — which is why `channels` holds only `tg_id` and `username`.

The collector resolves each channel by username on first contact and lets Telethon's session cache the result. This costs one `ResolveUsername` per channel on the first run and nothing afterwards, so the session file becomes load-bearing state: deleting it does not lose data, but does force every channel to be resolved again.

### Two cursors per channel, one of them unused for now

`backfill_state` records both `oldest_fetched_id` and `newest_fetched_id`.

Backfill walks backwards from the newest message, so only the oldest cursor is read by this change. The newest one is a high-water mark for incremental collection, which is the obvious next use of this table; recording it now costs a column and saves a migration against a table that will hold a row per channel.

### The cutoff is stored, not just applied

`backfill_state` records the cutoff date a channel was completed to. Re-running with the same cutoff skips the channel; re-running with an earlier one resumes from the oldest cursor. Without storing it, deepening the window would either silently do nothing or re-fetch everything.

### Rows and cursor commit together

Messages are inserted in batches, and the cursor advances in the same transaction as the batch it describes. A killed process therefore leaves the cursor consistent with what is stored — never ahead of it, which would silently lose a window of history.

`ON CONFLICT (channel_id, msg_id) DO NOTHING`: the first fetch wins. Message edits and changing view counts are deliberately not tracked here; they need time series, which is a different table and a different change.

### Metadata pass runs before history, per channel

`GetFullChannelRequest` is one cheap request that resolves the linked chat, confirms the channel is reachable, and yields the description. Running it first means an inaccessible channel costs one request instead of failing part-way through a long history walk.

Its payload is stored raw like any other. The external links in channel descriptions — GitHub, YouTube, personal sites — are extracted later from that payload, not now.

### Linked chats are updated, never upserted

The shared upsert refreshes only `username` and `title` on conflict, so it cannot write `linked_to` — routing the linked chat through it would silently do nothing. Resolution is therefore two explicit steps: upsert the chat row if unknown, then `UPDATE channels SET linked_to = :parent WHERE tg_id = :chat`.

A chat already imported from the operator's subscriptions keeps its original `discovered_via` and `first_seen_at`; only `linked_to` is written.

### FloodWait is handled in two places, both by waiting

Telethon sleeps through waits shorter than its `flood_sleep_threshold` on its own. Longer waits surface as an exception, which the collector logs with its duration and sleeps out explicitly. Neither path retries against a different session or connection.

Between requests there is a configurable delay, and channels are processed strictly one at a time. Concurrency would buy nothing anyway — Telegram's limits are per account, so parallel workers would only reach the same ceiling faster and look worse doing it.

### Failures are classified as permanent or transient

Permanent — the channel is private, deleted, or its username no longer resolves. Recorded against the channel; later runs skip it without retrying.

Transient — network errors, timeouts, unexpected server errors. Recorded with a timestamp; later runs try again.

Everything else about the channel is retained either way. A channel that goes private is still a node in the graph and still has whatever history was already collected.

### Raw payloads may contain personal data

A forward from an individual's message carries that user's id in `fwd_from`, and signed posts carry their author. This is unavoidable — the field is what the forward graph is built from — and it is the reason the raw layer is never exported, never committed, and never used as test fixtures without anonymization.

## Volume expectations

200 channels over 6–12 months, at a few posts per day each, is on the order of 400k messages: roughly 1–2 GB of `jsonb`, well within a local Postgres.

History arrives 100 messages per request, so a full pass is a few thousand requests. With conservative pacing that is hours, not minutes — which is the practical argument for resumability and for `--limit` on early runs, ahead of any ban-risk argument.

## Alternatives considered

- **Parsing during collection.** Rejected: the project's central invariant. Re-fetching to recover from a parsing decision is the expensive operation this design exists to avoid.
- **Parallel channel fetching.** Rejected: rate limits are per account, so it raises risk without raising throughput.
- **Storing `access_hash` in `channels`.** Rejected: per-account, and the account that built the inventory is not the one collecting.
- **One cursor.** Rejected: incremental collection needs the other end, and adding it later means migrating a per-channel table.
- **Normalized message columns beside the payload.** Tempting for query convenience, and rejected: every such column is a parsing decision smuggled into the collector.

## Deferred

- Comments in linked discussion chats: an order of magnitude more data, different request mechanics, and the first collection to carry personal data at scale.
- Message edits and view/reaction time series: separate table, separate cadence.
- Incremental collection of new posts: needs the newest cursor this change records.
- Everything derived — forward edges, mentions, external links, language.

## Testing

Telethon is mocked throughout; fixtures are anonymized payloads with synthetic ids.

Beyond the scenarios in the spec, three cases are worth explicit tests because they are where silent data loss would come from: a batch interrupted mid-channel leaves the cursor no further along than the stored rows; re-running with an earlier cutoff resumes rather than skips; and a `FloodWaitError` results in a sleep and a retry rather than a skipped window.