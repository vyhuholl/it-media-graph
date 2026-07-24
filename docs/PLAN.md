# Project plan

## Core principle

Keep **collection** and **analysis** strictly separate. The collector writes raw JSON to the raw layer and does nothing else; all logic lives in re-runnable transformations on top. Parsing will change (new entities, new metrics), and re-fetching Telegram history is expensive and risky for the account.

## Collection (Telegram)

- **MTProto userbot only** (Telethon). The Bot API cannot read other people's channels or their history.
- **Account strategy.** Dumping the operator's own subscriptions and early experiments run on the main account: reading your own dialog list is what every official client does on startup, so the risk is effectively zero, and an aged account carries high trust with anti-spam. Mass backfill runs on a second number, which first gets a couple of weeks of ordinary use from a phone — a fresh SIM pulling hundreds of channels from a VPS on day two is the most recognizable bot pattern there is.
- **Never join channels.** Public channels are read by username (`get_entity` + `iter_messages`). Mass joining is the single strongest ban trigger. Run from a residential IP rather than a datacenter one, and set realistic `device_model` / `system_version` / `app_version` on the client.
- What to extract from `Message`:
  - `fwd_from.from_id` — **this is the repost graph**, the main asset of the project. This slice alone already produces a result.
  - `entities`: `MessageEntityMention` (@channel), `MessageEntityTextUrl`/`Url` → `t.me/...` — the mention graph.
  - `reactions.results`, `views`, `forwards` — metrics.
  - `replies.channel_id` — id of the linked discussion group.
- **Comments**: a channel has a linked chat; `GetDiscussionMessageRequest` plus iteration over replies. By volume this is 10–100× the posts — a separate phase.
- Treat FloodWait as normal, not as an error: exponential backoff, a persistent per-channel `offset_id` cursor so a backfill is resumable.

## Storage

PostgreSQL, nothing exotic. `raw_messages(channel_id, msg_id, payload jsonb, fetched_at)` plus normalized tables `channels / messages / edges(src, dst, type, ts)`. Separately, **snapshots** `message_metrics(msg_id, ts, views, forwards, reactions jsonb)` — without them there is no spike detection and no retrospective analysis.

No graph database. At the scale of a few thousand channels, Postgres plus an in-memory export to `networkx`/`igraph` covers everything.

## Expanding the seed set — the interesting part

Three automatic sources of candidates:

1. **Snowball over forwards.** Any channel appearing in `fwd_from` is a candidate. The cleanest signal: people repost their own crowd.
2. **`channels.getChannelRecommendations`** — Telegram itself returns "similar channels". This is effectively a ready-made embedding of the community, computed from the behaviour of millions of users. Underrated; use it.
3. **Commenter overlap** (later, once comments exist): a bipartite user↔channel graph, projected onto channel↔channel. Must be TF-IDF weighted, otherwise it drowns in people who comment everywhere.

**Candidate scoring**: how many *distinct* seed channels referenced it × the authority of the referrers (PageRank over the current graph) × recency. Not a plain mention count — otherwise one hyperactive channel drags in all its junk.

**Where AI genuinely helps** — not in the decision, but in preparing it:

- Build a candidate queue sorted by score. For each: a 2–3 line summary of the last 20 posts, sample posts, and who referenced it.
- Accept / reject / maybe, **with a reason for rejection** (not IT / ads / dead / wrong crowd).
- After 100–200 labelled channels there is a dataset. From there — **embeddings, not an LLM**: `multilingual-e5` or `rubert-tiny2` over concatenated recent posts, plus logreg/catboost on those labels. Far cheaper, faster and more accurate than an LLM classifier, because it learns the *project's* definition of "the IT crowd" rather than a generic one.
- Leave the LLM to what it is irreplaceable for: triage summaries, name normalization, working out what a channel is even about.

Build the triage queue as a minimal UI right away (FastAPI + htmx, half a day of work) — labelling through a CLI does not happen in practice.

## Clustering

A weighted directed channel graph, edge weights decaying exponentially over time. Leiden (`igraph`/`leidenalg`), not Louvain. Compute **two independent clusterings** — over forwards and over commenters — and look at where they diverge: the divergence is the substantive result (a channel everyone reposts but nobody discusses is a different kind of node).

Visualization: start with a plain GEXF export into Gephi by hand. A custom view (sigma.js/cosmograph) only once it is clear what is worth looking at.

## Detecting strong reactions

Absolute numbers are useless — a 500k channel and a 3k channel are not comparable. What is needed is a **z-score against the channel's own history at the same post age**: reactions/views N minutes after publication versus that channel's median over the last 30 days. Alert above a threshold.

This requires polling recent posts: every 15–30 minutes for the first 48 hours, decaying afterwards. A simple Postgres-backed queue plus one worker (`arq`) — NATS would be overkill here.

Alert delivery: a dedicated aiogram bot.

## Plan for 2–3 months

| Weeks | What | Artifact |
|---|---|---|
| 1–2 | Collector + schema + backfill of 100 seed channels over 6–12 months | Forward graph v0 |
| 3–4 | Candidates + triage UI + classifier | 300–500 channels |
| 5–6 | Clustering + visualization | **First useful result** |
| 7–8 | Comments (the heavy phase) | Commenter graph |
| 9–10 | Metric snapshots + alert bot | Working notifications |
| 11–12 | Buffer + YouTube Data API | — |

Platform order after that: **YouTube → Threads → Instagram**, not the reverse. YouTube has a proper official API. Instagram and Threads are close to shut for third-party analysis — either grey scrapers that break constantly, or nothing. Assume Instagram may simply not be feasible at reasonable effort.

## Legal note, briefly

Commenter IDs and handles are personal data (152-FZ, and GDPR for any non-Russian scope). Store hashes, do not publish aggregates about individuals, stay at the channel level. Also, scraping is against Telegram's ToS — the account ban risk is the operator's own.