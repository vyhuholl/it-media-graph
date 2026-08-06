## Why

`itgraph watch` has been writing snapshots since 2026-08-03 and nothing reads them for alerting. The cascade bot speaks about once a day, which was the deliberate trade: delivery shipped first, on a signal that needed no warm-up, so that deduplication, caps and rendering were exercised on a trickle while the baselines accrued. They have accrued.

**The method is not a proposal any more; it has been measured end to end on this project's own data.** Expected value for a post of age *t* is the channel's mature median, scaled to the age:

```
  expected(t) = channel's 30-day median × 0.43 × curve(t, channel kind)
```

The 0.43 is the missing link, measured over 574 posts: a post reaches about 43% of its channel's mature median by eight hours. The curve is the per-kind growth shape already recorded in `docs/PLAN.md`.

Scoring real snapshots against that gives a residual `log(actual / expected)` which behaves the way a z-score needs it to:

```
   age    observations   median residual   spread
   15m         397           +0.08          0.38
   30m         572           +0.01          0.40
    1h        1151           +0.03          0.37
    2h        1585           +0.06          0.40
    4h        2513           +0.14          0.36
    8h        2187           +0.25          0.32
```

Two properties matter, and neither was known a week ago. It is **calibrated** — the median residual sits near zero at every age, so the estimate is not systematically wrong anywhere. And the **spread is constant at ~0.38 across ages**, which is what makes a single z-score denominator legitimate; had it drifted, the score would have meant something different at every age and the thresholds would have been uncomparable.

The volume follows directly, counted over the observation period:

```
  threshold   × expected   alerts/day
     z 2.0        2.1×          25
     z 2.5        2.6×          10
     z 3.0        3.1×           9
     z 4.0        4.6×           6
```

The noise floor is around z 2 — between 1.5 and 2.5 the rate falls from 70 a day to 10 — and above 2.5 the curve is nearly flat, which says the extreme cases are a separated tail rather than the top slice of a continuum. **About nine alerts a day at z 3**, against the cascade signal's one, and comfortably under the daily cap the delivery change already carries.

465 of 544 channels have the thirty mature posts a baseline needs. The other 79 are not scored, and that has to be reported rather than silently true.

## What Changes

- **Baselines become a stored artifact**, refreshed on a slow cadence rather than recomputed per snapshot: per channel a mature median, per kind a growth curve per metric, and the residual spread each of them was measured against. 465 channels by four metrics by six age bands is not arithmetic to redo for every one of ~38 000 daily readings.
- **A scoring pass** turns fresh snapshots into alerts, reading `message_metrics` and the baselines, touching no network and taking no session lease.
- **Levels are scored, not ratios — and this contradicts the plan as written.** `docs/PLAN.md` says "reactions/views N minutes after publication". Measurement says a ratio is the wrong instrument early: forwards front-load relative to views, so `forwards/views` runs at roughly **1.9× at fifteen minutes against eight hours** and would over-alert on young posts, which is the dangerous direction. Scoring each metric's own level against its own age curve accounts for age once instead of compounding two age-dependencies.
- **One alert per post, whichever metric is most extreme.** Four independent scores would let a genuinely viral post raise four messages within the hour — the same mistake as one message per reposter, in a different direction. The winning metric's kind is recorded; the message names the others.
- **Replay is a first-class mode, not a debug flag.** Score historical snapshots, report what would have fired, write nothing and send nothing. Without it a threshold experiment costs a day per iteration, which is how thresholds end up chosen once and never revisited.
- **Four new `AlertKind` values**, declared here rather than in the delivery change, for the reason that change gave: a value nothing can raise is a promise made in a type.
- **A channel with no baseline is reported, not skipped in silence.** 79 of them today, and the number will move as history accrues.

Out of scope, deliberately:

- **Any change to `alert-delivery`.** The queue, the caps, the digest, the feedback buttons and the bot are untouched. If this change had needed to modify them, that would have said the seam was drawn in the wrong place; it did not, and the one alert per post decision above is part of why.
- **Retuning the cascade thresholds.** They are measured and separate.
- **The 24-hour and 48-hour samples earning their keep.** No post had completed the full schedule when this was written — the first were finishing as it was drafted. Views are 96% settled by eight hours, so for the metric that dominates the answer barely matters; for forwards and comments it is an open question with a date on it rather than a design decision.

## Capabilities

### New Capabilities

- `virality-scoring`: what a post's expected value is at an age, how far above it counts, and what is done about it. Covers the baselines and their refresh, that age comes from the row rather than the schedule, that a channel without enough history is not scored and is reported, that one post raises one alert, and that the whole thing can be replayed over history without sending anything.

### Modified Capabilities

None. `alert-delivery` was specified without reference to what produces an alert, precisely so that this change would not have to touch it.

## Impact

- `src/itgraph/scoring/curves.py` — new: growth curves and the mature-to-recent factor, as pure functions over snapshot rows. No database, in the shape `alerts/cascade.py` and `affiliation/signals.py` already have.
- `src/itgraph/scoring/score.py` — new: expected value and z, pure.
- `src/itgraph/scoring/run.py` — new: the pass — load, score, raise, report — and the replay path through the same code, because a replay that runs different code proves nothing.
- `src/itgraph/db/baselines.py` — new: the baseline tables, their refresh, and reading them back.
- `src/itgraph/db/models.py` — `ChannelBaseline`, `KindCurve`, four `AlertKind` values.
- `src/itgraph/db/alerts.py` — a raise for virality alerts beside `raise_cascades`; nothing else changes.
- `src/itgraph/cli.py` — `itgraph baselines` to refresh them, `itgraph score` to run the pass, `--replay` on the latter.
- `src/itgraph/config.py` — the z threshold, the minimum mature posts, the baseline refresh interval, how far back a replay reaches.
- One Alembic migration: two tables, four enum values. The enum values go in their own revision, for the reason the `watch` revision documents.
- `tests/` — `test_curves.py`, `test_scoring.py`, `test_baselines.py`; the pass against a real database. The measured numbers above are what the fixtures should reproduce.
- `docs/PLAN.md` — the ratio-versus-level correction, since the plan currently specifies the instrument this change declines to use.
