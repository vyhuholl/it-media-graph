## Why

The spike bot sends the same event many times. When a whole channel gets a traffic boost — bought views, an ad, a broadcast day — every post younger than the scoring window crosses the threshold in one pass, and each becomes its own message. Over the last 14 days **81 of 196 spike alerts (41%) arrived in same-channel series**; `@aigency360` alone produced 66, in daily batches of 6–10, because it receives a near-identical injection of +400…+1500 views on every post of the day each evening. Per post the scorer is right — the views did rise three to ten times. What it gets wrong is the unit: one event about one channel is told as N events about N posts.

Two smaller defects amplify the series and were found on the same data. A post published as several messages seconds apart (text, then each video) is scored once per message, because only albums are collapsed. And an age band's own spread is trusted from as few as 20 readings — on 18 October the `event` kind's 150-minute band had a spread of 0.104 against a pooled 0.345, so a post at ×1.41 of its expectation scored z 6.86, and `@rucodefestival` sent 13 alerts in one afternoon.

## What Changes

- **Spike alerts of one channel inside a window are one event.** The first spike of a channel becomes the *head*; every spike of the same channel that crosses within `alert_spike_fold_hours` (6) of it is recorded as *folded into* the head and is not sent on its own. The head's message says how many other posts of the channel were elevated with it — which is the useful finding ("the whole channel is getting traffic"), not noise. Measured: same-event crossings fall within 5.5 h of each other, and everything 12–24 h apart is a separate event (the next day's boost, or genuinely different posts), so 6 h folds the series without hiding the next day.
- **A fold is recorded, never dropped.** Folded spikes are written to the alert queue like any other, so a folded post is not raised again once the window passes, the replay and the status report can count them, and the head's message can name the number. A fold is marked delivered once its head has been, and from then on it is no longer outstanding.
- **Messages published seconds apart are one post.** Consecutive messages of one channel less than `scoring_burst_seconds` (60) apart are scored as their first message only, as an album already is. The density of inter-message gaps falls to the background rate at about a minute; below it is the "text, then the videos" pattern.
- **A band's own spread needs enough distinct posts.** A band spread is used only if measured on at least `baseline_min_band_spread_posts` (100) distinct posts; otherwise the band takes the pooled spread, which is the fallback already specified for a thin band. Measured by bootstrap: at 100 posts the 5th percentile of the estimate is 0.78 of the full-sample value, matching the worst systematic error of the pooled fallback itself (0.74); at 20 it is 0.52.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `virality-scoring`: a channel's spikes inside a window raise one alert and fold the rest into it; a burst of messages is scored as one post; a band's own spread requires a minimum number of distinct posts, not only of readings; the replay reports folds.
- `alert-delivery`: a folded alert is told through its head rather than sent on its own, and is accounted for — never counted as outstanding, never silently withheld; the status report distinguishes folded alerts.

## Impact

- `src/itgraph/scoring/fold.py` (new) — bursts and folding, pure
- `src/itgraph/scoring/run.py` — burst collapse in `_load`, the moment each post crossed, folding before the write, the summary
- `src/itgraph/scoring/score.py` — `Spike` carries when it crossed
- `src/itgraph/scoring/curves.py`, `src/itgraph/scoring/refresh.py` — the distinct-post floor on band spreads
- `src/itgraph/db/alerts.py` — recent heads, writing heads and folds, excluding folds from the delivery queue, settling folds
- `src/itgraph/db/models.py` + one migration — `alerts.folded_into`, the `folded` delivery value
- `src/itgraph/bot/app.py`, `src/itgraph/bot/render.py`, `src/itgraph/bot/handlers.py` — the head's message, settling folds, status
- `src/itgraph/config.py` — `alert_spike_fold_hours`, `scoring_burst_seconds`, `baseline_min_band_spread_posts`
- `src/itgraph/cli.py` — the replay listing shows folds
- `docs/PLAN.md`, `src/itgraph/README.md`, `src/itgraph/CLAUDE.md`

No change to collection, to cascade alerts, or to the channel medians — their staleness is a separate change. The bot's database role needs no new grant: it already reads and updates `alerts`.
