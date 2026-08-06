## 1. Measure what is not yet measured

- [x] 1.1 Residual spread for **reactions, forwards and comments**, the same way views was measured: expected = channel median × factor × curve, then the dispersion of `log(actual/expected)` by age band. Views came out at 0.38 and flat; the other three have measured curves and unmeasured residuals, and hardcoding views' number would apply a views-shaped assumption to comments
- [x] 1.2 The mature-to-recent factor per metric — 0.43 is the views figure, and there is no reason forwards should share it
- [x] 1.3 Check whether the spread differs by channel kind. The curves do (vacancy feeds are visibly slower), so the residuals might; if they do, the threshold is per kind rather than the design changing
- [x] 1.4 Record all of it in the change before implementing against it. These numbers become defaults, and a default nobody can trace back to a measurement is a guess with a comment

## 2. Pure scoring

- [x] 2.1 `scoring/curves.py`: growth curves and the mature-to-recent factor from snapshot rows. Pure — no database, no clock of its own, every input passed in, as `alerts/cascade.py` and `affiliation/signals.py` are
- [x] 2.2 `scoring/score.py`: expected value at an age, and z. Also pure
- [x] 2.3 **Age is `observed_at` minus the publication date, never the schedule slot.** Samples are irregular by design — quiet hours confirmed, an eleven-hour outage already survived — and a scorer assuming the schedule was met mis-ages exactly the posts whose sampling was unusual
- [x] 2.4 **Score levels, not ratios.** `forwards/views` reads ~1.9× high at fifteen minutes because forwards front-load; scoring it against a mature baseline over-alerts on young posts. Each metric against its own curve, so age is corrected once rather than twice
- [x] 2.5 A metric a channel does not publish yields no score, not a score of zero — the same absent-is-not-zero distinction `derive/metrics.py` already preserves
- [x] 2.6 `tests/test_curves.py`, `tests/test_scoring.py`: the same reach scores differently on channels of different size; the same reach scores differently at different ages; an ordinary post scores near zero; a channel below the history minimum yields nothing. Fixtures should reproduce the measured numbers

## 3. Baselines

- [x] 3.1 `db/models.py`: `ChannelBaseline` (channel, metric, median, sample count) and `KindCurve` (kind, metric, age band, fraction, spread), each carrying the parameters it was computed under — as `AffiliationCandidate` records the parameters its evidence was scored with
- [x] 3.2 **Store the spread, do not hardcode it.** It is measured per metric and possibly per kind, and a constant in the code would quietly outlive the measurement it came from
- [x] 3.3 `db/baselines.py`: refresh and read back. A refresh replaces rather than accumulates — nothing may score against a mixture of two vintages
- [x] 3.4 Migration for the two tables
- [x] 3.5 A second migration for the four `AlertKind` values. **Postgres refuses to use a new enum value in the transaction that added it** — the `watch` revision documents this and it has cost a debugging session already
- [x] 3.6 `tests/test_baselines.py`: a refresh replaces; parameters are stored; a channel under the minimum gets no baseline

## 4. The pass

- [x] 4.1 `scoring/run.py`: load, score, raise, report — the shape `affiliation/run.py` and `alerts/run.py` already have. No network, no session lease, nothing it read is modified
- [x] 4.2 **One alert per post, under the kind of the highest-scoring metric.** Four independent alerts would put four messages about the most interesting post of the day into the chat within an hour — the same error as one message per reposter, from the other side
- [x] 4.3 Raise through `db/alerts.py` beside `raise_cascades`; the unique constraint does the deduplication it already does. **Nothing in `alert-delivery` changes** — if it turns out it must, the seam was drawn wrong and that is worth stopping over
- [x] 4.4 Report the channels with no baseline. "No alerts from this channel" and "this channel is not scored" are different facts and only one means quiet
- [x] 4.5 No baselines at all → raise nothing and say so, rather than scoring against defaults nobody chose
- [x] 4.6 `tests/test_scoring_run.py`, against a real database: one alert for a post unusual on several metrics; a re-run raises nothing; a later spike on another metric is a separate alert; nothing collected is modified

## 5. Replay

- [x] 5.1 The pass takes the moment to reason from; replay is that moment in the past. **The same code, not a second implementation** — a parallel scorer agrees on the cases anyone checks and diverges on the one that matters
- [x] 5.2 Replay writes no alert and sends nothing, and reports what would have fired
- [x] 5.3 Replay accepts a threshold other than the configured one, so an experiment costs minutes instead of a day
- [x] 5.4 Tests: a replay of the present names what the live pass named; a replay writes no rows

## 6. CLI and configuration

- [x] 6.1 `config.py`: threshold (default from the measured rate — z 3.0 gave ~9 alerts a day at 3.1× expected), minimum mature posts (30, which leaves 465 of 544 channels scorable today), baseline refresh interval, replay window
- [x] 6.2 `cli.py`: `itgraph baselines` to refresh, `itgraph score` to run, `--replay` and `--threshold` on the latter. Neither takes the session lease
- [x] 6.3 `tests/test_cli.py`: both run while the lease is held; neither connects to Telegram

## 7. Documentation

- [x] 7.1 `docs/PLAN.md`: the ratio-versus-level correction. The plan currently specifies `reactions/views`, which the measurements say is the wrong instrument inside the alerting window
- [x] 7.2 `src/itgraph/CLAUDE.md`: rows for the new modules
- [x] 7.3 `src/itgraph/README.md`: what the score means in plain terms — z 3 is "three times what a post of this age on this channel normally reaches" — the expected volume, and that `baselines` must run before `score`

## 8. Close out

- [x] 8.1 `make validate` green
- [x] 8.2 `openspec validate add-virality-scoring` green