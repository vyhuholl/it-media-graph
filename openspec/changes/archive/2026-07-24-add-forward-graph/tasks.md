## 1. Storage

- [x] 1.1 Add the `edges` model: `src_channel_id`, `dst_channel_id`, `kind`, `msg_id`, `published_at`, `derived_at`. Foreign keys on both endpoints, unique constraint on `(src_channel_id, msg_id, kind, dst_channel_id)`.
- [x] 1.2 Add the `edge_kind` enum with `forward` and `mention`.
- [x] 1.3 Add the `pending_mentions` model keyed by username: `first_seen_at`, `attempts`, `last_attempt_at`, `last_error`. Holds only usernames not yet turned into channels.
- [x] 1.4 Add resolution tracking to `channels`: `resolved_at`, `resolve_attempts`, `resolve_last_attempt_at`, `resolve_last_error`.
- [x] 1.5 Backfill `resolved_at` for existing rows in the migration — everything imported from dialogs or from the metadata pass already carries username and title.
- [x] 1.6 Add indexes on `edges.src_channel_id`, `edges.dst_channel_id` and `edges.published_at`; every analysis query filters on one of them.
- [x] 1.7 Generate and review the migration. Check `upgrade head` and `downgrade base` on a scratch database, then confirm `alembic check` is quiet.

## 2. Payload parsing

- [x] 2.1 Add peer extraction: given a payload's `from_id`, return a channel id, or nothing for users, hidden origins and self-forwards.
- [x] 2.2 Add entity extraction for `MessageEntityMention`, `MessageEntityUrl` and `MessageEntityTextUrl`. Entity offsets are **UTF-16 code units**, not Python characters — encode the text to UTF-16-LE before slicing, or every message containing an emoji before a mention yields a corrupted username.
- [x] 2.3 Add `t.me` link parsing covering `t.me/name`, `t.me/name/123`, `t.me/c/<id>/<msg>`, `t.me/s/name`, and the invite forms `t.me/joinchat/...` and `t.me/+...`, which reference nothing resolvable.
- [x] 2.4 Normalize usernames to lowercase without the leading `@` at every boundary, so lookups and `pending_mentions` keys agree.
- [x] 2.5 Unit-test each of the above against one fixture per shape. This is where the change will break, not in the pass that calls it.

## 3. Derivation pass

- [x] 3.1 Add `itgraph derive`: stream `raw_messages` with a server-side cursor, keyed by source channel, in batches.
- [x] 3.2 Emit forward edges. Create the missing endpoint channel row with `discovered_via='forward'` in the same transaction as the edge.
- [x] 3.3 Emit mention edges for usernames and id-shaped links already present in `channels`; record unknown usernames in `pending_mentions` instead.
- [x] 3.4 Deduplicate references within a single message before writing, so one message referencing a channel twice yields one edge.
- [x] 3.5 Write edges with `ON CONFLICT DO NOTHING`. A repeated run over unchanged raw data must produce no writes.
- [x] 3.6 Add `--rebuild`, truncating `edges` and `pending_mentions` first. This is the only path that deletes derived data; it must not touch `channels` or the raw layer.
- [x] 3.7 Report edges written, channels discovered and mentions left pending.

## 4. Resolution pass

- [x] 4.1 Add `itgraph resolve`, handling both queues: channels with `resolved_at IS NULL`, and every row in `pending_mentions`.
- [x] 4.2 Resolve channels by id through the session's cached `access_hash`; resolve pending usernames by public lookup.
- [x] 4.3 On success set `resolved_at`, username and title. For a pending username, upsert the channel with `discovered_via='mention'` and delete the pending row.
- [x] 4.4 On failure increment `attempts` and store the reason and timestamp. Skip rows with prior attempts by default; add `--retry-failed` to try them again, since an unresolvable id may become resolvable after a later backfill.
- [x] 4.5 Discard resolutions that turn out to be users or bots rather than channels: record the reason, create no channel row.
- [x] 4.6 Reuse the collector's pacing and FloodWait handling. Sequential requests, a configurable delay, `--limit` to bound a run.

## 5. Tests

- [x] 5.1 Cover the spec scenarios for derivation against fixtures, with no network.
- [x] 5.2 Assert repeatability explicitly: two consecutive runs produce identical edges, and the second writes nothing.
- [x] 5.3 Assert that derivation makes no Telegram request at all — fail the test if the client is constructed.
- [x] 5.4 Assert no edge references a channel absent from `channels`, and that no user id is written to any derived table.
- [x] 5.5 Cover resolution with a mocked client: a cached id, an uncached id, a username that resolves to a user, a FloodWait, and `--retry-failed` picking up a row that previously failed.
- [x] 5.6 Cover the two-cycle workflow: derive leaves a mention pending, resolve creates the channel, the second derive writes the edge.

## 6. Wrap-up

- [x] 6.1 Run `make validate` and fix everything it reports. Do not close the change while validate is red.
- [x] 6.2 Update `README.md` **in Russian**: the `derive` and `resolve` commands, why mention edges need a second derive pass, what `--rebuild` and `--retry-failed` do, and a note that derivation is safe to re-run at any time.