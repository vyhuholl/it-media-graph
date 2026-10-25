## 0. Before touching code

- [ ] 0.1 Agree with the operator how the live timers are kept off unfinished code: they run from this working copy, so every saved state is live. Either a git worktree on a branch (CLAUDE.md: only with the operator's yes), or `itgraph-score.timer` and `itgraph-baselines.timer` stopped for the duration

## 1. The reference, pure

- [ ] 1.1 `scoring/curves.py`: `recent_reference(values, *, newest, minimum)` — the median of a channel's newest reference-band values, or `None` under the minimum. Ordered by publication, so "newest" is a property of the input rather than of the caller's sort
- [ ] 1.2 `scoring/curves.py`: the rolling form — for one post, the reference built from the channel's posts whose reference band had closed (published at least the band's upper edge earlier) and that lie within the maximum age of it
- [ ] 1.3 `tests/test_scoring.py`: newest beats oldest; under the minimum is `None`, not a thin median; the rolling form never includes the post itself or a post published after it, nor one whose band was still open; the maximum age cuts

## 2. Storage

- [ ] 2.1 Revision: `baseline_reference` enum (`recent`, `mature`); `channel_baselines.reference`, filled `mature` for existing rows and then without a default; `baseline_runs.recent_posts`, `recent_min_posts`, `recent_max_days`, nullable
- [ ] 2.2 `db/models.py`: `BaselineReference`, the column, the three parameters, and the docstrings saying what each reference means
- [ ] 2.3 `db/baselines.py`: the recent-reference query — the largest 7 h–9.5 h reading per post, album first parts and burst heads only (`scoring/fold.burst_heads`), newest first, within the maximum age
- [ ] 2.4 `db/baselines.py`: store a channel's reference with its kind; `load_baselines` builds a recent one with factor 1
- [ ] 2.5 Verify the revision up and down on a scratch `_test` database, and `alembic check` against the models

## 3. The refresh

- [ ] 3.1 `scoring/refresh.py`: per metric, a recent reference where the channel has one, the mature median otherwise. The factor is still fitted from the mature medians — the fallback needs it
- [ ] 3.2 `scoring/refresh.py`: residuals against the rolling reference for channels that have one, against median × factor otherwise
- [ ] 3.3 `BaselineSummary`: how many channels are on each reference, per metric, and the line says so
- [ ] 3.4 `config.py`: `baseline_recent_posts` (20), `baseline_recent_min_posts` (10), `baseline_recent_max_days` (90), with the sweep beside them; `baseline_refresh_days` 7 → 2
- [ ] 3.5 `tests/test_baselines.py`: a channel with enough recent posts gets a recent reference and a channel without falls back; a channel with recent posts and no mature history is scored; the reference follows new posts across two refreshes; a residual never uses a reference containing its own post; the run records the three parameters

## 4. The scoring pass

- [ ] 4.1 `scoring/run.py`: the summary line counts channels on the fallback beside channels without a baseline
- [ ] 4.2 `tests/test_scoring_run.py`: a grown channel's ordinary post scores near zero against a recent reference; a shrunk channel's, likewise; a fallback channel is still scored

## 5. Measure, then price

- [ ] 5.1 Refit on the working data in memory, without writing: per-kind band spreads before and after, against the table in `design.md`
- [ ] 5.2 Re-measure per-channel bias rolling out of sample with the shipped code, against MAD 0.15 and three channels beyond 1 z
- [ ] 5.3 Replay the 14 days before the change with the shipped code at today's threshold and record the volume; then with the new baselines across thresholds, and take the one that reproduces it. The table goes into `docs/PLAN.md`
- [ ] 5.4 `config.py`: `alert_spike_z` re-priced, with the measurement and the previous value beside it

## 6. Deploy

- [ ] 6.1 `deploy/group_vars/all/main.yml`: `baselines_schedule` daily at 04:30, and the default in `deploy/templates/itgraph-baselines.timer.j2` to match, so the two cannot disagree; `make ansible-lint`
- [ ] 6.2 Upgrade the working database (the upgrade takes the dump), `itgraph baselines` once, read the summary
- [ ] 6.3 The playbook applies the timer, rather than an edit by hand on the machine

## 7. Documentation and close-out

- [ ] 7.1 `docs/PLAN.md`: correct "unreachable" with what was measured — the channel's own eight-hour value is reachable for 231 channels once the snapshots existed, and the decomposition is now the fallback rather than the method
- [ ] 7.2 `src/itgraph/README.md`: what a channel is measured against, why it moves daily, and what "on the fallback" means in the pass's line
- [ ] 7.3 `make validate` green
- [ ] 7.4 `openspec validate score-against-recent-posts --strict` green
