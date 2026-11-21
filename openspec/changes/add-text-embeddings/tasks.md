# Tasks

## 1. Feasibility pin

- [x] 1.1 Confirm the inference stack on Python 3.14 before any code: add the `embed` group (`uv add --group embed fastembed`), check that onnxruntime wheels resolve, that `intfloat/multilingual-e5-small` is listed, and with a small local script that a query/passage pair of the same text scores above an unrelated pair — writing down whether fastembed applies e5's `query:`/`passage:` prefixes itself or the wrapper must. If wheels for 3.14 are missing, stop and report to the operator instead of pinning Python around it. Verify: the script's output, recorded in the change's notes or the task conversation.

## 2. Schema

- [x] 2.1 Alembic revision creating `tg_post_embeddings` (composite PK, FK cascade onto `tg_raw_messages`) and `yt_video_embeddings` (PK, FK cascade onto `yt_raw_videos`, `content_hash`), plus SELECT grants for the reader role per the existing role migrations. Verify: `make validate` green with the revision applied by the suite's schema build.

## 3. Embedding core

- [ ] 3.1 Model wrapper (`src/itgraph/embed/model.py`): load from the baked cache path, batch-embed passages, embed a single query, emit float16 little-endian bytes; apply e5 prefixes if 1.1 found the library does not. Tests with a fake backend (no network, no real weights). Verify: pytest for the wrapper, including the prefix decision from 1.1.
- [ ] 3.2 Text extraction: Telegram `payload->>'message'`; YouTube `snippet.title` + `snippet.description` joined, title alone when the description is empty; `content_hash` of the joined YouTube text. Verify: pytest over payload fixtures, including the empty-description case.
- [ ] 3.3 Selection and upsert (`db/embed.py`): one loop iteration selects up to a batch of texts whose embedding is missing, whose `model_key` is not the configured one, or — YouTube only — whose `content_hash` differs; embeds; upserts; commits per batch. Verify: pytest covering the nightly-increment, interrupted-backfill-resumes, model-change-stale and unchanged-hash-not-re-embedded cases.
- [ ] 3.4 `itgraph embed` CLI command wiring the loop, with the model injectable for tests. Verify: pytest over the command with a fake model plus `uv run itgraph embed --help`.

## 4. Search

- [ ] 4.1 Ranking and result assembly: load both tables' vectors and metadata, cosine-rank in numpy, skip foreign `model_key` rows, build links (`t.me/<username>/<msg_id>`, the `t.me/c/…` form without a username, `youtube.com/watch?v=<id>`), print top N with channel, date, text head. Verify: pytest with fake vectors, including the empty-table message.
- [ ] 4.2 `itgraph search "<query>"` CLI command. Verify: pytest over the command with a fake model; `uv run itgraph search --help`.

## 5. Deploy

- [ ] 5.1 Dockerfile: add `--group embed` to both `uv sync` lines and bake the e5-small weights into a fixed path the wrapper loads. Verify: a local `docker build` succeeds and the image contains the weights (listing the path inside the built image).
- [ ] 5.2 Role: the CronJob on the `itgraph embed` command, schedule input defaulting to 04:00 Moscow, database-only egress labels and secrets, bounded resources per the design. Verify: `make ansible-lint`.
- [ ] 5.3 `make test-e2e` on the throwaway k3d cluster: the embedding CronJob exists, its pod carries only the configuration and database URL, and the role applies cleanly beside it. Verify: the e2e run's output.

## 6. Whole-change verification

- [ ] 6.1 `make validate` green over everything above. Verify: command output.
- [ ] 6.2 Smoke with the real model against a scratch database (developer machine, test data only): run `itgraph embed` over a small fixture corpus, then two or three `itgraph search` queries whose expected hits are known, one Russian over Russian texts. Verify: the searches return the expected texts in the top results.

## Workflow follow-up

- Release tag, then the operator deploys the role at the pin through the infrastructure repository; the first scheduled run is the production backfill (~30–50 min on the host).
- A week after deploy, check the off-site tier sizes against the B2 free budget, as with any change that grows full dumps.
