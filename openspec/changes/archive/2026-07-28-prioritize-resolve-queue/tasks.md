## 1. Schema: where a mention came from

- [x] 1.1 `db/models.py`: `PendingMentionSource` — `username` + `channel_id` as a composite primary key, foreign key `username → pending_mentions.username` with `ON DELETE CASCADE`. No foreign key onto `channels`: those rows are never deleted, so it would never fire, and it would make truncation order-dependent for nothing
- [x] 1.2 Alembic revision creating the table. Nothing is backfilled in it — the counts come from parsing payloads, and a copy of the reference parser frozen inside a migration is a file that has to keep working forever
- [x] 1.3 Verify the revision on a scratch database whose name ends in `_test`; read `alembic upgrade --sql` first. Check the cascade for real: delete a `pending_mentions` row, confirm its sources go with it

## 2. Derivation records the source it already has

- [x] 2.1 `derive/edges.py`: collect `(channel_id, username)` pairs instead of bare usernames — the source id is already in scope at the point the username is queued, so nothing new is parsed and no extra pass is made
- [x] 2.2 `db/edges.py`: `add_pending_mentions` takes pairs, inserts the pending row as it does now, and inserts the source rows with `ON CONFLICT DO NOTHING`. Both idempotent — a second pass over unchanged raw messages must still write nothing
- [x] 2.3 `db/edges.py`: `truncate_derived` names `pending_mention_sources` explicitly. **Not `CASCADE`** — that would truncate anything else that ever gains a foreign key here, which is the quiet reach the docstring warns against
- [x] 2.4 Tests in `test_derive.py`: two channels mentioning one username give two sources; one channel mentioning it repeatedly gives one; a second identical pass adds nothing; `--rebuild` empties the table and the next pass refills it

## 3. Resolution works the head first

- [x] 3.1 `db/edges.py`: `pending_mentions_to_resolve` orders by source count descending, then `first_seen_at`, then `username`. Count via an outer join to a grouped subquery — the composite primary key already makes it distinct, so `COUNT(*)` is enough and `DISTINCT` is not. A username with no sources counts zero through `COALESCE` and sorts last
- [x] 3.2 Same function: a `min_sources` filter on the same expression it orders by
- [x] 3.3 `tg/resolve.py`: pass `min_sources` through; log the count alongside the username being resolved, so a run's priorities are visible in the log rather than inferred
- [x] 3.4 `tg/resolve.py`: if the mention queue is non-empty and no sources are recorded at all, say so — derivation has not run since the change, and the ordering is therefore arrival order. One query, no request; the run proceeds
- [x] 3.5 `cli.py`: `--min-sources` on `itgraph resolve`, defaulting to none so the tail stays reachable and the default behaviour is reordered rather than narrowed
- [x] 3.6 Tests in `test_resolve.py`: most-mentioned first; ties fall back to arrival order and a bounded run repeats identically against unchanged data; a sourceless username sorts last; `--min-sources 2` requests only the head and leaves the rest pending; the empty-sources notice fires once and does not stop the run
- [x] 3.7 Test in `test_cli.py`: `--min-sources` reaches the pass

## 4. Close out

- [x] 4.1 `make validate` green — lint, mypy, pytest
- [x] 4.2 `README.md`: the `resolve` section describes the queue as arrival-ordered and the option table lacks `--min-sources`. Both change. Worth stating the measured shape of the queue there too — 87% of pending usernames are mentioned by exactly one channel — because it is the whole reason the ordering exists
- [x] 4.3 `src/itgraph/CLAUDE.md`: `db/edges.py` owns one more table; update its row
- [x] 4.4 `openspec validate prioritize-resolve-queue --type change` green
- [x] 4.5 Re-run the measurement after `derive` and reconcile against a fresh parse. **Done, and it turned up group 5.** Integrity is clean — zero pairs stored that a fresh parse under derivation's own scope cannot reproduce. The 365-pair gap against the first read-only script was not error but the script over-counting: it had no way to know that 365 of those usernames are already channels, which derivation correctly writes an edge for rather than queueing. The real numbers replace the ones this task was written with

## 5. A queue entry whose channel already exists

- [x] 5.1 `db/edges.py`: `pending_mentions_to_resolve` excludes any username matching an existing channel — `NOT EXISTS` against `channels`, matched case-insensitively because pending usernames are stored normalised and channel usernames the way Telegram spells them. The load-bearing fix: 365 such rows, most of two days' quota, spent to re-learn channels the inventory has
- [x] 5.2 `tg/resolve.py`: `_resolve_channel` deletes the pending mention its own success made redundant, lowercased to match how the queue stores it. Hygiene rather than protection — it stops new ones accruing, at the point the inconsistency is created
- [x] 5.3 Tests in `test_resolve.py`: a username whose channel exists is never requested; resolving by id clears the matching pending row
- [x] 5.4 The 365 existing rows are left in place — invisible to the queue, costing nothing, and removed by the next `derive --rebuild`, which truncates the queue and will not re-queue a username the channel index knows. Deleting them eagerly would be a data migration to fix what an ordinary operation already cleans
