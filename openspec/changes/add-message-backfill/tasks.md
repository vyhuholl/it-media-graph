## 1. Storage

- [x] 1.1 Add the `raw_messages` model: `channel_id`, `msg_id`, `payload jsonb`, `fetched_at`, composite primary key on `(channel_id, msg_id)`.
- [x] 1.2 Add the `backfill_state` model: one row per channel with `oldest_fetched_id`, `newest_fetched_id`, `cutoff_at`, run status, failure kind, failure detail, `updated_at`.
- [x] 1.3 Add the `raw_channels` model: one row per channel holding the latest `GetFullChannelRequest` payload and its `fetched_at`. The freshest payload wins — unlike messages, a description and a linked chat change, and only the current state is wanted.
- [x] 1.4 Add `last_post_at` to `channels`.
- [x] 1.5 Generate and review the migration. Check both `upgrade head` and `downgrade base` on a scratch database, then confirm `alembic check` is quiet.

## 2. Payload serialization

- [x] 2.1 Add the encoder converting Telethon `.to_dict()` output to JSON-safe values: datetimes to ISO-8601, bytes to base64. One function, used by every writer.
- [x] 2.2 Test it against a fixture containing both, and assert that no key is dropped or renamed.

## 3. Channel metadata pass

- [x] 3.1 Add `tg/full_channel.py`: resolve the channel by username, call `GetFullChannelRequest`, store the payload raw.
- [x] 3.2 Resolve the linked discussion chat in two explicit steps — upsert the chat row with `discovered_via='linked_chat'` if unknown, then `UPDATE channels SET linked_to`. Do not route `linked_to` through the shared upsert; it silently ignores the column.
- [x] 3.3 Leave `discovered_via` and `first_seen_at` untouched on chats that were already imported from the operator's subscriptions.

## 4. History walker

- [x] 4.1 Add the channel selection query: `status = 'seed'` and `NOT is_chat` and no permanent failure recorded. Never select by any other predicate.
- [x] 4.2 Refuse any entity without a username before requesting history; record it as skipped and continue.
- [x] 4.3 Walk `iter_messages` backwards from the newest message toward the cutoff, inserting in batches with `ON CONFLICT (channel_id, msg_id) DO NOTHING`.
- [x] 4.4 Advance `oldest_fetched_id` in the same transaction as the batch it describes. The cursor must never be ahead of the stored rows.
- [x] 4.5 Record `newest_fetched_id` on first contact, and `last_post_at` on the channel from the newest message's date.
- [x] 4.6 Store `cutoff_at` on completion. Skip channels already completed to the same or an earlier cutoff; resume the rest from `oldest_fetched_id`.
- [x] 4.7 Store message metadata only. No `download_media`, no file writes.

## 5. Pacing and failures

- [x] 5.1 Process channels strictly sequentially, with a configurable delay between requests and conservative defaults.
- [x] 5.2 Set `flood_sleep_threshold` on the client and catch `FloodWaitError` above it: log the duration, sleep, retry the same request. No alternative session, account or connection on any path.
- [x] 5.3 Classify failures as permanent (private, deleted, unresolvable username) or transient (network, timeout, server error), record both against the channel, and continue the run.
- [x] 5.4 Report counts of completed, skipped and failed channels when a run ends.

## 6. CLI

- [x] 6.1 Wire `itgraph backfill` with `--since`, `--limit` and the pacing options.
- [x] 6.2 Extend `itgraph channels` to show backfill state, so progress across runs is visible without opening psql.

## 7. Tests

- [x] 7.1 Extend the fixtures with anonymized message and full-channel payloads using synthetic ids. No real channels, no real users.
- [x] 7.2 Cover the spec scenarios: scope selection, refusal without a username, verbatim storage, no duplicates on re-fetch, resume, skip when complete, FloodWait waited out, permanent and transient failures, linked chat resolved and not duplicated.
- [x] 7.3 Cover the three silent-loss cases specifically: a batch interrupted mid channel leaves the cursor no further along than the stored rows; re-running with an earlier cutoff resumes instead of skipping; a `FloodWaitError` produces a sleep and a retry rather than a dropped window.
- [x] 7.4 Assert that a completed run wrote no derived data — no edges, mentions, links or language labels.

## 8. Wrap-up

- [x] 8.1 Run `make validate` and fix everything it reports. Do not close the change while validate is red.
- [x] 8.2 Update `README.md` **in Russian**: the `backfill` command and its options, what a cautious first run looks like, how to read backfill state, and a note that the Telethon session file now caches entity resolution — losing it costs a slow first run, not data.