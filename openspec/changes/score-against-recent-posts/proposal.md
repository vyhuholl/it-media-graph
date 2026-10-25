## Why

The reference every post is scored against has not moved since July. `mature_medians` takes a channel's posts that were *read* at 28–120 days of age, but `raw_messages` keeps the first fetch and `itgraph watch` reads every post at age zero — so no post published since August has ever entered a median. All eight weekly refreshes since 31 August produced identical medians, computed from posts published between late March and early July. The spec's own scenario, "a channel that has grown is measured against what it is now", has been false for the whole time the bot has been talking.

The cost is per-channel bias, and it is large. Scored rolling out of sample over the last 28 days, an ordinary post's median z per channel spans p5 −3.24 to p95 +1.17, and **72 of 229 channels sit more than 1 z off zero**. The positive side is noise the operator reads: `dmitrii_ireshev_Agile_PMP` (+6.2) has grown ninefold since July and alerts on almost every post; `turboproject` (+2.2) on most. The negative side is silence nobody notices — `j_links` at −5.2 or `pythonl` at −3.6 could triple their reach and never alert, and for them a fresh mature median would not help, because their bias is not growth but the kind-wide factor misdescribing a channel whose posts keep accruing for weeks.

`docs/PLAN.md` called a channel's own history at the same post age "unreachable", and in July it was: a median channel posts 0.53 times a day. The watch loop has now collected 80 days, and the measurement is no longer hypothetical.

## What Changes

- **A channel is measured against its own newest posts at eight hours.** Its reference becomes the median reference-band value (the largest reading at 7 h–9.5 h, the same statistic the curve is normalised to) of its newest `baseline_recent_posts` (20) posts within `baseline_recent_max_days` (90), required to number at least `baseline_recent_min_posts` (10). No factor: the reference is already at the age the curve is normalised to. Measured on the same 229 channels, rolling out of sample: bias MAD **0.81 → 0.15 z**, channels beyond 1 z **72 → 3**, `dmitrii_ireshev_Agile_PMP` +6.2 → +1.3, `turboproject` +2.2 → 0.0. It also fixes the factor mismatch, which is not staleness at all: `j_links` −5.2 → −0.1, `pythonl` −3.6 → −0.1.
- **Newest posts, not a window of days.** A 90-day window lags a fast-growing channel by six weeks (`dmitrii_ireshev_Agile_PMP` stays at +3.8); the newest twenty follow it. The price is that a sustained channel-wide shift becomes normal after about twenty posts — a day for a prolific channel, which for one boosted every evening is the behaviour the operator asked for.
- **Baselines are refreshed daily.** The reference is only as fresh as the last refresh; daily against weekly narrows the bias MAD from 0.19 to 0.15 and costs a minute of database time. `baseline_refresh_days` and the timer follow.
- **The spread is measured out of sample.** Each residual is taken against the reference its post would have been scored against — built from posts whose reference band had closed before it was published — so no post narrows the ruler it is divided by. In sample, measured, the residuals come out 5% narrower: small, and exactly the over-alerting direction.
- **The mature median stays, as the fallback, and says so.** 142 channels publish too rarely to fill a recent reference; they keep the mature median and the kind's factor, frozen since the backfill, and the refresh and the scoring pass count them separately. Measured over 14 days they produced 2 of 196 spike alerts.
- **The threshold is re-priced.** Removing per-channel bias narrows the residual spread, which raises every z for the same multiple. As in `calibrate-virality-scoring`: fix, measure, then price — on the same fortnight, to the volume measured just before this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `virality-scoring`: what a channel's reference is (its newest posts at the reference age, with the mature median as a reported fallback); that the spread is measured against the reference each post would actually have met; what makes a channel scoreable and how the two kinds of reference are counted.

## Impact

- `src/itgraph/db/baselines.py` — the recent-reference query; storing and loading which reference a channel has
- `src/itgraph/scoring/curves.py` — the reference statistic, pure, reused for both the stored reference and the rolling one
- `src/itgraph/scoring/refresh.py` — recent references beside mature medians; residuals against the rolling reference; the summary
- `src/itgraph/scoring/run.py`, `src/itgraph/scoring/score.py` — a baseline whose reference needs no factor; the summary line counts both references
- `src/itgraph/db/models.py` + one migration — `channel_baselines` records which reference a row is; `baseline_runs` the three new parameters
- `src/itgraph/config.py` — `baseline_recent_posts`, `baseline_recent_min_posts`, `baseline_recent_max_days`, `baseline_refresh_days`, and the re-priced `alert_spike_z`
- `deploy/group_vars/all/main.yml` — `baselines_schedule` daily, and the template's fallback default with it
- `docs/PLAN.md` — the "unreachable" paragraph, corrected with what was measured; `src/itgraph/README.md`

No collection change, no new Telegram request: everything above is read from snapshots already on disk. Re-reading month-old posts to refresh the fallback is deliberately not part of it — see `design.md`.
