## 1. Storage

- [x] 1.1 Add `dst_msg_id`, `dst_published_at` and `grouped_id` to the `edges` model, all nullable.
- [x] 1.2 Replace the unique constraint with `(src_channel_id, msg_id, kind, dst_channel_id, dst_msg_id)` declared `NULLS NOT DISTINCT`. Verify the installed SQLAlchemy renders that clause; if it does not, write it as raw SQL in the migration and keep the model declaration in step by hand.
- [x] 1.3 Add an index on `(dst_channel_id, dst_msg_id)` — every post-level question filters on that pair.
- [x] 1.4 Write the migration: drop the old constraint, empty `edges`, add the columns, create the new constraint and index. Leave `raw_messages`, `channels` and `pending_mentions` untouched.
- [x] 1.5 Check `upgrade head` and `downgrade base` on a scratch database, then confirm `alembic check` is quiet.

## 2. Parsing

- [x] 2.1 Extract `fwd_from.channel_post` and `fwd_from.date` alongside the existing peer extraction. A forward naming no original post still yields an edge, with those fields empty.
- [x] 2.2 Extract `grouped_id` from the referencing message and carry it onto every edge that message produces.
- [x] 2.3 Extend `t.me` link parsing to return the message id from `t.me/name/123` and `t.me/c/<id>/<msg>`. Channel-only forms continue to return none.
- [x] 2.4 Unit-test each shape from the design's testing section, including two links to different posts of one channel and a username mention alongside a post link to the same channel.

## 3. Derivation

- [x] 3.1 Write the three new columns on every edge.
- [x] 3.2 Change within-message deduplication to key on `(kind, dst_channel_id, dst_msg_id)`, so different posts of one channel produce different edges while an exact repeat produces one.
- [x] 3.3 Confirm nothing derived from the new columns is stored — no elapsed time, no album collapsing, no counts.

## 4. Tests

- [x] 4.1 Cover the modified spec scenarios against fixtures.
- [x] 4.2 Re-assert repeatability with the new key: two consecutive runs produce identical edges and the second writes nothing.
- [x] 4.3 Assert that a forward whose payload names no original post still produces an edge.

## 5. Wrap-up

- [x] 5.1 Run `make validate` and fix everything it reports. Do not close the change while validate is red.
- [x] 5.2 Update `README.md` **in Russian**: that the migration empties `edges` and a `derive` run is required after it, what the new columns mean, and that album forwards produce one edge per message sharing a group id rather than a single merged edge.