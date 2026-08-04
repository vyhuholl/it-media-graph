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
- **Never join channels.** Public channels are read by username (`get_entity` + `iter_messages`). Mass joining is the single strongest ban trigger. Set realistic `device_model` / `system_version` / `app_version` on the client.
- **The address must be shared with people — which is not the same as residential**, and this line originally said the wrong thing. Telegram is blocked here, so every connection to this account has always gone through a commercial VPN, whose exit is a datacenter address; on that footing it walked 211 thousand messages in eleven days and hit three rate limits, all of them `ResolveUsernameRequest` against its daily quota. Zero on `messages.getHistory`, which ran thousands of times. So a datacenter address is not itself the problem. What would stand out is an address nothing human ever comes from — a rented machine's own IP, used by one collector and nothing else. A VPN exit shared with a large number of ordinary users is what the account already has and what a move to a VPS has to preserve. Prefer a stable exit to a rotating one: changing address on every connection is stranger than changing region occasionally.
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
  A history walk spends neither: its peer comes out of the session file's entity cache, and a channel the cache cannot supply is skipped rather than resolved. That is why the daily quota bounds how fast *new* channels enter the graph, not how much history can be collected. The poll loop spends neither for the same reason and with more at stake — a leak there would spend the day's quota every day rather than once.
- **One process holds the session.** Once collection includes something that runs continuously, this stops being obvious and starts needing enforcing: two processes on one session file is a corrupted file and possibly a revoked authorization. Every networked command takes an exclusive lease before connecting and refuses when it is held, so a backfill started next to a running loop declines instead of racing it.

## Storage

PostgreSQL, nothing exotic. `raw_messages(channel_id, msg_id, payload jsonb, fetched_at)` plus normalized tables `channels / messages / edges(src, dst, type, ts)`. Separately, **snapshots** `message_metrics(msg_id, ts, views, forwards, reactions jsonb)` — without them there is no spike detection and no retrospective analysis.

The snapshot layer grows faster than its shape suggests, and the reason is worth knowing before someone budgets for it. A poll reads *every* live post of a channel, and the channel's due time is the earliest over them — so a post is sampled whenever any of its neighbours is due, not only at its own scheduled offsets. Measured: 2.6 readings per post on a channel with one live post, 15.6 on one with a dozen. That is ~38 thousand rows a day, ~14 million a year, against the 2 million a naive reading of the schedule predicts. It costs no extra requests, only rows — a few gigabytes a year, which Postgres does not notice, but it is 6× the arithmetic anyone would do from the schedule alone.

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

Absolute numbers are useless — a 500k channel and a 3k channel are not comparable. What is needed is a **z-score against what a post of that age normally reaches on that channel**.

**"That channel's own history at the same post age" turns out to be unreachable, and this is the plan's most consequential correction.** The median seed channel publishes 0.53 posts a day. A median worth dividing by needs on the order of thirty posts, which is fifty-seven days *per channel* — and the channels that would take longest are most of the inventory. Waiting does not fix it; nothing does.

What replaces it is a decomposition. A **shared growth curve** — what fraction of its value a metric has reached at age *t* — estimated over all channels at once, multiplied by the **channel's own mature baseline**, which the 211 thousand already-collected messages supply today. Curves need posts, not posts-per-channel, so they are affordable where per-channel baselines are not.

Measured over 353 posts tracked for at least eight hours, as a fraction of the eight-hour value:

```
                 15m   30m    1h    2h    4h    8h
  views          17%   22%   33%   50%   76%   96%
  reactions      27%   33%   47%   67%   85%   98%
  forwards       35%   45%   56%   73%   89%  100%
  comments       40%   40%   76%   85%  100%  100%
```

Three things follow, and two of them contradict what this section used to say.

**Each metric needs its own curve.** At one hour a post has a third of its views and over half its forwards. One curve applied to four metrics would be wrong for three.

**Forwards are not the slowest to accumulate — early on they are among the fastest.** That claim held against *maturity*, over weeks; inside the window an alert actually uses, forwards front-load relative to views. The direct consequence: `forwards / views` is **not** age-free early. Measured, it runs about 0.0146 at fifteen minutes against 0.0078 at eight hours — nearly double. Scoring a young post's forward rate against a mature baseline therefore *over*-alerts, which is the dangerous direction: false alarms are what destroy trust in an alert bot, and this was assumed to err the safe way round.

**The curve varies by channel kind, and only there.** Aggregators, personal channels and media cluster together; vacancy feeds are visibly slower (52% of views at four hours against 72–81%), which fits — a job feed is read when someone is looking for work, not when it is posted. So the curve is per `ChannelKind`: enough data per kind, and `kind` is already hand-reviewed in the inventory.

Four independent signals, and they mean different things: views (reach), reactions (approval), forwards (endorsement strong enough to republish), comments (disagreement as often as interest). Comment spikes are the ones most likely to be a fight. Track each against its own baseline rather than collapsing them into one score.

This requires polling recent posts. As built and measured: samples at 15, 30, 60, 120, 240, 480, 1440 and 2880 minutes after publication, then never again; a per-channel due time; and a single sequential worker behind a Postgres queue — NATS would be overkill here. The whole inventory costs **3–4 thousand `messages.getHistory` calls a day**, roughly one request every 20–25 seconds, which is gentler than the backfill that preceded it. One request serves both jobs: a history window returns new posts *and* current counters for everything still live, so cost is per channel per cycle rather than per post.

**Collection and judgement are separate phases, and the split is a schedule decision rather than a taste one.** The baselines above are of the form "what this channel's posts normally have at age *t*", and they cannot be computed, borrowed or backfilled — they accumulate in wall-clock time. So the snapshot loop went first and alone (`itgraph watch`), and everything that scores those snapshots can be written afterwards, at any point, as a pass with no deadline of its own. The interim payoff is real: the offline analytics stop being restricted to posts that were already mature when they were read.

The corollary is a rule any scoring pass has to obey. Samples are irregular by design — quiet hours, suspend and rate limits all cost readings, and a missed one is dropped rather than taken late — so **a snapshot's age is `observed_at` minus the publication date, never which sample in the schedule it was meant to be.**

The queue is taken from the suggestion above; `arq` is not. It is a Redis job queue, and adding Redis to schedule a single sequential worker contradicts the Postgres-only rule for no gain — the whole dispatch is one indexed `due_at` query. Concurrency is the one thing this must not have anyway: Telegram's limits are per account, so parallel workers reach the same ceiling faster and look worse doing it.

**Measured, once the graph existed: the cascade signal is real and thin.** Counting distinct unaffiliated families carrying one post, over the densely collected last 30 and 60 days — one family is ~19 events a day and is noise; two is ~1.1; three is ~0.35; four or more happened once in two months. There is almost no band between noise and silence. So "what is being reposted right now" is a genuine signal and not, on its own, a product — which is why post-level *virality* rather than post-level *travel* is what carries the realtime claim above, and why the delivery machinery was built before the scoring that will fill it rather than alongside.

Alert delivery: a dedicated aiogram bot, in its own dependency group and its own process, reading an alert table. That interface is the seam that lets the bot move to a different machine later without the collector following it — the collector's connection is the one with something to lose, and the Bot API's is not.

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
| 7 | Metric snapshots: the poll loop, the time series it writes | Baselines start accruing |
| 8 | Alert delivery + repost cascades — the one signal needing no warm-up | A bot that speaks ~once a day |
| 9 | Post-level virality: growth curves, scoring, replay | Notifications with volume |
| 10–11 | Comments (the heavy phase) | Commenter graph |
| 12 | Buffer + digests + YouTube Data API | — |

Delivery shipped a week before the scoring that fills it, and deliberately: the cascade signal needs no warm-up but produces about one alert a day, so the machinery that is unpleasant to debug in production — deduplication, caps, the digest, retries — got exercised on a trickle while the growth curves accrued. The cost is a fortnight of a quiet bot, which is cheaper than debugging delivery and thresholds at the same time on data that does not exist yet.

Comments sit after the alert bot rather than before it. They are the heaviest phase, the first to carry personal data at scale, and the questions they answer are partly covered by external analytics in the meantime — whereas post-level virality reuses history already collected and produces a working product on its own.

Platform order after that: **YouTube → Threads → Instagram**, not the reverse. YouTube has a proper official API. Instagram and Threads are close to shut for third-party analysis — either grey scrapers that break constantly, or nothing. Assume Instagram may simply not be feasible at reasonable effort.

## Legal note, briefly

Commenter IDs and handles are personal data (152-FZ, and GDPR for any non-Russian scope). Store hashes, do not publish aggregates about individuals, stay at the channel level. Also, scraping is against Telegram's ToS — the account ban risk is the operator's own.