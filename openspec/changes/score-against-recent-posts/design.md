## Context

Everything below was measured on the working database on 25 October 2026, after `fold-channel-wide-spikes` shipped, from snapshots `itgraph watch` has collected since 3 August.

**Why the medians froze.** `mature_medians` selects raw messages whose `fetched_at − date` lies in 28–120 days. The raw layer is insert-only, so `fetched_at` is the first fetch, and the watch loop fetches every post within minutes of publication. A post published since August therefore has `fetched_at − date ≈ 0` forever, and only the July backfill can satisfy the filter: for `aigency360` the contributing posts were published 30 March – 25 June, and its median has been 255 over 831 posts in every run since 31 August. The snapshot layer does not help either — the watch loop's last sample is at 48 h.

**How it is measured here.** One per-channel bias statistic throughout, the one `calibrate-virality-scoring` used: the median, over a channel's posts, of `ln(v / reference) / spread`, where `v` is the post's largest views reading at 7 h–9.5 h and `spread` the stored 420m-band spread of its kind. Rolling and out of sample: over the last 28 days, at each refresh moment the reference is built only from posts published before it, and judged on posts published until the next refresh. Channels with at least five judged posts; the same channels for every variant compared.

```
                                         channels  bias MAD  |bias| > 1z    p5      p95
  frozen mature median × factor (today)       229      0.81           72  −3.24    +1.17
  newest 20 posts, daily refresh              229      0.15            3  −0.34    +0.55
```

```
  channel                     frozen    newest 20
  dmitrii_ireshev_Agile_PMP    +6.2       +1.3     grew ninefold since July
  turboproject                 +2.2       +0.0
  j_links                      −5.2       −0.1     no growth: views at 2 days unchanged since July
  pythonl                      −3.6       −0.1     same
  secwebinars                  −4.9       +0.4
  cpluspluc                    −3.5       +0.1
```

The second group matters for the choice below: those channels did not grow. Their bias is the kind-wide factor — "a post reaches 55% of its mature median by eight hours" — misdescribing a channel whose posts keep accruing for weeks. A fresh mature median would leave them exactly where they are.

## Goals / Non-Goals

**Goals:**
- An ordinary post scores near zero on its own channel, whichever way the channel has moved since July and whatever its posts do after eight hours.
- The reference keeps moving without anyone re-running a backfill.
- The spread is the error of the estimate as scoring will meet it, not as it looks from inside the sample.
- Channels scored against the old reference are counted, not discovered.

**Non-Goals:**
- **Re-reading month-old posts** to give the fallback fresh mature values. It would be one `messages.getHistory` per channel per week — cheap — but it is a collection change, it only fixes staleness and not the factor mismatch, and the channels it would serve produced **2 of 196** spike alerts over the last 14 days. Revisit if that share grows.
- **An hour-of-day correction.** Still visible against the new reference (median z by publication hour: about +0.4 at midday, −0.5 at night), still its own change.
- **A curve per channel.** The kind's curve with a channel's own reference already brings the bias MAD to 0.15; per-channel shapes would be fitted on twenty posts.
- **Pruning old runs.** Daily refresh adds ~2 000 rows a run; that is a storage question with no deadline.

## Decisions

### The channel's own newest posts, not a fresher mature median

The two candidates fix different things. Re-collecting mature values keeps the decomposition `mature median × kind factor × curve` and makes the median current; it leaves the factor's per-channel error in place (`j_links`, `pythonl`, `secwebinars` above). Measuring the channel at the age the curve is normalised to removes the factor for every channel that has enough recent posts, and needs no request at all. The decomposition stays as the fallback for the channels too quiet to support the direct measurement — the job it was invented for in `add-virality-scoring`.

### The reference statistic: the largest reading in the reference band

The same per-post value `curves._reference` already uses as the curve's denominator, so "fraction of the eight-hour value" and "the channel's eight-hour value" are the same quantity by construction.

The alternative was a post's latest reading from 220 minutes on, divided by its kind's curve fraction there. It recovers the posts whose reference band falls in the collector's quiet hours — measured, 67–100% of posts published 18:00–21:00 Moscow time have no 7 h–9.5 h reading — and gives 16 more channels a reference. It was measured and rejected: the curve's error comes in with it, and the tails widen (channels beyond 1 z: 14 against 8; p95 +1.08 against +0.91). A channel that publishes only in the evening stays on the fallback, and is counted there.

### The newest twenty posts within 90 days, at least ten

Daily refresh, same frame as above:

```
                                     channels  bias MAD  |bias| > 1z    p95   dmitrii_ireshev
  90 days, at least 15                    215      0.21            5  +0.75        +3.8
  30 days, at least 15                    164      0.17            3  +0.76        +1.6
  newest 10                               231      0.14            2  +0.45        +0.5
  newest 20 (at least 15)                 215      0.14            2  +0.55        +1.3
  newest 30                               215      0.16            2  +0.73        +2.3
  newest 50                               215      0.17            5  +0.74        +3.7
  newest 20, at least 10                  231      0.15            3  +0.54        +1.3
```

A window of days is the wrong shape: a prolific channel fills it with hundreds of posts and lags its own growth by half the window, while a slow one barely fills it at all. Counting posts adapts both ways. Ten and twenty are equally good on this data; twenty is chosen because a median of ten is one day of a prolific channel, so a single boosted day *is* its reference — and with weekly refresh ten is already measurably worse (`aigency360` +0.9 against +0.4). The minimum of ten keeps the coverage ten would have had. Ninety days caps how stale a slow channel's reference may be, and is all the snapshot history there currently is.

Posts are counted the way the scorer counts them: an album as its first part, a burst as its first message (`scoring/fold.burst_heads`). Otherwise an album of ten parts is ten votes for one post's reach.

No factor applies to a recent reference — it is already the eight-hour value. Stored as the channel's baseline value with the reference kind beside it; loaded as a baseline with factor 1.

### Refreshed daily

```
                          bias MAD  |bias| > 1z    p95
  newest 20, weekly           0.19            4  +0.74
  newest 20, daily            0.15            3  +0.54
```

A refresh takes about a minute and touches no network. The timer moves from Monday 04:30 to 04:30 daily, inside the quiet hours where nothing else is competing, and `baseline_refresh_days` — when the pass starts saying its baselines are stale — from 7 to 2, so one missed run is tolerated and two are reported.

### The spread is measured out of sample

A refresh fits the spread from the last 14 days' residuals. Against a reference that contains those same posts, the residuals come out narrower — measured, MAD 0.161 against 0.169, about 5% — which inflates every z by the same 5%, in the over-alerting direction.

So each post's residuals are taken against the reference it would actually have been scored against: the median over the channel's newest twenty posts *whose reference band had closed before this post was published* (published at least 9.5 h earlier), within 90 days. That is the stored reference evaluated at a different moment — one pure function, called once per channel for the published reference and once per post for the residuals. Channels on the fallback are unaffected: their reference does not depend on the post.

The kind's factor is still fitted from the mature medians, exactly as today, because the fallback channels still need it.

### The threshold is re-priced on the same fortnight

Taking per-channel bias out of the residual narrows the spread, and by a lot. MAD of `ln(v8h / reference)`, rolling out of sample, per kind:

```
  kind         posts   frozen    newest 20
  aggregator    4160    0.237      0.165
  personal      2788    0.386      0.186
  vacancies     1424    0.342      0.213
  media         1003    0.211      0.163
  company        442    0.260      0.197
  community      367    0.365      0.157
```

Most of what the stored spreads called post-to-post noise was channels sitting off their reference. The same multiple therefore scores 1.4–2× the z it does today, and leaving 3.3 in place would raise the volume severalfold without anyone deciding to — the mistake `calibrate-virality-scoring` refused. The order is the same: fix, refit, measure, then price — by replaying the 14 days before the change and taking the threshold that reproduces the volume the shipped code produces on them. The table of volume against threshold goes into `docs/PLAN.md`, so a different choice is a decision rather than a guess.

A first estimate, with a 90-day reference rather than the chosen rule and the old spreads left in place: 8.0 messages a day to 7.7, with the composition changing — `turboproject` drops from 9 to under 3, channels that were biased low start to appear. The refitted spread is what moves the number, and that is the measurement the re-pricing makes.

### Storage

- `channel_baselines.reference`: a new enum, `recent` or `mature`. Not nullable. The revision fills existing rows with `mature`, which is what they are, and then drops the server default, so a new row has to say which it is.
- `baseline_runs.recent_posts`, `recent_min_posts`, `recent_max_days`: nullable, for the reason `named handle signal` gave — a run before the rule never ran under it.
- `channel_baselines.samples` keeps meaning "posts the value was taken over", for either kind.

## Risks / Trade-offs

- [A sustained channel-wide shift becomes normal after about twenty posts — a day for a prolific channel] → It alerts at its start, folded into one message per six hours; for the channel boosted every evening this is the requested behaviour. A single viral post moves a median of twenty by at most one rank.
- [A refresh that lands in the middle of a boosted day gives a boosted reference, and the next ordinary day reads low] → Negative z raises nothing; the following refresh corrects it.
- [Evening posts rarely enter a reference, because the collector sleeps through their eighth hour] → Measured cost: 16 channels stay on the fallback that the curve-adjusted variant would have covered. Their posts are still scored.
- [The fallback stays frozen at the backfill] → Reported in the refresh summary and the pass's line; 142 channels, 2 of 196 alerts. Re-reading is the named follow-up if that changes.
- [The z scale moves, so thresholds and verdicts recorded before and after are not comparable] → `alerts.value` is a z under the run that produced it, and runs keep their parameters; the re-pricing is documented beside the old one.
- [Every intermediate state of the working copy is live, because the timers run from it] → The first implementation task is agreeing how to keep them apart; `fold-channel-wide-spikes` took the scoring timer down for twenty minutes by not doing so.

## Migration Plan

One revision: `CREATE TYPE baseline_reference AS ENUM ('recent', 'mature')`, `channel_baselines.reference` added with a server default of `mature`, the default dropped; the three nullable parameters on `baseline_runs`. Creating a type and using it in one transaction is allowed — the restriction is on `ADD VALUE` to an existing one. Downgrade drops the columns and the type; existing runs read the same afterwards.

Deploy order: back up (the upgrade does), upgrade, run `itgraph baselines` once so the current run carries recent references, then let the timers run. The bot needs no restart: it reads none of this. The timer change goes through the playbook — `baselines_schedule` in `deploy/group_vars/all/main.yml`, which is what the timer template actually reads — not by hand on the machine.

Rollback: downgrade and refresh; the next run is a mature-only run again, exactly as before.
