## 1. Bands that cover the window

- [x] 1.1 `scoring/curves.py`: contiguous bands from 10 minutes to the end of the alerting window, replacing the six offset windows. Keep them **discrete** — a step function of measured medians lets a reader check "this band was fitted on 2 016 readings", which a smooth fit hides
- [x] 1.2 The reference band stays 8h, so `factor` keeps meaning what it means and the stored factors are still comparable across runs
- [x] 1.3 `min_band_samples` still excludes a thin band. More bands means fewer readings each, and the floor is what stops the coverage fix from buying itself with worse numbers
- [x] 1.4 A band past the reference is fitted like any other; its fraction is simply above 1. **Do not clamp it** — clamping would silently flatten the curve exactly where a still-growing post is most interesting
- [x] 1.5 `tests/test_scoring.py`: a reading at three hours is scored rather than refused; the band boundaries have no gap; a thin band is still omitted

## 2. A mature window with two ends

- [x] 2.1 `db/baselines.py`: upper bound on `mature_medians`. Measured per row as `fetched_at - date`, same as the lower bound — one cutoff date would mean a different window per channel, which is the correction this project already made once
- [x] 2.2 `config.py`: `baseline_mature_max_days` (120) and `baseline_min_channel_posts` 30 → 20. **The pair is the decision, not two settings.** 28–120d/20 and 28–180d/30 cost the same coverage and differ in bias (2.39 against 2.66); changing one without the other picks neither
- [x] 2.3 A validator refusing `max <= mature_days`: the window would be empty, every channel would lose its baseline, and the pass would report that as "no channel has enough history" — indistinguishable from a legitimately thin inventory
- [x] 2.4 `tests/test_baselines.py`: a post older than the window does not contribute; a channel with history only outside it gets no baseline and is counted; the window is measured per row

## 3. A spread per band

- [x] 3.1 `db/models.py` + migration: `MetricBaseline` carries a spread per band. The pooled spread stays — it is the fallback, not a leftover
- [x] 3.2 `scoring/curves.py`, `scoring/score.py`: fit and select the spread for the reading's own band
- [x] 3.3 A band with too few residuals takes the metric's pooled spread rather than going unscoreable. A slightly wrong ruler at one age beats a hole at that age — the lesson of task group 1, applied to itself
- [x] 3.4 `tests/test_scoring.py`: the same reading scores differently at two ages whose spreads differ; a thin band falls back rather than refusing; the fallback is visible to the caller

## 4. A curve for every kind

- [x] 4.1 `scoring/refresh.py`: fit a pooled all-kinds curve alongside the per-kind ones, and use it where a kind could not be fitted
- [x] 4.2 Record on the row that the curve was borrowed. **This is not the "no partial baselines" rule being relaxed** — that rule forbids mixing measured and assumed *silently*, and a borrowed curve that travels with the fact is a different object: reportable, queryable, withdrawable
- [x] 4.3 The refresh summary names which kind/metric pairs borrowed. `event` is 18 channels today; if that list grows, the pooling is covering for something else
- [x] 4.4 `tests/test_baselines.py`: a kind below the minimum gets the pooled curve and is marked; a kind with its own keeps it; the summary says which

## 5. Comments, re-measured

- [x] 5.1 Refit the factor and per-band spreads on the 2 301 posts now carrying comments. The median z of −0.74 says the factor of 1.00 — "a post reaches its full mature comment count by eight hours" — is too high
- [x] 5.2 Measure again **after** groups 1–4 land, not before. Three of the four fixes change what the residual is, so a verdict taken now would be about the old reference
- [x] 5.3 Record the answer either way. If the median lands near zero and the spread near 1, comments become alertable by setting `alert_spike_metrics` and nothing else. If they do not, that is a result and belongs in `docs/PLAN.md` beside the first one

## 6. Verification against the same week

- [x] 6.1 `itgraph baselines`, then `itgraph score --replay` over the same window the current configuration was measured on. The 40 alerts it would have raised are the comparison
- [x] 6.2 Expect the count to rise roughly as coverage does — 840 posts become scoreable. **If it moves by much more, the new bands are measuring something the old ones were not**, and that is worth stopping over rather than accepting as a better detector
- [x] 6.3 Re-measure the per-channel bias. The p95 should fall from 3.23 toward 2.39; if it does not, the window bound is not doing what the sweep said
- [x] 6.4 Re-measure median z per band. The drift from −0.16 to +0.26 should flatten, since the curve is now fitted where the readings actually are
- [x] 6.5 Confirm the channels-without-baseline count lands near 382. Far from it means something other than the window moved
- [x] 6.6 **Re-measure the hour-of-day effect.** It was ~1 z and partly an artefact of the coverage gap. Whatever survives is a real finding and the input to whether a calendar correction is worth a change of its own

## 7. Documentation

- [x] 7.1 `docs/PLAN.md`: replace the "spread ~0.38, flat across ages" claim with the measured 1.18 → 0.98, and record what the mature window is now bounded to and why
- [x] 7.2 `src/itgraph/README.md`: that `baselines` now excludes channels too quiet to fill the window, and that this is the cost of not scoring a channel against its former self
- [x] 7.3 The numbers from group 6 go beside the ones they replace, not over them. A default nobody can trace to a measurement is a guess with a comment

## 8. Close out

- [x] 8.1 `make validate` green
- [x] 8.2 `openspec validate calibrate-virality-scoring --strict` green
- [x] 8.3 Refresh baselines on the working database before the next `score`, and read the summary. The old run stays readable, so the two are comparable
