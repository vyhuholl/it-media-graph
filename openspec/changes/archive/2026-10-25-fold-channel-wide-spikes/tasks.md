## 1. Schema

- [x] 1.1 Revision: `ALTER TYPE alert_delivery ADD VALUE 'folded'`, empty downgrade, alone in its revision
- [x] 1.2 Revision: `alerts.folded_into` self-reference with `ON DELETE CASCADE`, a `CHECK` against folding into itself, a partial index `WHERE folded_into IS NOT NULL`; the downgrade settles folds still waiting for their head
- [x] 1.2a Revision: `baseline_runs.min_band_spread_posts`, nullable — the run records the rule it was fitted under
- [x] 1.3 `db/models.py`: `AlertDelivery.FOLDED`, `Alert.folded_into`, and the docstrings that say what a fold is
- [x] 1.4 Verify both revisions up and down on a scratch `_test` database, never the working one

## 2. Bursts and folding, pure

- [x] 2.1 `scoring/fold.py`: `burst_heads` — chain consecutive messages of a channel less than the gap apart, map each to the chain's first message
- [x] 2.2 `scoring/fold.py`: `fold` — order spikes by crossing moment then score, fold each into its channel's head if it crossed within the window of it, otherwise make it the head
- [x] 2.3 `scoring/score.py`: `Spike` carries the moment it crossed
- [x] 2.4 `tests/test_scoring.py`: a burst chained over several gaps is one post; a gap above the limit splits it; spikes of one channel fold into the earliest; a crossing past the window is a new head; two channels never fold together; an existing head is folded into; ties go to the higher score

## 3. The scoring pass

- [x] 3.1 `scoring/run.py`: `Reading` keeps `observed_at`; `_load` collapses bursts after albums
- [x] 3.2 `scoring/run.py`: per post, the best score and the first reading at which it crossed
- [x] 3.3 `db/alerts.py`: the spike rows already raised in the window, and each channel's newest unfolded spike alert inside the fold window
- [x] 3.4 `db/alerts.py`: `raise_spikes` writes heads first, then folds against their ids, in one transaction; returns both counts
- [x] 3.5 `scoring/run.py`: live pass excludes already-raised spikes before folding; a replay folds from no heads; `SpikeSummary` reports folds and what each folded into
- [x] 3.6 `cli.py`: the replay listing marks a folded spike and names its head
- [x] 3.7 `config.py`: `alert_spike_fold_hours` (6), `scoring_burst_seconds` (60), with the measurements beside them
- [x] 3.8 `tests/test_scoring_run.py`: one channel crossing together raises one head and folds the rest; a later pass inside the window folds into the existing head; after the window, a new head; a folded post is not raised again after the window; a burst raises one alert under its first message; the replay reports the same folds; the existing "later spike on another metric" test asserts the fold

## 4. The band spread floor

- [x] 4.1 `scoring/refresh.py`: residuals keep which post they came from
- [x] 4.2 `scoring/curves.py`: `fit_band_spreads` refuses a band below the distinct-post minimum as well as the reading minimum
- [x] 4.3 `config.py`: `baseline_min_band_spread_posts` (100) with the bootstrap beside it
- [x] 4.4 `tests/test_scoring.py`, `tests/test_baselines.py`: a band with enough readings from too few posts gets no spread of its own and stores `NULL`; the existing stored-spread test sets the floor it relies on

## 5. Delivery

- [x] 5.1 `db/alerts.py`: `claim_undelivered` and `outstanding_alerts` exclude folds; `settle_folds` marks folds of delivered heads `folded`; a count of distinct other posts folded into a head
- [x] 5.2 `bot/render.py`: the head's message states how many other posts of the channel were elevated with it, and says nothing when there are none
- [x] 5.3 `bot/app.py`: render with the fold count; settle folds on every pass, whatever else the pass did
- [x] 5.4 `bot/handlers.py`: status says how many alerts were folded
- [x] 5.5 `tests/test_bot.py`, `tests/test_alerts_db.py`: a fold is never claimed; a head's message carries the count; a fold is settled after its head and not before; settled folds do not count towards the cap; a fold waiting for a held head is not outstanding

## 6. Verification on the working data

- [x] 6.1 In-memory replay of the last 14 days with the new code and an in-memory refit (no write): alerts/day, series, `aigency360`, against the 14.0 / 81 / 66 measured before
- [x] 6.2 The same for 18 October under run 11: `rucodefestival` should come down from 13
- [x] 6.3 How many band spreads the 100-post floor sends back to the pooled spread, per kind

## 7. Documentation and close-out

- [x] 7.1 `docs/PLAN.md`: channel-wide events, bursts and the band-spread floor, with the numbers
- [x] 7.2 `src/itgraph/README.md`: what a folded alert is and how it shows in the bot
- [x] 7.3 `src/itgraph/CLAUDE.md`: a row for `scoring/fold.py`
- [x] 7.4 `make validate` green
- [x] 7.5 `openspec validate fold-channel-wide-spikes --strict` green
