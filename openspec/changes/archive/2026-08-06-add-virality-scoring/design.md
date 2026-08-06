## Context

Unusually for this project, the design was measured before it was written. The pipeline below was run against real snapshots and its residuals inspected; what follows is a description of something that has already been shown to work, not a plan that hopes to.

```
  channel's 30-day median views          ← from the 211k backfilled messages
            ×
  0.43                                   ← mature → 8h, measured over 574 posts
            ×
  curve(age, channel kind)               ← per metric, per kind
            =
  expected(t)
                     log(actual / expected) / spread   →   z
```

Everything the pass needs already exists in the database. The scarce input was never data volume — it was knowing whether the residual spread is stable enough for a z-score to mean the same thing at fifteen minutes and at eight hours. It is: ~0.38 in logs, flat across ages.

## Goals / Non-Goals

**Goals:**

- A score that means the same thing at every post age and on every channel size.
- Thresholds chosen from measured volume, not from taste.
- Replay over history, so a threshold change costs minutes rather than a day.
- No change to anything the delivery change specified.

**Non-Goals:**

- Scoring every channel. 79 of 544 lack the history; they are named, not fudged.
- A combined virality score. Views, reactions, forwards and comments mean different things, and `docs/PLAN.md` is right that collapsing them loses the distinction.
- Predicting what will go viral. This measures what is unusual now.

## Decisions

### Levels are scored, not ratios — and the plan says otherwise

`docs/PLAN.md` specifies "reactions/views N minutes after publication versus that channel's median". That instrument is wrong early, and the measurement is unambiguous: forwards front-load relative to views, so `forwards/views` reads about **1.9× at fifteen minutes against its eight-hour value** (0.0146 versus 0.0078). A young post scored on that ratio against a mature baseline is over-alerted — false alarms, which is the failure that destroys trust in an alert bot fastest.

A ratio was attractive because it looked age-free. It is age-free only against *maturity*, over weeks; inside the window an alert actually uses, it compounds two different age dependencies and inherits the difference between them.

Scoring each metric's own level against its own curve accounts for age exactly once. It also keeps the four signals genuinely independent, which is what makes "this post is being argued about" distinguishable from "this post is being carried".

### One post raises one alert

Four metrics scored independently would let a genuinely viral post produce four messages inside an hour. That is the same error as one message per reposter, arriving from the other direction, and it lands hardest on exactly the posts most worth reading about.

So the pass takes the highest z across the four, raises a single alert under that metric's kind, and the rendering names the others. The unique constraint on `(kind, channel_id, msg_id, band)` then does what it already does, and the delivery change needs no modification — which is the test of whether the seam between detection and delivery was drawn correctly. It was.

The cost: a post that spikes on views at one hour and on comments at four hours raises two alerts under different kinds. That is correct rather than a leak — a comment spike hours later is a different event, usually an argument, and worth saying.

### The measurements the rest of this rests on

Taken before implementation, over the snapshots collected since 2026-08-03.
Every default below traces to a row here.

```
  metric      channels  posts  factor  spread   calibration (15m → 8h)
  ─────────────────────────────────────────────────────────────────────
  views          465     586    0.44    0.38    +0.08 … +0.25
  reactions      436     424    0.68    0.75    +0.02 … −0.02
  forwards       454     562    0.63    0.77    −0.04 … +0.04
  comments       304     184    1.00    1.01    −0.15 … +0.00
```

**Both numbers differ per metric, and by more than enough to matter.** The
factor — how much of the mature median a post has reached by eight hours —
runs from 0.44 for views to 1.00 for comments, because views keep trickling
for weeks while a comment happens when somebody reads the post. Carrying
views' 0.43 across all four, which was the obvious shortcut, would have been
wrong by a factor of 2.3 on comments.

The spread matters more. At a threshold of z 3 it decides what the alert
actually means:

```
  views      3.1× expected        reactions   9.5×
  forwards  10.1×                 comments   20.7×
```

Hardcoding views' 0.38 for reactions would have made a nominal z 3 fire at
an effective z 1.5 — half the intended distance into the tail, on the metric
with the second-highest volume.

**Spread also varies by channel kind**, measured on views:

```
  aggregator 0.26   media 0.30   community 0.30
  personal   0.36   vacancies 0.37   company 0.37
```

A 1.4× range. Using one global figure would under-alert aggregators and
over-alert personal channels — not catastrophic, and not a decision anyone
made. Stored per kind, since the curve table is keyed that way already.

### Volume differs sharply per metric, and that is a finding rather than a defect

At z 3, one alert per post:

```
  views 8.8/day   reactions 4.8   forwards 0.7   comments 0.0
  combined, highest metric wins:  12.9/day
```

The temptation is to give each metric its own threshold so that all four
alert at similar rates. That would be wrong. `z` is already the normalized
unit; forcing equal rates would mean alerting on ordinary comment counts
because extraordinary ones do not occur. Views spike often, comment spikes
at twenty times expectation essentially do not happen in this corpus, and
saying so is the honest output.

One threshold, then, and 12.9 alerts a day — a readable volume, and closer
to `alert_daily_cap` of 20 than the cascade signal ever came. The cap and
the digest will start to matter.

### Comments are excluded from alerting for now

184 posts, 304 channels, spread 1.01, and a calibration that drifts from
−0.15 at fifteen minutes to zero at eight hours rather than sitting flat
like the other three.

That is not the finding "comments do not spike" — it is "comments cannot yet
be measured well enough to say". The distinction matters because the first
would be a design conclusion and the second is a data shortage that will
resolve itself. The curve and the baseline are computed and stored for
comments like everything else; only the alerting is off, behind a setting,
so re-enabling it is a configuration change and a re-run of the replay
rather than a code change.

Forwards stay in despite firing 0.7 times a day: 562 posts, calibration flat
within ±0.11, spread measured on real data. They are rarely extreme, which
is a fact about forwards.

### Baselines are stored and refreshed, not computed per snapshot

465 channels × 4 metrics × 6 age bands, against roughly 38 000 snapshots a day. Recomputing on demand is not a performance question, it is a category error: a baseline is a slowly-moving property of a channel, and treating it as a function of the current row invites it to move for reasons that have nothing to do with the channel.

Stored with the parameters they were computed under, as `AffiliationCandidate` already stores the parameters its evidence was scored with. A threshold argued about later has to be able to say which baseline it was arguing about.

**The spread is stored with them, not hardcoded.** It came out at 0.38 for views; the other three metrics have measured curves but unmeasured residuals, and writing 0.38 into the code would silently apply a views-shaped assumption to comments.

### Age is read from the row, always

`observed_at` minus the publication date, never which sample in the schedule this was meant to be. Already the rule in `itgraph.schedule` and in `docs/PLAN.md`, restated here because this is the change that would suffer from breaking it: samples are irregular by design — quiet hours confirmed at 02:00–07:00, an eleven-hour outage already survived — and a scorer that assumed the schedule was met would silently mis-age exactly the posts that were sampled unusually.

### A channel without a baseline is reported, not skipped

79 of 544 today. The number moves as history accrues, and it moves differently per channel, so it cannot be stated once in documentation and forgotten.

The precedent is the affiliation pass, which reports its signal coverage because a signal that could not run says something different from a signal that found nothing. Here the equivalent is that "no alerts from this channel" and "this channel is not scored at all" must not look the same.

### Replay runs the same code, or it proves nothing

The pass takes the moment to reason from, and a replay is that moment set in the past over stored snapshots. Not a parallel implementation, not a notebook: a second scorer that agrees with the first on the cases anyone checks and diverges on the case that matters is worse than no replay.

Replay writes no alerts and sends nothing. It reports what would have fired, which is the only affordable way to move a threshold — the alternative is one experiment per day, and thresholds chosen that way get chosen once.

### Thresholds default to the measured rate, not to a round number

z 3.0, giving about nine alerts a day at 3.1× expected. Chosen because the measurement says the noise floor is near z 2 and the curve flattens above 2.5, so 3.0 sits in the quiet part of the distribution with room to move either way.

One band rather than several. Between z 3 and z 4 the volume falls only from nine a day to six, so a second band would add a second message about six of the nine posts that already alerted — escalation that says almost nothing. The alert carries its z, and magnitude is read from that.

## Risks / Trade-offs

**The baselines are built from a corpus collected at one moment, and channels drift.** A channel that doubled its audience last month has a median that understates it, and every post will score high. → The mature window is thirty days and rolls; a refresh on a slow cadence picks the drift up. The residual calibration measured above is the detector: if medians start drifting badly, the residual stops being centred at zero, which the refresh can report.

**0.43 is a single global factor over a distribution with real spread** (quartiles 0.30–0.59). Channels whose posts settle faster or slower than average will be biased. → Per-kind factors are the obvious refinement and the data to fit them exists; global first because it is measured and works, per-kind when the residuals say the global one is the limiting error.

**The late samples are unproven.** No post had completed the 48-hour schedule when this was designed. → Views are 96% settled by eight hours so the answer barely matters for them; for forwards and comments it is an open question that resolves itself within days of this being implemented, at which point trimming the schedule is a settings change.

**Nine alerts a day is a different bot from one a day.** Everything the delivery change built for volume — the cap, the digest, the retry path — will be exercised for the first time. → That was the plan, and it is why they were built early rather than alongside. Expect the first week to find something in them.

**Measured since this was drafted, and the answer changed two defaults.** The spreads are 0.38 / 0.75 / 0.77 / 1.01 and the factors 0.44 / 0.68 / 0.63 / 1.00 — both per metric, neither shareable. Comments came out too poorly measured to alert on and are off behind a setting. See the measurements section above.

## Migration Plan

One migration for two tables, a second for the four enum values — Postgres refuses to use a new enum value in the transaction that added it, which the `watch` revision documents and which cost a debugging session once already.

Baselines start empty. The pass reports that it has none and raises nothing, which is the correct behaviour on a machine where `itgraph baselines` has never run, and avoids a first run that scores against defaults nobody chose.

Rollback is not running the pass. Alerts already raised stay; they are ordinary rows in a table the bot already knows how to deliver.

## Open Questions

- ~~Whether the spread should be per kind as well as per metric.~~ Measured: it should. 0.26 for aggregators against 0.37 for personal channels and vacancy feeds, on views.
- **Whether the small positive drift at four and eight hours** (+0.14, +0.25) is worth correcting in the curve or is an artefact of normalising to the eight-hour value. It biases toward under-alerting mature posts, which is the safe direction, so it is not urgent.
