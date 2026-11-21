# Proposal

## Why

The project measures what is *taking off* — spikes, cascades, weekly lists — but cannot answer *what a post or video is about*. The texts are already collected (Telegram post texts in `tg_raw_messages.payload`, YouTube `title`+`description` in `yt_raw_videos.payload`), and PLAN.md's clustering section names semantic embeddings (`multilingual-e5`) as the third axis, beside structure and interaction. A vector layer over the stored texts is a pure derivation: no new collection, no new outbound connection, re-runnable from the raw layer at any time — which is why it is cheap to add now.

## What Changes

- Two derived tables, `tg_post_embeddings` and `yt_video_embeddings`: one row per text, the vector as `bytea` float16, the model named by `model_key` so a model change is a re-embed, not a migration. Telegram texts are immutable in the raw layer, so they embed once; a YouTube video's text is re-embedded when its `content_hash(title, description)` changes on a refresh.
- An `embed` dependency group (fastembed, ONNX runtime, `intfloat/multilingual-e5-small`, 384 dims) and one Dockerfile line: the published image carries the group and the model weights, so embedding never downloads anything at runtime and needs no network beyond the database.
- `itgraph embed`: embeds texts that are missing or stale, in batches, CPU-only; a full backfill of the ~280k existing texts and a nightly increment (~500–900 texts/day, burst-tolerant on new-seed walk days).
- `itgraph search "<query>"`: embeds the query with the same model and ranks texts by cosine similarity in memory (numpy), printing top-N Telegram posts and YouTube videos with channel, date and link. Runs through the existing console Jobs; a bot button is a later change, not this one.
- The vectors ride in the existing backups — the full tier dumps the whole database, and the added ~215 MB (fp16, 384d, ~280k texts) fits the off-site budget (~3–3.5 GB of B2's 10 GB free tier after this change).
- **No** pgvector, **no** Postgres image change, **no** new outbound label: the embed job reaches the database and nothing else.

## Capabilities

### New Capabilities

- `text-embeddings`: what is embedded and from what stored text, how the tables are keyed and refreshed when texts arrive or change, which model and storage shape, and how the search command ranks and presents results.

### Modified Capabilities

- `youtube-data-retention`: video embeddings join the enumerations — they are not removed by age, they go with the video when YouTube stops returning it, and backups carry them.
- `collection-deploy`: the embed job joins the per-job pattern — it runs in every environment (it needs only the database and the image, so the test environment runs it too), on its own schedule input.

## Impact

- **Code**: a new `src/itgraph/embed/` module (model wrapper, batch embedding, upserts, cosine search), two CLI commands in `cli.py`, one Alembic migration creating both tables with FK `ON DELETE CASCADE` onto the raw rows they derive from, and a `db/` writer module.
- **Dependencies**: `pyproject.toml` gains the `embed` group (fastembed + onnxruntime, no torch); `uv.lock` grows accordingly; the image gains ~200–300 MB (runtime + e5-small weights).
- **Deploy**: one CronJob in the role (`itgraph embed`, overnight) with a schedule input, database-only egress — the same shape as the scoring jobs.
- **Database**: ~+215 MB today, growing with the texts; full dumps +~60–130 MB each.
- **Untouched**: both collectors, the raw layer, the backup schedule, the proxy, the network policies' existing labels.
