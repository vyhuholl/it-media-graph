## Context

`edges` names both endpoints as channels and the referencing message, but not the referenced one. This change adds it, along with the original publication date and the album group of the referencing message.

All three come from payloads already stored, so the work is entirely a rebuild. The interesting part is not the extraction — it is that the natural key of an edge changes shape, and one of its columns is nullable.

## Decisions

### Three columns, and no fourth

`dst_msg_id`, `dst_published_at`, `grouped_id`.

Travel time — the interval between the two publication dates — is deliberately not stored. It is a subtraction over two columns in the same row, and storing it would be the first derived measure to live inside the observation table.

### The unique key must treat nulls as equal

`dst_msg_id` joins the natural key, because two links to different posts of the same channel in one message are two references, not one.

It is also nullable: plain `@username` mentions and forwards that name no original post have nothing to put there. Postgres treats nulls as distinct in unique constraints by default, so a mention edge would satisfy the constraint against a copy of itself, and every re-run of derivation would insert it again — silently, since `ON CONFLICT DO NOTHING` never fires on a conflict that Postgres does not consider one.

That would break the repeatability the whole derivation design rests on. The constraint therefore declares nulls equal:

```sql
ALTER TABLE edges ADD CONSTRAINT uq_edges_reference
    UNIQUE NULLS NOT DISTINCT
    (src_channel_id, msg_id, kind, dst_channel_id, dst_msg_id);
```

`NULLS NOT DISTINCT` requires Postgres 15 or newer, which the project's compose file already pins. Check whether the installed SQLAlchemy renders it from the model; if it does not, write the constraint as raw SQL in the migration and keep the model's declaration in step manually.

### Travel time is measured from the original, not from the hop it arrived through

When a forward is itself forwarded, Telegram keeps `fwd_from` pointing at the original author rather than the intermediate channel. `dst_published_at` is therefore always the root post's date, and the interval derived from it is time since original publication — not time since the copy this channel actually saw.

For "how fast did this post spread" that is the right measurement. For reconstructing the path it travelled it is not, and that would need `saved_from_peer`, which this project still ignores.

### Album grouping is recorded, not applied

A forwarded album arrives as several messages, each with its own `fwd_from`, so one repost of a ten-image album currently produces ten edges. This is already skewing the collected data, not just future data.

`grouped_id` is stored on the edge and nothing else changes: collapsing an album into one event is a counting decision, and counting decisions belong to analysis. Note that the id is only meaningful together with the channel — group on `(src_channel_id, grouped_id)`, never on `grouped_id` alone.

### `pending_mentions` needs no change

An unresolved mention that pointed at a specific post would seem to need its post id carried through resolution. It does not: derivation re-reads the full raw layer on every run, so once the username becomes a channel, the next run extracts the post id from the same payload it always came from.

The queue stays a bare list of usernames. This falls out of derivation being a complete re-read rather than an incremental one, and is a good reason to keep it that way.

### The migration truncates rather than backfills

Derivation inserts with `ON CONFLICT DO NOTHING`. Existing rows already satisfy the old key, so a re-run would leave their new columns empty while reporting success — the worst kind of failure, since nothing looks wrong.

Backfilling the columns in SQL would mean reimplementing peer and entity parsing as `jsonb` expressions, in a migration, for data that rebuilds in minutes. So the migration drops the old constraint, empties the table and creates the new one; `itgraph derive` repopulates it.

The raw layer is untouched, and `pending_mentions` is left alone — the usernames in it are still unresolved regardless of what edges say.

## What this does not give you

Knowing that post 12345 of channel X was forwarded seven times says nothing about what that post contains. Its text is in the raw layer only if X is a seed whose history was collected.

For forwards between seeds — the dense middle of the graph — the post is there. For forwards from channels discovered by reference, only the id is. Ranking posts by reach works either way; showing what the post said does not, and any digest built on this will need to say so or collect the channels it wants to quote.

## Alternatives considered

- **`ON CONFLICT DO UPDATE` instead of truncating.** Rejected: it contradicts the requirement that a repeated run writes nothing, and leaves conflict-handling code in the derivation path that is needed exactly once.
- **A sentinel value instead of a nullable `dst_msg_id`.** Rejected: it makes "no post referenced" indistinguishable from a real id in every query, to avoid one keyword.
- **Merging album edges during derivation.** Rejected: it is an aggregate, and aggregates do not belong in the observation table. The group id keeps the option open.
- **Storing travel time.** Rejected for the same reason.

## Deferred

- Forward chains through `saved_from_peer`.
- Any query over the new columns: post reach ranking, spread speed, album-aware counting.
- Collecting the text of frequently forwarded posts from channels outside the seed set.

## Testing

Fixtures need one payload per shape, since the branching is where this breaks: a forward naming an original post, a forward naming none, several messages of one forwarded album, `t.me/name/123`, two links to different posts of one channel in a single message, a username mention and a post link to the same channel in one message, and `t.me/c/<id>/<msg>`.

Beyond the spec scenarios, the null-handling deserves its own test at the database level: inserting the same mention edge twice must raise a conflict rather than produce two rows. That is the failure this change is most likely to reintroduce later, and it is invisible in application code.