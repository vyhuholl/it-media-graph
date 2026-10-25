## Context

Everything below was measured on the working database on 25 October, with the shipped scoring code: a replay of 14 days through `load_baselines`, `READINGS` and `score_post` reproduced every one of the 84 alerts raised since the last baseline refresh, and none it did not raise. The same replay pinned to baseline run 11 reproduced all 13 alerts `@rucodefestival` sent on 18 October.

What the scorer does today, per pass: every post published in the last 9.5 h is scored at every reading, its best score is kept, and a post whose best crosses z 3.3 is inserted into `alerts` with `ON CONFLICT DO NOTHING`. The only collapsing is within a post (across metrics and readings) and across the parts of an album. Nothing relates two posts of one channel.

`@aigency360`, 15 October, views per poll:

```
  post          17:32   18:01   18:31   19:00
  3448 (11:00)    156     228     450     530
  3450 (13:00)    132     204     428     512
  3452 (16:00)     73     154     385     467
  3454 (18:00)      —       1     263     352
```

Every post of the day gains the same ~+400 within the hour and converges on ~550 whatever its age. That has happened on 13 of the last 20 days, the injection growing to +1 500 a post. Organic level before it is exactly on the baseline (median ~100–150 at five hours against an expected 129).

## Goals / Non-Goals

**Goals:**
- One direct message per channel-wide event, and that message says it was channel-wide.
- One post published as several messages scores as one post.
- No band is divided by a spread measured on a handful of posts.
- The replay prices all three exactly as the live pass applies them.

**Non-Goals:**
- **Separating "the channel bought views" from "the channel had an event".** Both lift every post at once, both are worth one message, and the head's text names the possibilities rather than guessing.
- **Scoring a post against its siblings.** Priced and rejected below.
- **Stale channel medians.** `mature_medians` only ever sees the July backfill, because `raw_messages` keeps the first fetch and the watch loop reads posts at age zero. That is real — it is why `@dmitrii_ireshev_Agile_PMP` scores +7 on an ordinary post — and it is a separate change, because fixing it changes what the reference *is*, and this change only changes how many messages one finding becomes.
- **Editing a message already sent** to update its count. Telegram allows it; it would need the sent message's id stored, and a fold arriving after delivery belongs to an event already told.
- **Thin curve fractions.** A band's fraction is a median of ratios, far less sensitive to a few posts than a MAD is; the 150-minute fraction moved 0.835 → 0.805 between runs while the spread moved 0.104 → 0.169.

## Decisions

### Fold by channel, not by comparison with sibling posts

The obvious alternative is to score a post against its own channel's current level — the median z of the channel's other posts in the same poll — and alert only on posts that stand out of their siblings. Priced over 14 days:

```
  variant                                         alerts/day   in series   aigency360
  today                                                 14.0          81           66
  must beat siblings' median by 2 z                      9.6          28           24
  must beat siblings' median by 3.3 z                    7.9          25           18
  one per channel per 6 h                                8.6           0           10
  one per channel per 24 h                               7.4           0            8
```

The sibling comparison underperforms because the injection is additive: +400 is a larger log-ratio on a young post than on an old one, so the youngest posts still stand out of their elders. It would also need a lift estimate for channels with one live post, which is most of them. Folding needs neither, and turns the event into the thing the message says.

### A six-hour window, measured from the head

Gaps between consecutive threshold crossings of one channel, 14 days:

```
     0–15 min   54      same pass: the series itself
   15 min–3 h   15      same event, staggered crossings
        3–6 h    7      same event (aigency360 late boosts: 3.0 h, 5.5 h) and distinct posts
      6–9.5 h    6      distinct posts on one busy day
      12–24 h   19      the next day's boost — or habr_com_news, gonzo_ML, anti_agi, kaiten_ru
         24 h+  36
```

The largest gap inside one event is 5.5 h. Everything 12–24 h apart is a separate event, and 24 h would fold four genuinely different posts into yesterday's alerts. It would also hide most of the stale-median noise (`turboproject`, `dmitrii_ireshev_Agile_PMP` alert once a day), which is the wrong reason to look better: the next change is supposed to fix that, and a 24 h window would make it impossible to tell whether it did.

The window is anchored at the head and does not slide. A sliding window ("fold if the channel crossed within W of its *last* crossing") would chain daily boosts 15–22 h apart into one event forever at W = 24 h and is no better at 6 h; anchored, the guarantee is simple to state and to test — at most one direct spike message per channel per six hours.

For a head already in the queue the anchor is its `raised_at`; for a head found in the same pass, or in a replay, it is the moment the head crossed. The two differ by at most one pass interval.

### Folds are rows, pointing at their head

`alerts.folded_into` references the head's `id`. The alternative — not writing folded spikes at all — fails on the window's own edge: a post folded at 18:00 is still inside the 9.5 h scoring window at 00:01 and still above the threshold, so the first pass after the window would raise it as a new head. The unique constraint is already the "do not raise this again" mechanism; a folded row keeps it working, at the cost of a column.

`ON DELETE CASCADE`: a head and its folds are one event, and the only way a head disappears is its post leaving the raw layer.

The head is decided per pass from three inputs: spikes not yet in the queue (a live pass re-scores everything in its window, so already-raised posts are excluded before folding rather than left to the conflict clause, or an old head would be taken for a new crossing), the newest unfolded spike alert per channel raised within the window, and the spikes' crossing moments. Heads are inserted first, `RETURNING` their ids; folds second. One transaction, so the bot never sees a fold before its head.

A post's own later spike on another metric folds into the channel's head like any other post's. That removes the 24 cases where one post arrived twice, as views and as reactions, without a rule of its own.

### Folds are settled by the bot, with a delivery value of their own

The pass writes `folded_into`; the bot writes `delivered_at` and `delivery` — the split the two roles already keep. A fold stays `delivered_at IS NULL` until its head has been delivered, and the bot then marks it `delivery = 'folded'` in one statement per tick. The queue the bot claims from, and the outstanding count the status reports, both exclude folds, so a fold waiting for a held head never reads as "never attempted".

`folded` rather than reusing `direct`: the daily cap counts `direct` rows, and six folds would otherwise use up six of twenty messages that were never sent. Rather than `digest`: a digest line is a message the reader saw, a fold is a count inside one.

The head's text counts distinct *other* posts among its folds at render time — "fresher at render" as for every other piece of evidence — so the same post's second metric does not inflate it. Counted, not listed: the point of folding is fewer links, and the head's own link is the one to open.

### A burst is a chain of gaps under 60 s, scored under its first message

Gaps between consecutive posts of one channel over 60 days (albums already collapsed), per second of interval:

```
   <5 s   5–15 s   15–30 s   30–60 s   1–2 min   2–3 min   3–30 min
     87       24       9.3       3.6       2.5       2.5         ~3
```

From a minute on the density is the background rate of genuinely separate posts; below it is "the text, then each video". `@rucodefestival`'s bursts all have gaps of 14–48 s. A message joins a burst if it follows the previous message within the gap, so a four-message burst spread over 85 s is one post. The background rate puts the cost at roughly one false merge a day across the inventory, in which the second post is not scored.

Under the first message, as albums are: it is what the reader sees first and what the head's link should open. Counters on later messages are not consulted, which understates rather than over-alerts — the trade `_load` already documents for album parts.

Applied in the scoring pass's loading step only. The baseline refresh fits curves over every message, album parts included, as it does today; changing what a baseline is made of is the next change's subject.

### A band's own spread needs 100 distinct posts

Bootstrap over the 137 bands with at least 400 posts: re-estimate each band's spread from k random posts, compare with the full-sample estimate.

```
   k     p1     p5    p10   median
   10   0.19   0.34   0.43   0.84
   20   0.39   0.52   0.59   0.93
   40   0.54   0.65   0.72   0.97
   60   0.61   0.72   0.77   0.98
  100   0.69   0.78   0.83   0.99
```

Small samples are biased low, not merely noisy, and low is the over-alerting direction: a spread at 0.52 of the truth turns z 3.3 into an effective 1.7. The fallback is the pooled spread, which is itself a little narrow for the earliest bands (aggregator 10 m: pooled 0.258 against 0.350 measured, 0.74). 100 is where the thin estimate's tail error is no worse than the fallback's worst systematic error, so the rule never swaps a good ruler for a worse one.

Distinct posts rather than more readings, because readings are what overstated the band: the 18 October band had 20 readings and the minimum was met. The reading floor stays, a band must clear both.

## Risks / Trade-offs

- [A genuinely separate second spike in a channel within six hours of the first is not sent on its own] → It is counted in the head's message if it crossed in the same pass, recorded either way, and visible in the replay; measured, 7 of 196 alerts fell 3–6 h after their channel's previous one.
- [A head may be sent before the rest of its event crosses, so its message says nothing about folding] → Accepted. The event is still one message; the replay and the queue say how large it became.
- [A false burst merge hides a genuinely separate post published under a minute after another] → Estimated at about one a day across the inventory, and only matters if that post is the remarkable one.
- [Bands of thin kinds fall back to the pooled spread, which is narrow for the earliest bands] → Bounded at the same ~0.75 the 100-post rule tolerates; it was already the specified fallback.
- [The fold depends on the queue's `raised_at`, so a scoring pass that was not running for hours folds against a stale anchor] → The pass already reports its own staleness; a gap long enough to matter is a missed day of alerts first.

## Migration Plan

Three Alembic revisions. The first two are split the way `spike alert kinds` split its enum from its table: Postgres refuses to let a value added by `ALTER TYPE ... ADD VALUE` be used inside the transaction that added it.

1. `ALTER TYPE alert_delivery ADD VALUE 'folded'`, with a deliberately empty downgrade — there is no `DROP VALUE`, and an unused value strands nothing.
2. `alerts.folded_into` (nullable, self-referencing, `ON DELETE CASCADE`), a `CHECK` that a row is not folded into itself, and a partial index on it. Downgrade first settles every fold still waiting for its head — without the column each would be an ordinary undelivered alert, and the bot would send the whole series at once — then drops all three.
3. `baseline_runs.min_band_spread_posts`, nullable: a run records the parameters it was computed under, this one decides which band spreads exist, and a run made before it never saw the rule.

Every existing row has `folded_into IS NULL` and behaves exactly as before.

The bot's role needs nothing new: it holds table-level `SELECT, UPDATE` on `alerts`, and enum values carry no grant.

Order on the working machine: back up, upgrade, restart the bot (it must stop claiming folds before the pass starts writing them), then let the scoring timer run. `itgraph baselines` after deploy, so the 100-post rule applies before the next pass; until then the pass reads the old run's band spreads, which is what it did yesterday.
