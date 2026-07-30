# Project plan

## What the analytics must answer

Everything below exists to serve three questions. When a design decision is unclear, this is what to check it against.

**Who talks to whom.** Not just volume — *variety*. A channel that reposts forty different people is a connector; a channel that reposts the same two hundred times is an echo. These are opposite roles and a raw count cannot tell them apart. The mirror question is who gets reposted, by how many distinct sources, and from which corners.

**What travels.** Which individual posts get picked up, how far, and how fast. This is post-level, not channel-level, and it is the layer the realtime product sits on: what is being reposted, viewed, reacted to or discussed unusually heavily *right now*.

**How the crowd divides.** Clusters by connection, but also by subject matter, depth and dryness — a channel posting three-line jokes and one posting essays with code sit in different worlds even when they repost each other constantly.

A summary layer over the first two — what is circulating this week and how people are reacting to it — is the point at which this stops being a dataset and becomes something worth reading. It is a bonus, not a prerequisite.

## Core principle

Keep **collection** and **analysis** strictly separate. The collector writes raw JSON to the raw layer and does nothing else; all logic lives in re-runnable transformations on top. Parsing will change (new entities, new metrics), and re-fetching Telegram history is expensive and risky for the account.

The corollary is worth stating separately: **anything derivable can be deferred for free**, and anything not collected costs a re-fetch to recover. Be greedy when collecting, lazy when deriving.

## Collection (Telegram)

- **MTProto userbot only** (Telethon). The Bot API cannot read other people's channels or their history.
- **Account strategy.** Dumping the operator's own subscriptions and early experiments run on the main account: reading your own dialog list is what every official client does on startup, so the risk is effectively zero, and an aged account carries high trust with anti-spam. Mass backfill runs on a second number, which first gets a couple of weeks of ordinary use from a phone — a fresh SIM pulling hundreds of channels from a VPS on day two is the most recognizable bot pattern there is.
- **Never join channels.** Public channels are read by username (`get_entity` + `iter_messages`). Mass joining is the single strongest ban trigger. Run from a residential IP rather than a datacenter one, and set realistic `device_model` / `system_version` / `app_version` on the client.
- What to extract from `Message`:
  - `fwd_from.from_id` — **this is the repost graph**, the main asset of the project. This slice alone already produces a result.
  - `fwd_from.channel_post` — the id of the *original* message. Without it the graph knows that A reposted B but not *what* it reposted, and "which posts travel" is unanswerable. Present in every payload already stored.
  - `entities`: `MessageEntityMention` (@channel), `MessageEntityTextUrl`/`Url` → `t.me/...` — the mention graph.
  - `reactions.results`, `views`, `forwards` — metrics. Note that `forwards` counts every forward anywhere, including into private chats, so it measures reach beyond the observed graph. The two numbers answer different questions and both are worth keeping.
  - `replies.channel_id` — id of the linked discussion group.
- **Comments**: a channel has a linked chat; `GetDiscussionMessageRequest` plus iteration over replies. By volume this is 10–100× the posts — a separate phase.
- Treat FloodWait as normal, not as an error: exponential backoff, a persistent per-channel `offset_id` cursor so a backfill is resumable.
- **Not every request is priced the same, and the difference decides the shape of the tooling.** `messages.getHistory` is cheap: it rate-limits per burst, and a wait is measured in seconds. Two others carry a *per-day* quota, where waiting does not help because the limit counts calls rather than measuring their rate:
  - `contacts.resolveUsername` — the tightest of them, empirically a couple of hundred a day, and it has no batch form. Telethon's own `get_entity` docstring warns that flood waits start "around 50 usernames in a short period". Only `itgraph resolve` is allowed to spend it; a `ResolveUsernameRequest` recorded in `flood_events` under any other command is a regression.
  - `channels.getFullChannel` — rationed too, and what `itgraph metadata` spends. Descriptions and linked chats change on the order of months, so it runs about monthly rather than alongside every walk.
  A history walk spends neither: its peer comes out of the session file's entity cache, and a channel the cache cannot supply is skipped rather than resolved. That is why the daily quota bounds how fast *new* channels enter the graph, not how much history can be collected.

## Storage

PostgreSQL, nothing exotic. `raw_messages(channel_id, msg_id, payload jsonb, fetched_at)` plus normalized tables `channels / messages / edges(src, dst, type, ts)`. Separately, **snapshots** `message_metrics(msg_id, ts, views, forwards, reactions jsonb)` — without them there is no spike detection and no retrospective analysis.

Edges carry both endpoints at message granularity: the referencing message and, for forwards, the referenced one. Channel-level aggregates are then a `GROUP BY` away, while the reverse — recovering post-level detail from channel-level rows — is not.

No graph database. At the scale of a few thousand channels, Postgres plus an in-memory export to `networkx`/`igraph` covers everything.

## Expanding the seed set — the interesting part

Three automatic sources of candidates:

1. **Snowball over forwards.** Any channel appearing in `fwd_from` is a candidate. The cleanest signal: people repost their own crowd.
2. **`channels.getChannelRecommendations`** — Telegram itself returns "similar channels". This is effectively a ready-made embedding of the community, computed from the behaviour of millions of users. Underrated; use it.
3. **Commenter overlap** (later, once comments exist): a bipartite user↔channel graph, projected onto channel↔channel. Must be TF-IDF weighted, otherwise it drowns in people who comment everywhere.

**Candidate scoring**: how many *distinct* seed channels referenced it × the authority of the referrers (PageRank over the current graph) × recency. Not a plain mention count — otherwise one hyperactive channel drags in all its junk.

PageRank is only meaningful where both endpoints of an edge have measured outgoing links. Channels discovered by reference have none until their own history is collected, so authority must be computed over the collected subgraph, never over the full one.

## Channel roles

Volume and variety are different measurements, and the first question the analytics must answer depends on separating them.

For outgoing links, count distinct targets and the spread across them, not the number of edges. A channel forwarding forty different people is doing something structurally different from one forwarding the same two hundred times, and an entropy-style measure over the target distribution separates them where a count does not.

For incoming links, the same asymmetry: reposted by many distinct sources versus reposted often by one patron. The second is a relationship, not influence.

Both need the reciprocity check — mutual forwarding is a peer relationship, one-directional forwarding is an audience relationship — and both should be computed separately over recent and older windows, since roles drift.

## Clustering

The crowd divides along more than one axis, and the axes disagree. Three feature spaces, clustered independently:

- **Structural** — the weighted directed forward graph, edge weights decaying exponentially over time. Leiden (`igraph`/`leidenalg`), not Louvain.
- **Interaction** — commenter overlap, once comments exist.
- **Semantic** — embeddings of recent posts (`multilingual-e5`), giving subject matter rather than connection.

Where they diverge is the substantive result: a channel everyone reposts but nobody discusses is a different kind of node; a cluster tight by links but scattered by topic is a social circle rather than a subject area.

**Depth and dryness** are a separate axis and need no ML at all. Median post length, sentence length, code-block presence, link and term density, emoji rate, how much of a post is quoted versus original — cheap aggregates over messages already stored, and they separate the essay channels from the meme channels far more sharply than embeddings do. Compute them first and see how much of the intuition they already capture.

Visualization: start with a plain GEXF export into Gephi by hand. A custom view (sigma.js/cosmograph) only once it is clear what is worth looking at.

## Detecting strong reactions

Absolute numbers are useless — a 500k channel and a 3k channel are not comparable. What is needed is a **z-score against the channel's own history at the same post age**: reactions/views N minutes after publication versus that channel's median over the last 30 days. Alert above a threshold.

Four independent signals, and they mean different things: views (reach), reactions (approval), forwards (endorsement strong enough to republish), comments (disagreement as often as interest). Forwards are the most valuable and the slowest to accumulate; comment spikes are the ones most likely to be a fight. Track each against its own baseline rather than collapsing them into one score.

This requires polling recent posts: every 15–30 minutes for the first 48 hours, decaying afterwards. A simple Postgres-backed queue plus one worker (`arq`) — NATS would be overkill here.

Alert delivery: a dedicated aiogram bot.

## Digests

Once post-level virality exists, a periodic summary of what circulated and how it was received is the first output a person would read rather than query. This is where an LLM earns its place: input is a handful of top posts plus a sample of the discussion, output is a few paragraphs.

Two constraints decide when this becomes feasible. It needs an API budget — a chat subscription is not a batch inference plan — so it stays small and periodic rather than continuous. And summarizing comments means sending user-written text to a third party, which the legal note below governs: strip identifiers first, summarize the discussion rather than the people.

## Out of scope, deliberately

Repost activity across the wider chat ecosystem — beyond the channels collected here — is better bought than built. Established Telegram analytics services already index it, and a first approximation from one of them costs a subscription rather than an engineering phase. Revisit only if their coverage of this particular crowd turns out to be poor.

## Plan for 2–3 months

| Weeks | What | Artifact |
|---|---|---|
| 1–2 | Collector + schema + backfill of seed channels | Forward graph v0 |
| 3–4 | Candidates | 300–500 channels |
| 5–6 | Clustering + role metrics + visualization | **First useful result** |
| 7–8 | Post-level virality + metric snapshots + alert bot | Working notifications |
| 9–10 | Comments (the heavy phase) | Commenter graph |
| 11–12 | Buffer + digests + YouTube Data API | — |

Comments sit after the alert bot rather than before it. They are the heaviest phase, the first to carry personal data at scale, and the questions they answer are partly covered by external analytics in the meantime — whereas post-level virality reuses history already collected and produces a working product on its own.

Platform order after that: **YouTube → Threads → Instagram**, not the reverse. YouTube has a proper official API. Instagram and Threads are close to shut for third-party analysis — either grey scrapers that break constantly, or nothing. Assume Instagram may simply not be feasible at reasonable effort.

## Legal note, briefly

Commenter IDs and handles are personal data (152-FZ, and GDPR for any non-Russian scope). Store hashes, do not publish aggregates about individuals, stay at the channel level. Also, scraping is against Telegram's ToS — the account ban risk is the operator's own.