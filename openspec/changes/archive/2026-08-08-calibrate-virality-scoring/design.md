## Context

`add-virality-scoring` shipped against three days of snapshots. There are now 137 190 readings over 4 078 posts and 385 channels, collected across 5.2 days with the poll queue never more than half a minute overdue. Everything below was measured on that, using the shipped scoring code rather than a parallel implementation — a second scorer would agree on whatever anyone checked and diverge on the case that matters.

### What the data says is right

```
  metric      median z   spread       n
  views         +0.09      1.02    20039
  reactions     −0.10      1.03    15356
  forwards      −0.04      1.03    20029
  comments      −0.74      1.10    10024
```

A z that is calibrated has median 0 and spread 1. Three metrics land there. The decomposition — channel median × factor × curve, dispersion measured rather than assumed — works, and nothing in this change touches it.

### What it says is wrong

**Only 273 of 2 940 minutes are scoreable.** The six bands are narrow windows around the sample offsets: 12–21m, 24–36m, 51–75m, 108–144m, 3h30–4h42, 7h–9h.

```
  readings                          124 487
    age falls between bands         104 314   84%
    scored                           20 039   16%
    channel has no baseline              75    0%

  posts 3 459, scored at least once 2 619   76%
```

840 posts can never alert, at any threshold. And the loss is structured, not random:

```
  share of posts ever scored, by publication hour (Moscow)
    11:00–17:00   80–88%
    18:00–23:00   64–75%
    03:00            42%
```

Which suggested that part of the "night posts underperform" signal (median z −0.7 at 00:00, 05:00, 06:00 against +0.3 at midday) was an artefact of *not being measured* rather than of doing badly. That is why no hour-of-day correction is in this change: the effect had to be re-measured once the gap was closed.

**Measured afterwards, the guess was wrong and the opposite is true.** With the bands contiguous the swing grew from ~1.1 z to 1.53 (midday +0.72 against 01:00 −0.80). The blind spot was masking the effect, not manufacturing it. That makes a calendar correction a stronger candidate for its own change than it looked here, and it is still out of scope for this one.

**The mature median has no upper bound.** `mature_medians` filters `fetched_at - date >= 28 days` and nothing else.

```
  age of a post contributing to a channel median
    youngest    28 days
    median     183 days
    p90        332 days
    oldest    2945 days
```

One channel's own history: median views per post ran 1588 in January 2026, 2735 in March, 2966 in June. Its baseline averages all of it, so today's ordinary post scores z +1.87 — and it produced 7 of the 40 alerts in the first replay.

```
  per-channel median z (109 channels with ≥20 recent readings)
    highest  +1.87        lowest  −4.48
```

**The spread is not flat across ages**, which `add-virality-scoring/design.md` explicitly assumed ("~0.38 in logs, flat across ages"):

```
  band   median z   spread        n
   15m     −0.11      1.18      819
   30m     −0.16      1.14     1250
    1h     −0.13      1.11     2430
    2h     −0.04      1.01     3492
    4h     +0.16      0.95     5765
    8h     +0.26      0.98     6283
```

**`event` has no curve for any metric.** Not "these channels lack history" — the *kind* has too few posts to fit a shape, so its 18 seed channels are unscoreable permanently.

## Goals / Non-Goals

**Goals:**

- Every reading inside the alerting window is scoreable, so a post's chance of alerting does not depend on the hour it was published.
- A channel is compared against what it is now, not against its whole history.
- A threshold means the same thing at fifteen minutes as at eight hours.
- No channel kind is silently unscoreable.
- Comments get a verdict backed by the larger sample, whichever way it goes.

**Non-Goals:**

- **No hour-of-day or weekday correction.** The hour effect is confounded with the coverage gap this change closes, and must be re-measured after. The weekday effect cannot be measured at all yet: the window holds exactly one of each weekday, so "Tuesday" is inseparable from what happened to be published that Tuesday.
- **No change to the threshold — as a starting position, revised on evidence.** Fixing the reference moves every score, and re-tuning in the same breath would have made it impossible to say which did what. So the threshold was held at 3.0 through the work and priced afterwards, on the same week, with the same code: the reference correction rescaled z enough that 3.0 would have raised 54 alerts where it used to raise 40. **3.3 reproduces the original volume exactly**, and shipping without it would have changed how much the bot says without anyone deciding to. The order — fix, measure, then re-price — is what made the new number a measurement rather than a guess.
- **No change to alerting, delivery, or collection.** Same queue, same bot, same requests.
- **No re-collection.** Every number here came from data already on disk.

## Decisions

### Bands become contiguous from 10 minutes to the end of the window

Not interpolation. The original refusal — "inventing a value for it would be interpolating a shape this module has not measured" — was correct against a few hundred observations. The gaps now hold 2 000–3 300 readings *per hour*: 0h 2309, 1h 2217, 2h 2330, 3h 2016, 5h 3259, 6h 2978. A band there is measured like any other, and the same `min_band_samples` floor keeps a thin one out.

Bands stay discrete rather than becoming a fitted continuous function. A step function of measured medians makes "this band was fitted on 2 016 readings" a fact a reader can check; a smooth fit hides which parts are evidence and which are shape.

### The mature window gains an upper bound, and the post minimum drops to pay for it

```
  window / min posts   channels   bias spread   |bias|>2   p95 |bias|
  unbounded / 30            465          1.05        10%        3.23
  28–180d   / 30            384          0.82         8%        2.66
  28–120d   / 30            332          0.78         6%        2.39
  28–120d   / 20            382          0.78         6%        2.39
```

`28–120d/20` and `28–180d/30` cost the same coverage — 382 against 384 channels — and differ in bias, 2.39 against 2.66. So: **28–120 days, minimum 20 posts.**

The honest limit: the bias figures cover the 109 channels with at least 20 recent readings, and those all have 30+ mature posts regardless. Lowering the minimum admits 50 channels the measurement cannot speak for, because they are too quiet to have produced 20 recent readings. What is claimed is that the busy channels do not get worse, not that the quiet ones are proven fine.

Coverage still falls from 465 to 382. That is the price, and it is the right side to err on: an unscored channel is reported, while a channel scored against its former self alerts on ordinary posts and looks like a working detector.

### The spread is fitted per band

One number per (kind, metric, band) rather than per (kind, metric). Nothing else changes — `score_metric` divides by the spread for the reading's band instead of the metric's.

Where a band has too few residuals to fit its own, it takes the metric's pooled spread rather than going unscoreable. A slightly wrong ruler at one age is a smaller error than a hole at that age, which is the whole lesson of the coverage finding above.

### A kind with no curve borrows the pooled one, and says so

Fitted across all kinds, used only where a kind could not be fitted, and recorded on the row. This is deliberately *not* the "no partial baselines" rule being relaxed: that rule forbids mixing a measured median with an assumed curve **silently**. A borrowed curve that travels with the fact that it was borrowed is a different thing — it can be reported, queried, and withdrawn when the kind has enough data of its own.

### Comments are re-measured, and the answer is whatever it is

Median z −0.74 says the estimate is biased, not noisy: the factor of 1.00 — the claim that a post reaches its full mature comment count by eight hours — is too high. With 2 301 posts against 184, the factor and the per-band spreads can be fitted properly. If the median lands near zero and the spread near 1 after the other three fixes, comments become alertable by setting `alert_spike_metrics`. If it does not, that is a result and gets written down.

**Measured afterwards: they stay off, and now for a stated reason rather than for thinness.** Median z moved only from −0.74 to −0.64 and the spread not at all, from 1.10 to 1.11, across twelve times the data — and the calibration drifts 0.72 z across the bands. The cause is not sample size:

```
  share of readings that are exactly zero
    comments   44.2%
    views       0.0%
```

A log-ratio against a median is the wrong instrument for a counter that is zero in nearly half its observations. ``score_metric`` floors a zero reading at 0.5, so those 44% all land far below any expectation and drag the median with them. No amount of further collection changes that; what would is a different model for the zero-inflated case — scoring whether a post drew any discussion at all separately from how much. That is a change of its own, and this measurement is its justification.

## Risks / Trade-offs

**Coverage falls from 465 channels to 382.** Measured and accepted above. Watch the unscored count in the pass summary after the first refresh; if it lands far from 382, something else moved.

**More bands mean more alerts, before any threshold changes.** 840 posts become scoreable, and roughly a quarter of readings will be. The replay is the check — run it over the same week and compare against the 40 alerts the current configuration would have raised. If the count moves by much more than the coverage did, the new bands are fitted on something other than what the old ones measured.

**Per-band spreads are fitted on less data each.** The 15m band has 819 residuals against 20 039 pooled. That is above `min_band_samples` by a wide margin today, but it is the number that shrinks first if collection is interrupted — hence the pooled fallback.

**The bias measurement rests on 109 channels and 5.2 days.** It is enough to establish direction and rough magnitude and not enough to tune against. Nothing in this change tunes against it: the window bound is a structural fix, and 120 days is chosen from the sweep rather than optimised.

**Everything here is one week of one summer.** The August lull, a single Saturday, one news cycle. The change corrects defects that are structural — a missing upper bound, bands that cover a tenth of the window — rather than fitting this week's numbers, which is why the parameters it introduces are bounds and floors rather than coefficients.
