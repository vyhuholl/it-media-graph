## Why

The scoring pass shipped on measurements taken over three days of snapshots. Five days later there are 137 thousand readings across 4 078 posts, and they say the machinery is sound and four of its parameters are wrong. Pooled calibration is close to perfect — median z of +0.09 for views, −0.10 for reactions, −0.04 for forwards, and a spread of 1.02 where 1.00 is the target. The defects are all in *how the reference is built*, not in the arithmetic that uses it.

Three of the four cost coverage silently, which is the worst way for an alerting system to be wrong: it looks exactly like a quiet week.

## What Changes

- **Age bands become contiguous across the alerting window.** Six narrow windows cover 273 minutes of a 49-hour horizon, so **84% of readings are discarded and 840 of 3 459 posts are never scored at all**. The blind spot is not random: 80–88% of posts published between 11:00 and 17:00 get scored against 42% of those published at 03:00. The original refusal to score between bands was a refusal to interpolate a shape measured on a few hundred observations; the gaps now hold 2 000–3 300 readings per hour and the shape can simply be measured there.

- **The mature median gains an upper age bound.** `mature_medians` filters on `age >= 28 days` and nothing else, so a channel's "normal" is its median across all of history — the median contributing post is **183 days old**, the 90th percentile 332 days, the oldest eight years. A channel that has grown is scored against the channel it used to be. Measured: per-channel bias spans +1.87 to −4.48 z, and **5% of channels are biased by more than the alert threshold itself**. A 28–120 day window cuts the 95th percentile of that bias from 3.23 to 2.39.

- **The spread is fitted per age band.** It was fitted once per metric and kind on the belief, stated in `design.md`, that it is "flat across ages". It is not: the residual spread runs 1.18 at fifteen minutes and 0.98 at eight hours, and the median drifts from −0.16 to +0.26 across the same range. One number means the threshold is stricter at some ages than at others without saying so.

- **A kind with no curve of its own falls back to the pooled curve.** `event` has no fitted curve for any metric, so its 18 seed channels cannot be scored however much history they accumulate. A curve measured across all kinds is a worse estimate than the kind's own and a far better one than none; the borrowing is recorded so it is never mistaken for a measurement.

- **Comments are re-measured.** 2 301 posts carry them now against 184 when they were switched off. Their median z of −0.74 says the estimate is biased rather than merely noisy, which is a different and more fixable complaint. Whether they become alertable is an outcome of this change, not a premise of it.

## Capabilities

### New Capabilities

None. This corrects an existing capability against its own measurements.

### Modified Capabilities

- `virality-scoring`: what a baseline is measured over gains an upper bound; the dispersion is measured per age band rather than once per metric; a post is scoreable at any age inside the alerting window rather than only near a sample offset; and a channel kind too thin to fit is given a stated fallback rather than silently excluded.

## Impact

- `src/itgraph/scoring/curves.py` — band definitions; the spread fitted per band
- `src/itgraph/scoring/score.py` — selecting the spread for a reading's band
- `src/itgraph/scoring/refresh.py` — the pooled fallback curve, and per-band spreads
- `src/itgraph/db/baselines.py` — the upper bound on the mature window; storing a spread per band
- `src/itgraph/db/models.py` + one migration — `MetricBaseline` carries a spread per band, and records whether a curve was borrowed
- `src/itgraph/config.py` — `baseline_mature_max_days`, and whatever `baseline_min_channel_posts` has to become to pay for it
- `docs/PLAN.md`, `src/itgraph/README.md` — the "flat across ages" claim, and the measured numbers that replace it

No change to the alert queue, to delivery, or to the collector. Nothing already collected is re-fetched: every number above came from data that is already on disk, and the change is re-runnable against it.
