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
- **The address must be shared with people — which is not the same as residential**, and this line originally said the wrong thing. Telegram is blocked here, so every connection to this account has always gone through a commercial VPN, whose exit is a datacenter address; on that footing it walked 211 thousand messages in eleven days and hit three rate limits, all of them `ResolveUsernameRequest` against its daily quota. Zero on `messages.getHistory`, which ran thousands of times. So a datacenter address is not itself the problem. What would stand out is an address nothing human ever comes from — a rented machine's own IP, used by one collector and nothing else. A VPN exit shared with a large number of ordinary users is what the account already has and what a move to a VPS has to preserve. Prefer a stable exit to a rotating one: changing address on every connection is stranger than changing region occasionally. On the server this is met inside the collection namespace: every MTProto connection goes through a proxy whose only exit is the same VPN server the laptop used — so the move changes nothing about the address Telegram sees — and a proxy that cannot be reached stops the command rather than falling back to the server's own address (`src/itgraph/README.md`, "Прокси").
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

**So the thing scored is a level, never a ratio.** A ratio of two metrics looks age-free and is not — that is what the forwards measurement above shows — and scoring one against a mature baseline corrects for age twice, in opposite directions. Each metric is compared to its own curve instead, so age is corrected exactly once:

```
  expected(age) = channel's mature median × factor × curve(age, kind, metric)
  z             = log(actual / expected) / spread
```

The remaining three numbers were measured rather than assumed, and none of them turned out to be shareable between metrics:

```
  metric      channels  posts  factor  spread
  views          465     586    0.44    0.38
  reactions      436     424    0.68    0.75
  forwards       454     562    0.63    0.77
  comments       304     184    1.00    1.01
```

The `factor` is the join between a curve normalised to eight hours and a median computed over thirty days; the `spread` is the dispersion of `log(actual/expected)`, which is what makes the score a z rather than a multiple. Hardcoding views' 0.38 across all four would have fired reaction alerts at half the intended distance into the tail — which is why the spread is stored beside each baseline rather than written into the code.

**Two corrections, measured five days later on 137 thousand readings.**

*The spread is not flat across ages*, which this section originally assumed. It runs 1.18 at fifteen minutes against 0.98 at eight hours, so one figure per metric made the threshold stricter at some points of a post's life than at others without saying so. It is now fitted per age band, with the per-metric figure kept as the fallback for a band too thin to measure its own.

*The channel median needs an upper bound as well as a lower one.* With only "at least 28 days old", the median contributing post was 183 days old, the 90th percentile 332, the oldest eight years — so a channel that had grown was scored against the channel it used to be. Measured, per-channel bias ran from +1.87 to −4.48 z, with 5% of channels biased by more than the alert threshold itself. Bounding the window to 28–120 days cuts the 95th percentile of that bias from 3.23 to 2.30, at the cost of the channels too quiet to fill it: 382 scorable rather than 465. The post minimum drops from 30 to 20 to pay part of that back, and the two are one decision rather than two settings.

The age bands themselves were the larger error. Six narrow windows around the collection offsets covered 273 minutes of a 49-hour horizon, so 84% of readings were discarded and 840 of 3 459 posts were never scored at all — and not at random: 80–88% of posts published at midday got scored against 42% of those published at 03:00. They are now contiguous from ten minutes to the end of the alerting window.

Correcting all three rescaled the score underneath the threshold, so **the threshold was re-priced on the same week: 3.3, not 3.0**, which reproduces the same volume of about six alerts a day. Fixing the reference and re-tuning the threshold in one step would have made it impossible to say which did what, so the order was fix, measure, then price.

**Comments are measured but do not alert, and the reason is now structural rather than statistical.** Re-measured on twelve times the data — 2 301 posts against 184 — the median z moved only from −0.74 to −0.64 and the spread not at all, from 1.10 to 1.11. More collection will not fix it, because the problem is the instrument:

```
  share of readings that are exactly zero
    comments   44.2%
    views       0.0%
```

A log-ratio against a median cannot describe a counter that is zero in nearly half its observations. The baseline is computed anyway, so the measurement keeps accumulating; what enabling comments actually needs is a different model — whether a post drew any discussion at all, scored separately from how much.

One threshold across all metrics — z 3.0, about nine alerts a day — because dividing by each metric's own spread is exactly what makes a z comparable, and tuning a threshold back per metric would undo it. One band rather than several: between z 3 and z 4 the volume falls only from nine a day to six, so a second band would say almost nothing about six of the nine posts that already alerted.

**The unit of an alert is an event, not a post — measured once the bot had run for two months.** 81 of 196 spike alerts over 14 days arrived in same-channel series: when a whole channel's audience grows at once — an ad, a broadcast, bought views — every post younger than the scoring window crosses together, and the scorer, which relates no post to its siblings, told one event as N. One channel received a near-identical injection of +400…+1500 views on every post of the day, most evenings, and produced 66 of those messages. Scoring each post against its siblings was priced and rejected: the injection is additive, so a young post's log-ratio still stands out of its elders'. What replaced it is folding: a channel's first spike heads the event and every spike of that channel within six hours of it is recorded but told only as a count in the head's message. Six hours is measured — the widest gap inside one event was 5.5 h, and everything 12–24 h apart was a separate event. Two amplifiers went with it. A post published as several messages under a minute apart (text, then each video) is scored once, as an album is; the density of inter-message gaps reaches the background rate of separate posts at about a minute. And a band's own spread now needs 100 distinct posts, not only 20 readings: a spread from few posts is biased low — 0.52 of the full estimate at the 5th percentile for 20 posts, 0.78 for 100 — and one such band gave a broadcast day 13 alerts at ×1.4 of normal. Together: 14.0 messages a day to 8.1, and no series.

**"Unreachable" was true of July, not of the problem — and the decomposition that replaced it had quietly stopped working.** The mature median selects posts *read* at 28–120 days of age; the raw layer keeps the first fetch and the watch loop fetches at age zero, so after the backfill no post ever qualified again, and every weekly refresh produced the same medians. Rolling out of sample over 28 days, 72 of 229 channels sat more than 1 z off zero — growth on one side, and on the other channels whose posts keep accruing for weeks, which the kind-wide factor misdescribes and which no fresh median would have fixed. By October the watch loop had 80 days of eight-hour readings, and the channel's own history at the same post age was simply there to be measured: the median eight-hour value of its newest twenty posts within 90 days, refreshed daily, puts the per-channel bias MAD at 0.15 against 0.81, with three channels beyond 1 z. A window of days lagged a fast-growing channel by half the window; counting posts follows it. The decomposition stays as the fallback for channels too quiet to fill twenty posts, and is counted as such — over two weeks they produced 2 of 196 alerts. Taking per-channel bias out of the residual narrowed the spreads (personal views at eight hours 0.395 → 0.183, aggregator 0.230 → 0.178), so the threshold was re-priced on the same fortnight, as before:

```
  messages a day after folding, each post against the reference its day's refresh would have stored
  z      3.3   3.5   3.7   3.9   4.1   4.3   4.5   4.9   5.5
  /day  10.3   9.0   7.8   7.1   6.2   5.3   4.9   4.2   3.5      (8.0 from the frozen medians at 3.3)
```

3.7 keeps the volume; what fills it changes — channels the stale median had biased upward drop out, and posts that really jumped on their own channel take their place.

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

Platform order after that: **YouTube → Threads → Instagram**, not the reverse. YouTube has a proper official API. Instagram and Threads are close to shut for third-party analysis — either grey scrapers that break constantly, or nothing. Assume Instagram may simply not be feasible at reasonable effort. X sits outside this order for a different reason: it is priced rather than closed (see "X, and why it waits" below).

## YouTube

The task as received on 2026-10-25: do for English-language IT YouTube and X what this project did for Russian-language IT Telegram — an inventory of 500+ channels or accounts each, and alerts on what is being watched, liked or discussed unusually heavily right now, delivered to a Telegram chat separate from the Telegram one. The small Russian-language IT YouTube and X come along as a by-product. YouTube goes first; X waits, for reasons of money rather than engineering. Spending is meant to be as close to zero as possible, and that one constraint decided the order.

The seed set is assembled by hand, about a hundred channels from outside suggestions, because the operator's own subscriptions — the source of the Telegram seed — contain no English-language IT YouTube. A hand-assembled list from a language model carries renamed, dead and invented handles, so every entry is resolved through the API before it is stored, and what does not resolve is refused before anything else is spent.

### Policy before engineering — the decision that has to come first

The YouTube API Services Developer Policies collide with three things this plan treats as settled, and the collision is a decision for the operator and the manager, **not covered by the requirements**. It precedes the first change, because even the inventory stores channel titles.

- **Nothing read with an API key may be kept longer than 30 days without being refreshed (III.E.4.d).** Titles, descriptions and channel names are "Non-Authorized Data": readable without the channel owner's credentials, and storable only temporarily — after 30 calendar days they are deleted or re-fetched. A raw layer where the first fetch wins forever is exactly what this forbids. Refreshing is cheap: a year of uploads for 500 channels is on the order of 75 thousand videos, 1.5 thousand units per 30-day cycle at 50 videos a unit. So it is a schedule rather than a blocker — but a YouTube raw layer is refreshed in place, a video deleted upstream is deleted here, and the root `CLAUDE.md` invariant "the raw layer is immutable" needs a YouTube-specific statement.
- **Statistics read without authorization may not be stored beyond 30 days (III.E.4.b).** Replacing a mature video's count with its current one is a refresh and allowed; keeping the *history* of counts is not. Under the base rules the snapshot layer is a rolling 30-day window.
- **No derived metrics, no aggregation across owners (III.E.4.h, III.E.2).** A z-score against a channel's expected value is a derived metric; a growth curve shared across channels aggregates data of different content owners. Under the base rules the scorer itself is the violation, whatever its retention. The clause is wider than metrics: an API client must not "access or use API Data to create new or derived data or metrics" (III.E.4.h(ii)). Language detected from titles is derived data under that reading, which is why the inventory's language is set by hand; and so are edges parsed out of descriptions, so the mention graph (change 4) has to decide whether its edges inherit the 30 days.

The way out is the *Additional policies on derived metrics and data storage*, added on 2026-05-04 and in force since 2026-06-01. They apply to audited developers with an analytics use case who applied through the standard quota extension request form. Custom scores, rankings and leaderboards ("Top YouTuber by View Growth" is the policy's own example) become permitted, and statistics and derived metrics may be kept for up to 36 months. Titles, descriptions and comment text stay under the 30-day rule regardless. What is not known: the review criteria, how long it takes, and whether an internal tool — a team chat with no public interface — is accepted at all. The policy draws no line between internal and public use. The derived-metrics page itself, updated on 2026-09-14, describes acceptance as an amendment taken through the form's "Section 5: Use Cases, API Integration, and Feature Implementation" → "Analytics & Reporting", and does not mention the audit the main policy page and the revision history speak of. Most likely the same form; to be read again when applying.

The options, then: apply, and run under the base rules until accepted (30-day snapshot window, refreshed raw layer, scoring held back); or proceed without applying and accept the risk the policy names — quota reduction or termination of API access for the project. Splitting the work across several API projects to escape either is out: one API client gets exactly one project (III.D.1.c), the same principle as never switching Telegram accounts to escape a limit.

**Decided on 2026-10-25: apply**, once the inventory gives the application something to describe. The project belongs to the operator's personal Google account, so the application is made in the operator's name; moving it to a company account later is possible and not planned. Until it is accepted the project runs under the base rules, and the inventory was built to be compliant under both: what YouTube says about a channel lives in `yt_raw_channels`, refreshed within 25 days and deleted at 30, and never in a backup; what the operator decided lives in `yt_channels` and is kept. The channel id is kept with the record — the policies say nothing about identifiers, and treating the id as one is the operator's reading, not a policy statement. The root `CLAUDE.md` states the exception this makes to "the raw layer is immutable".

**Filed on 2026-10-25**, as an individual, through the Audit and Quota Extension Form: Analytics & Reporting, the derived-metrics and data-storage acknowledgement checked, no quota increase asked for. No answer yet. The website it points at is `docs/`, served by GitHub Pages — a homepage, a privacy policy and terms of use. What the application and those pages promise binds the code from here on: statistics are kept at most 30 days until it is accepted; no YouTube data is scored until then; itgraph's scores are labelled as its own and shown beside the counts YouTube reports; alerts name YouTube as the source; and `docs/privacy.html` lists the fields itgraph reads, so a change that reads more updates that page with it.

**The key moved to the application's project on 2026-10-26.** The API key itgraph had used from the start was created by mistake in another of the operator's Google Cloud projects, not in the one the application names; it was found the day after filing. An API client gets exactly one project, and that project's credentials serve that client alone (III.D.1.c), so the key was replaced rather than kept: a new one in the itgraph project, restricted to the YouTube Data API v3, and the old one deleted. Data already read with the old key needs nothing — it belongs to the same client and stays under the same 30-day rule. The policies say nothing about moving a client between projects; if the review asks why the project's usage starts only now, this is the answer. The key is not yet restricted by address: on the laptop the collector reached YouTube from a VPN exit that moved; from the server it goes direct from one fixed address, and the restriction follows the move.

### What carries over, and what does not

**Collection is the Data API v3 with an API key, and nothing in the Telegram account-safety section applies.** No user account, no session file, no lease against one, no shared-exit requirement. The laptop's always-on VPN covered reachability; from the server YouTube is reached direct, from the host's own address — nothing about it needs hiding. The API key is a secret on the same footing as the api hash.

**The budget is quota, not rate.** 10,000 units a day per project. `channels.list`, `playlistItems.list` and `videos.list` cost one unit per page of up to 50, an invalid request included. `search.list` does not draw on the 10,000 at all: it has a bucket of its own, 100 calls a day at one unit each — this paragraph used to say 100 units a call, which the quota documentation contradicted when checked on 2026-10-25. Quota resets at midnight Pacific time, so `quotaExceeded` means stop until the reset — waiting and retrying is the one response that cannot work, the same shape as Telegram's per-day quotas. Every networked command spends from one ledger, and the poll loop has a share the others cannot eat: as built, a 10% margin against the console's own count and 3,000 units held for the loop, leaving 6,000 for everything else. A pool follows from the command, and neither borrows from the other. Estimated, not yet measured: a year of history for 500 channels is about 3 thousand units, once; the 30-day refresh is about 1.5 thousand a cycle. That leaves headroom everywhere except search, which is the scarce resource and belongs to discovery alone.

**The poll loop's cost is discovery, not readings — and this paragraph used to price only the readings.** "About a thousand live videos every 15 minutes is about 2 thousand a day" is right for the snapshots (fifty videos of any channels to a unit) and silent on finding the videos. `playlistItems.list` is a unit per channel per call with no batch form, so listing every channel's uploads every 15 minutes is 9,600 units a day for 100 channels and 48,000 for 500 — the whole quota several times over. What change 2 built instead: every pass asks for every seed through `channels.list`, fifty to a unit, and lists a channel's uploads only when its public video count has moved — plus once a day whatever the count says, because the count is a trigger rather than the truth (an upload and a deletion in one interval cancel out, and how fast it moves is undocumented). That is ⌈N/50⌉·96 + U·N + N units a day, with U uploads per channel per day: 342–442 for 100 channels and 1,710–2,210 for 500 at U = 0.5–1.5, and 1,830–2,500 for 500 with the readings. The 3,000 reserve therefore stays, to be checked against two weeks of `yt quota`; which path found each video is recorded, so how often the count misses is measured rather than assumed. Rejected on the way: RSS `feeds/videos.xml` (not the API — III.E.6 forbids scraping and III.I.14 any technology other than the API Services for API data — and intermittently failing since December 2025), WebSub (documented, but it needs a callback the host's firewall does not admit), `activities.list` (a unit per channel like `playlistItems`, not a declared method), `search.list` (discovery's 100 calls), and deriving the uploads playlist id from the channel id (works, and is undocumented: III.D.7).

**Quota is granular now.** Since 2026-06-01 `videos.insert` and `search.list` have buckets of their own, and since 2026-06-03 `videos.batchGetStats` — statistics, publication time and duration for a list of ids, a unit a call — has one too, 10,000 a day. It would move the poll loop's readings off the main bucket and return no titles at all; it is not used yet, because it is new, its id limit is undocumented, it is in neither the application nor the site, and the ledger would need exhaustion per bucket rather than per day. The main bucket is not tight; this is the option if it becomes so.

**Separate tables, not a platform column.** `tg_id bigint` is a primary or foreign key in about a dozen tables, `alerts` among them (it references `raw_messages`), and YouTube ids are strings — `UC…` for a channel, eleven characters for a video. Generalizing means migrating the working database under a live bot to gain nothing. What is shared is code, and only the pure part: `scoring/`, `schedule.py`, the bot's claim–send–mark loop. YouTube tables carry a `yt_` prefix; collection lives in `src/itgraph/yt/`, as Telegram's does in `tg/`.

**The graph is thin, and the product is the alert.** YouTube has no repost. What references remain: `@handles` and channel links in descriptions, and links to another channel's video — a post-level edge, the nearest thing to a forward. Featured channels come through `channelSections` (whether the API still returns them is unverified). There is no counterpart to `channels.getChannelRecommendations`. So the snowball will be slower than Telegram's and reaching 500 will take more review by hand, and "who talks to whom" will be answered thinly. "What travels" is answered at full strength.

**Three signals, not four.** Views, likes and comments; there is no forward. Each gets its own curve, as on Telegram, and the curves are measured, not borrowed: YouTube views accrue over days rather than the eight hours a Telegram post takes. As built in change 2, a video is read at discovery and then at 1, 2, 4, 8, 12, 18, 24 and 36 hours and 2, 3, 4, 5, 7, 10, 14, 21 and 28 days, with a 29-day horizon. Retention does not bound it — a reading at day 28 may legally be kept to day 58 — usefulness does: under the base rules a video's whole trajectory exists only until its earliest reading turns 30 days old. Checking those choices against the data is itself analysis of statistics across content owners (III.E.2, III.E.4.h), so it waits for acceptance. Shorts are a separate population — since 2025-03-31 their `viewCount` counts every play and replay with no minimum watch time — and the API has no flag that says a video is a Short, only heuristics. The undocumented `UUSH`/`UULF` playlists are ruled out by III.D.7 and a request to `/shorts/` by III.E.6; and the derived-metrics page lists "content categorization and tagging" among what acceptance permits, which places any format classification in change 7, after it. The poll loop collects every format and records none. Since 2026-08-24 the API reference says the same of every format: long-form, live and Shorts count a view "the moment a video begins to play", autoplay and hover included. So Shorts no longer differ by how a view is counted, only by how they are watched — and every view count is inflated by autoplay, which a curve measured on data after that date absorbs and one borrowed from before it would not. Streams and premieres have their own notion of when a video started.

**The channel's own reference takes months, not weeks.** At one to three uploads a week, twenty of a channel's own videos at the same age take months of watching to collect. Hence the order below: the poll loop goes second, before the history walk, because it is the only part whose value accrues in wall-clock time, and the decomposition fallback carries scoring for longer than it did on Telegram.

### Alerts

The same bot, a separate chat per platform. YouTube gets its own queue table — the existing `alerts` table references Telegram's `raw_messages`, and an alert about a post nothing collected should stay unrepresentable — and its own chat id beside `alert_chat_id`. Where alerts about Russian-language YouTube go — the YouTube chat, a chat of their own, or nowhere — is open.

### Order of changes

| # | Change | What it delivers |
|---|---|---|
| 1 | `add-youtube-inventory` | API client, quota ledger, `yt_channels` and `yt_raw_channels`, import, review by file from the operator's machine over SSH, the 30-day refresh |
| 2 | `add-youtube-snapshots` | The poll loop: uploads noticed by video count with a daily check, readings from an hour to 28 days, videos and readings under the 30-day rule, a fifteen-minute timer, a dormant switch for acceptance |
| 3 | `add-youtube-backfill` | A year of uploads per channel; the mature baseline the fallback needs |
| 4 | `add-youtube-mention-graph` | Description references → edges and pending mentions; no network |
| 5 | `add-youtube-discovery` | Resolving pending mentions, featured channels, search; the loop to 500+ |
| 6 | `route-alerts-by-platform` | The YouTube alert queue, a chat per platform, rendering, the bot role's grants |
| 7 | `add-youtube-virality-scoring` | Curves, spreads and threshold measured on YouTube data; `scoring/` made platform-blind |

The critical path is 1 → 2 → weeks of snapshots → 7; changes 3–5 fill that wait, and 6 fits anywhere before 7. Calibration follows scoring as its own change, as `calibrate-virality-scoring` did. Comments (`commentThreads`) stay out, for the same reasons as on Telegram: the heaviest phase and the first to carry personal data.

### X, and why it waits

X has the graph YouTube lacks — retweets, quotes, mentions and replies are a direct counterpart of forwards — and no free way to read it.

- **The official API** is pay-per-use: $0.005 per post read, a post read twice inside one UTC day charged once, capped at 3 million post reads a month. Estimated for 500 accounts at about two posts a day each, replies excluded: about $1.8 thousand for a year of history, and $300–500 a month to poll.
- **Resellers** quote about $0.15 per thousand tweets, which is cheap and grey: they resell scraping, and the prices come from reviews rather than their own pages.
- **The free route** is an account-pool scraper (twscrape, Twikit). Against X's terms, the accounts get banned, it reportedly breaks every few weeks — and a pool of accounts *is* the switching of accounts to escape a limit that this project rules out on Telegram. Rejected.

So X waits for a budget decision, or for a different reading of "Twitter". A good part of English-language tech moved to Bluesky, whose API is free, needs no authentication for reading, and has reposts and quotes. Whether the task means X specifically is a question for the manager.

## Legal note, briefly

Commenter IDs and handles are personal data (152-FZ, and GDPR for any non-Russian scope). Store hashes, do not publish aggregates about individuals, stay at the channel level. Also, scraping is against Telegram's ToS — the account ban risk is the operator's own. YouTube binds harder than that: its API Services Developer Policies are terms of access, not a ban risk, and they constrain storage and derived metrics directly (see "Policy before engineering").