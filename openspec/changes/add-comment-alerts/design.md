# Design

## Context

See `proposal.md`, Why; the requirements are in `specs/`. What shapes the approach, measured on 2026-11-07 from read-only pulls of production — 30 days of readings between ten minutes and 9.5 hours, 120 days of posts' eight-hour values, every alert of the 30 days:

- **Comments are a sparse count.** 309 of 550 seed channels have them open, 210 have enough recent posts for a reference. 52% of comment readings are zero.
- **The shared score fails on them, three ways.** `log(max(obs, 0.5) / expected) / spread`: small counts carry most of the dispersion, so the spread comes out about 1.1 and z 3.7 needs some 60 times the expectation; the recent reference is the median of *non-zero* values and needs ten of them, so half the channels have none and the rest are judged against their commented posts only; the curve is the median of per-post fractions over non-zero readings, which discards the zeros that are half the data. Replayed, it gives 0.1 messages a day at z 3.7.
- **What was tried, on the same posts, folded per channel as the pass folds:**

  | Model | Readings past z 3.7 (normal tail 0.011%) | Messages a day at z 3.7, minimum 10 |
  |---|---|---|
  | log-ratio, as now | 0.027% | 0.1 |
  | shifted log, `log((obs + 3) / (exp + 3))` | 6.1% | 9.0 — and barely falls with z |
  | negative binomial, dispersion per kind by moments over the middle 98% | 1.5% | 6.0 |
  | **negative binomial, dispersion per kind by maximum likelihood** | **0.33%** | **1.3** |
  | the same, dispersion per channel, floored at its kind's | 0.25% | 1.1 |
  | the same, per channel shrunk toward its kind's | 0.44% | 1.6 |

  The shifted log collapses for the reason the log-ratio does: residuals at zero have no spread, so the median absolute deviation measures nothing. The moment estimator trimmed the tail it was meant to describe. These are offline figures, from a script; on the code as built the volume is lower (Decision 7).
- **The operator read the top twenty of the chosen model:** two were a giveaway and a contact exchange, one was deleted, seventeen were natural reactions to news, among them a large company's post on recovering from an attack on its data centres.
- **Who it alerts on:** at the crossing, the expectation has quartiles 0.4, 0.9 and 2.6 comments and the count 17, 24 and 70; 3 of 39 had an expectation of ten or more. The posts of big discussion channels vary so much that a triple is ordinary there. Crossings come at a median of 2.4 hours of age; 10–17% of the posts had already alerted on another metric.
- **The production image has no numpy or scipy:** they are in the `data` group, which the image does not install.

## Goals / Non-Goals

**Goals:**

- One more metric that alerts, about once or twice a day, on the shared threshold.
- The other three metrics untouched: their reference, curve, spread and z stay what they are.

**Non-Goals:**

- A per-channel dispersion. It added nothing measurable over the kind's (table above) and costs a column per channel.
- Big discussion channels' outliers. A triple of three hundred comments is not improbable for them, and the model says so; the views and reactions alerts still see those posts.
- A fallback reference for comments. The settled median the other metrics fall back to is a final count, not an eight-hour one, and for comments its factor would be fitted on the non-zero posts only.

## Decisions

### 1. The count score

```
  mean   μ = reference × curve(kind, age band)                      floored at 0.05
  count  X ~ NB(mean μ, dispersion α)          Var X = μ + α μ²
  tail   P(X ≥ obs) = I_{1-p}(obs, n),  n = 1/α,  p = n / (n + μ)     regularised incomplete beta
  score  z = −Φ⁻¹(P(X ≥ obs))                  +40 when the tail underflows, −40 at obs = 0
```

- **The incomplete beta is written out:** the continued fraction (modified Lentz) in log space, from `math.lgamma`, with `statistics.NormalDist().inv_cdf` for the inverse. No dependency, and accurate in the far tail, where `1 − CDF` would cancel to zero.
- **A z, not a probability, on the alert.** It is what `alerts.value` holds for every spike, what the threshold compares and what "one post, one alert" ranks; a comment score next to a views score has to be on the same axis, and the tail's z is that axis.
- **Rejected:** the shifted log and the two moment estimators (table above); a two-part model, "any comment at all" scored apart from "how many" — the part about any comment is the weakest signal in the data and doubles the code; comments per view — a different product, "argued over out of proportion to reach", which the operator did not ask for.

### 2. The comment reference

The mean of the channel's newest twenty posts' eight-hour comment counts, zeros included, within 90 days, settled before the moment, at least ten posts — the windows of the other metrics, the statistic changed. The negative binomial is parametrised by its mean, and a median of a counter that is zero half the time is zero for half the channels.

A mean is pulled up by a past discussion for the next twenty posts, so a channel just after one is briefly harder to alert on. Accepted: dropping each window's largest value under-estimated the mean and over-alerted (6.0 a day above).

### 3. The comment curve and dispersion, in the refresh

- **The curve** per kind and band is `Σ comments in the band / Σ eight-hour comments`, over posts with an eight-hour reading, zeros included. A kind under the band minimum borrows the pooled curve, as today.
- **The dispersion** per kind is the α maximising `Σ log NB(obs | μ, α)` over that kind's readings — of posts counted as the scorer counts them, an album by its first part and a burst by its first message, since a later part's counter is nearly always zero and is never scored — each μ from the reference its post would have been scored against (`reference_at` at the post's publication, so a post never fits its own ruler). Golden-section search over `log α` in [0.01, 50]; the likelihood is unimodal in α. Measured: aggregator 2.45, community 2.68, company 5.39, event 16.8, media 1.12, personal 1.58, vacancies 12.9. A kind under `baseline_min_band_spread_posts` posts borrows the pooled dispersion, flagged as borrowed.
- **Stored** in `metric_baselines.dispersion`, null for every other metric. The log spread is still fitted and stored for comments, as before, and nothing scores with it.

### 4. The minimum count, and crossing

`alert_spike_comments_min`, default 10. A comment reading under it is scored and set aside: in `_best_per_post` it neither crosses nor becomes the post's best, so five comments on a quiet channel cannot take the alert a post's views had earned. Replayed on the code over 16 days: 1.19 a day with no minimum, 1.06 at five, 0.75 at ten; offline, twenty dropped posts the operator found real at 17–19 comments.

### 5. What the alert keeps and says

- **`alerts.observed` and `alerts.expected`**, nullable, written for comment spikes only: the count and μ at the crossing reading. The z alone does not give the count back, and the next refresh gives another μ.
- **The message:** `💬 Комментарии — 117, обычно около 1 (z 14.2)`; an expectation under one reads «обычно меньше одного». The other kinds keep their words, since their multiple depends on a spread.

### 6. Settings

`alert_spike_metrics` gains `comments`; `alert_spike_comments_min` is new. The threshold stays z 3.7 for all four, as the plan's "one threshold across all metrics" asks. On the code it gives 0.75 a day, under the one or two asked for; a threshold of its own, z 3.3, would give 1.38, and the operator kept the shared one.

### 7. Verifying the implementation against the measurement

The offline figures came from a scratch script. Before the tag, the pulled readings are loaded into a scratch database on the operator's machine — channels, posts reduced to their date, album and comment count, and the snapshots — and the real refresh and scoring are run over it. The pulled files hold channel ids and usernames, so they stay in the session's scratch directory and are deleted once this is done.

Done on 2026-11-07. A refresh fits on the fourteen days before it, so only the last 16 of the 30 days could be replayed as they would have run: baselines refreshed at the start of each day, in order, then that day scored. **0.69 comment messages a day** at z 3.7 and ten, eleven posts in 16 days, the same posts the script raised but for borderline ones at z 3.7–4.1. The script, counted the same way over the same days, gives 1.00: it had put every part of an album and every message of a burst into the channel's mean (1.19 before that was corrected), and fitted its curve and dispersion on the days it scored. Views were not scored in that database — the pull holds no settled history to fit their factor — so the one-post-one-alert ranking was tried by the tests alone.

**Done again after `/opsx:verify`**, on a fresh pull ending at 03:12 Moscow time the same night: the dispersion had been fitted on every part of an album and every message of a burst, never scored and nearly always at zero comments, and is now fitted on posts as the scorer counts them. The dispersions on the first replayed day now agree with the offline ones (aggregator 2.44 against 2.45, community 3.35 against 2.68, company 5.32 against 5.39), and `event` fits its own, 14.85, where it had borrowed the pool. **0.75 comment messages a day** at z 3.7 and ten, twelve posts; 1.06 at a minimum of five, 1.19 with none, 1.38 at z 3.3. These are the figures everything else here quotes.

## Risks / Trade-offs

- **[Fewer comment alerts than first measured]** 0.75 a day on the code, against the 1.3 the decision was first taken on → the operator kept the parameters; the first week's volume and verdicts are read in production (`tasks.md`, 5.4), and the minimum is the setting to move.
- **[Real tails are heavier than the model's]** 0.33% of readings pass z 3.7 against the normal's 0.011% → the threshold is priced by volume, as it is for the other metrics, and the first week's volume is read after the deploy.
- **[A channel whose discussions are bursty alerts often]** → folding within six hours bounds a day; across days it would show in the feedback the bot already records.
- **[Comments start a day late]** a run without a dispersion scores none → the first refresh after the deploy, at 04:30, starts them; the scoring pass's summary says comments are not scored until then.
- **[The refresh gets slower]** about six million `lgamma` calls a day for the likelihood → seconds, in a job measured at 98.
- **[Giveaways and contact exchanges]** two of twenty → accepted, by the operator's decision.

## Migration Plan

1. **Agent:** this change; `make validate`; the scratch replay of Decision 7, recorded in `tasks.md`.
2. **Operator:** the tag `v0.1.0`, as `adopt-infrastructure-requirements` says, with this revision in it.
3. **Operator:** in the test environment, after the restore of production's dump: `itgraph baselines`, then `itgraph score --replay` over 14 days; the comment volume recorded.
4. **Operator:** production's first run; the next morning's refresh stores the dispersion; a week later, the comment messages a day, read and recorded.

**Rollback:** a release whose `alert_spike_metrics` leaves out `comments`, or the previous pin, stops comment alerts; the role does not expose the setting, so it takes a release. The columns are nullable and stay, and the old code ignores them.
