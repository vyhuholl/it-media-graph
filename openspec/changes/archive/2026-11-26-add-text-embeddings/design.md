# Design

## Context

The texts are already stored: Telegram post texts in `tg_raw_messages.payload->>'message'` (immutable — the first fetch wins), YouTube `snippet.title` + `snippet.description` in `yt_raw_videos.payload` (replaced by refreshes). This change adds a derived layer over them, so it changes no collector and touches no network path the project does not already have. Measured corpus: ~280k texts (213,661 TG, 65,977 YT), p50 length 492/788 chars, daily increment ~500–900 with bursts to ~9k on new-seed walk days. The node runs the job comfortably (24 threads, 62 GiB RAM, 4–5% allocated).

## Goals / Non-Goals

Goals: one command embeds everything missing or stale and keeps up nightly; one command searches both platforms by meaning; a model change is a re-embed, never a migration.

Non-goals: a bot button (later change), semantic clustering (reads the same table, separate change), threshold-filtered search (top-N only), chunking long texts into several vectors (head only), pgvector / ANN indexes (brute force over 280k is milliseconds), any change to collectors, Postgres, or network policies.

## Decisions

### Model: `intfloat/multilingual-e5-small`, 384 dims, ONNX directly

Topic-level search in mixed Russian/English is what e5 was built for, and PLAN.md already names it. The small variant halves storage and compute against base with little loss at "what is it about" granularity; `model_key` on every row makes an upgrade to base a re-embed, not a redesign.

**The original plan — ONNX via fastembed — did not survive apply.** fastembed's registry does not carry `intfloat/multilingual-e5-small` (its only e5 is `multilingual-e5-large`, 1024 dims, 2.2 GB), and `TextEmbedding` refuses unlisted models, so the design as written would have forced the large model. The operator chose to keep e5-small and drop fastembed: the wrapper loads the canonical repo's own ONNX export with onnxruntime + tokenizers, both of which fastembed had pulled in anyway — the no-torch property fastembed was chosen for is kept, with ~80 lines of inference glue the project owns. Verified on apply (2026-11-21): wheels resolve on Python 3.14.3, `model_O4.onnx` (int8, 235 MB) runs on arm64 and scores within 0.0002 cosine of the fp32 export on a query/passage probe in both languages, same-subject pairs rank above unrelated ones, and the wrapped prefixes are ours to apply. The fp32 export (470 MB) stays the fallback; the avx512-only variant is excluded because the developer machine is arm64.

e5 needs `query:` / `passage:` input prefixes and **the wrapper applies them itself** — fastembed's `query_embed`/`passage_embed` are thin wrappers over `embed` that add nothing (verified in source), so direct onnxruntime loses nothing here either.

### Storage: `bytea` float16 vectors, no pgvector

The live Postgres (16.15, official Debian image) has no `vector` extension compiled in; enabling it means swapping the role's `itgraph_postgres_image` input — an infra change for a problem that does not exist: 280k × 384 float16 is ~215 MB, and a numpy cosine over it is single-digit milliseconds. This is also the project's established shape — graphs load into igraph in memory, vectors load into numpy. float16 over float32: half the bytes, no measurable recall cost at this scale.

### Tables

```
tg_post_embeddings
  channel_id, msg_id   composite PK, FK -> tg_raw_messages ON DELETE CASCADE
  model_key  text      e.g. 'multilingual-e5-small'
  vec        bytea     384 float16, little-endian
  embedded_at timestamptz

yt_video_embeddings
  video_id   PK, FK -> yt_raw_videos ON DELETE CASCADE
  model_key, vec, embedded_at
  content_hash text   hash of the joined title+description that was embedded
```

The FK cascades mirror `yt_raw_video_stats`: a video YouTube stops returning takes its embedding with it (the retention delta), and a Telegram message has no deletion path today, so TG needs no hash — the raw text cannot change under the embedding. A future TG deletion path would add its own rule then.

### Text extraction and truncation

TG text: `payload->>'message'`. YT text: `title`, blank line, `description` (93.7% of videos have one; a video with none embeds the title alone). Texts longer than the model's 512-token window are head-truncated at tokenization — explicit `max_length`, not the library default, so behaviour is pinned. One text, one vector.

### The job: `itgraph embed`, no flags

Every run does the same thing: select up to a batch (256) of texts whose embedding is missing, whose `model_key` differs from the configured model, or — YouTube only — whose `content_hash` differs; embed; upsert; commit; repeat until nothing is due. The first run on production is therefore the backfill, an interrupted run is resumed by the next, and a model change re-embeds the corpus with no special mode. CronJob daily at 04:00 Moscow (schedule input like every other job), database-only egress, pod limits generous but bounded (e.g. 4 CPU / 4 Gi) — the node is 4–5% allocated, so the job is a good neighbour at 4 ONNX threads.

### Search: `itgraph search "<query>"`

Embeds the query with the same configured model, loads both tables' vectors plus the metadata needed to print results (channel name, date, text head, link), ranks by cosine in numpy, prints the top N (default 20). Links: `https://t.me/<username>/<msg_id>` (or the `t.me/c/…` form for channels without a username), `https://www.youtube.com/watch?v=<id>`. Runs through the existing console Jobs — it holds the main database URL, like every other operator command, and reaches nothing else. Vectors of a different `model_key` than the configured one are not ranked (the spec's stale rule re-embeds them on the next job run anyway).

### Dependencies and image

`pyproject.toml` gains an `embed` group (`numpy`, `onnxruntime`, `tokenizers`, `huggingface-hub` — the last only to fetch weights at build and on dev machines; the runtime loads from the baked path and never downloads, which the job's egress denies anyway, making it a hard guarantee); the Dockerfile's two `uv sync` lines gain `--group embed`. The `data` group on developer machines is unaffected.

## Risks / Trade-offs

- [onnxruntime has no wheel for Python 3.14] → checked first in apply: wheels resolve and run on 3.14.3, both arm64 (dev) and x86-64 (image).
- [fastembed does not apply e5's query/passage prefixes] → verified in source: it does not, and the wrapper applies them. Moot for the stack itself since fastembed was dropped (see the model decision).
- [small model misses nuance on short slang posts] → accepted for topic-level search; upgrade path is a `model_key` bump plus one re-embed run.
- [float16 rounding shifts cosine ranks slightly] → irrelevant at top-20 granularity; measured, not assumed, if search quality ever looks off.
- [burst day adds ~9k texts] → the loop shape absorbs it: minutes of work at batch 256 on 4 threads; no run ever exceeds what the next can finish.
- [embeddings inflate full dumps] → ~+60–130 MB per dump at fp16 — fits the B2 budget (~3–3.5 GB of 10 GB after this change), and a worst case is re-embeddable from texts, so the dumps lose nothing irreplaceable.

## Migration Plan

One Alembic revision creates the two tables (and grants SELECT to the reader role, following the existing role migrations). The pre-migration whole-database dump the deploy already takes covers rollback; embeddings are derived, so a restore without them is completed by one `itgraph embed` run. Deploy order is the usual: image with the `embed` group → role at the new tag → migration → the first job run backfills.

## Open Questions

None blocking. Deferrable to apply: the exact top-N default (20 unless the operator prefers otherwise) and whether `search` grows platform filters before a bot button exists.
