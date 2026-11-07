# Tasks

Tasks marked **(operator)** reach the server or the test environment, and are the operator's. Every task that changes code ends on a green `make validate`. No test holds a real payload, channel id or username: fixtures are synthetic.

## 1. The count arithmetic

- [x] 1.1 `scoring/score.py` (or a module of its own beside it): the negative binomial upper tail as the regularised incomplete beta by continued fraction, in log space, and the z of design.md, Decision 1, with its ±40 ceiling. Verify: tests against the tail summed term by term from `math.lgamma` for small counts, against values computed once with scipy and written into the test with their parameters, far in the tail without underflowing to zero too early, monotonic in the count, and −40 at zero.
- [x] 1.2 `score_metric` scores comments by it when the baseline carries a dispersion, and refuses them when it does not. Verify: tests per scenario of `specs/virality-scoring`, "Comments Are Scored As Counts" — no comments on a quiet channel, thirty on a channel of about one, two kinds of different dispersion, the underflowing count; and that views, reactions and forwards score exactly as before.

## 2. The comment baselines

- [x] 2.1 One Alembic revision: `metric_baselines.dispersion`, `alerts.observed`, `alerts.expected`, all nullable. Verify on a scratch database (`_test`): upgrade, downgrade, upgrade; an existing run and existing alerts read back unchanged.
- [x] 2.2 `db/baselines.py` and `scoring/curves.py`: comment history keeps its zeros; the comment reference is the mean of design.md, Decision 2; the comment curve the ratio of sums of Decision 3. Verify: tests that the reference of twenty posts with two at ten and the rest at zero is one, that it needs ten posts and gives none under, that zeros shape the curve, and that the other metrics' reference and curve are unchanged.
- [x] 2.3 `scoring/refresh.py`: the dispersion per kind by maximum likelihood against each post's rolling reference, borrowed from the pool when the kind is thin, stored with the run; loaded into the comment baseline. Verify: tests that a synthetic kind drawn with a known dispersion fits it within a stated tolerance, that a post never takes part in its own reference, that a thin kind borrows and is reported, and that a run stored without a dispersion scores no comments and says so.

## 3. Crossing and the alert

- [x] 3.1 `alert_spike_comments_min` and `comments` in `alert_spike_metrics`, with their measurement in `config.py`'s comments; in `scoring/run.py` a comment reading under the minimum neither crosses nor becomes the post's best. Verify: tests of nine comments at any z raising nothing, ten raising, one post one alert across a comment and a views score, and a replay listing comment crossings.
- [x] 3.2 `db/alerts.py` writes the count and the expectation on a comment spike; `bot/render.py` says them as design.md, Decision 5. Verify: tests per scenario of `specs/alert-delivery`'s two amended requirements, «обычно меньше одного» under one, and the other kinds' messages unchanged.

## 4. Docs

- [x] 4.1 `docs/PLAN.md`, "Comments are measured but do not alert", rewritten with the measurement of 2026-11-07 and what was decided; `src/itgraph/README.md`'s scoring section; `src/itgraph/CLAUDE.md`'s rows for the modules that changed. Verify: every figure in them is one in design.md.

## 5. Verification

- [x] 5.1 The scratch replay of design.md, Decision 7: the pulled readings in a scratch database on the operator's machine, the real `itgraph baselines` and `itgraph score --replay` over the 30 days. Verify: the comment messages a day within noise of 1.3, the top posts the ones the operator read, recorded here; then the pulled files deleted.

  Run on 2026-11-07 with the real refresh and scoring over the pulled readings, baselines refreshed for each of the last 16 days in order (the first 14 of the 30 cannot be, since a refresh fits on the 14 days before it). At z 3.7 and a minimum of 10: **0.69 comment messages a day**, eleven posts, the same posts the offline model raised but for borderline ones at z 3.7–4.1. The offline model, counted the same way over the same 16 days, gives 1.00; it had counted every part of an album and every message of a burst into the channel's mean (1.19 before that was fixed), and fitted its curve and dispersion on the days it scored. The 1.3 a day the decision rested on is therefore about 0.7–0.8 on the code as built. Measured alternatives, same 16 days: minimum 5, 0.94; minimum 0, 1.00; z 3.3 with minimum 10, 1.44 (a threshold of its own for comments). The operator's choice, the same day: «1» — z 3.7 and a minimum of 10, kept. The pulled files and the scratch database deleted afterwards.

  Re-run after `/opsx:verify`, whose answer was «Правь все warnings и suggestions. Выгрузка нужна.», once the dispersion was fitted on posts as the scorer counts them: a fresh read-only pull at 03:12 Moscow time, the database not restarted, the same 16-day replay. **0.75 a day** at z 3.7 and ten (twelve posts, the same posts but for borderline ones); minimum 5, 1.06; none, 1.19; z 3.3, 1.38. The dispersions now agree with the offline fit. The pulled files and the scratch database deleted again.
- [x] 5.2 `make validate`, `make test-e2e`, `openspec validate --strict`. Verify: their output.

  2026-11-07: `make validate` 2142 passed with ansible-lint; `make test-e2e` 49 of 49, its detection pass running the comment scoring on a restored copy; the three open changes valid under `--strict`. Again after the fixes of `/opsx:verify`: `make validate` 2144 passed, `make test-e2e` 49 of 49.
- [ ] 5.3 **(operator)** In the test environment, after `adopt-infrastructure-requirements`' 13.6: `itgraph baselines`, then `itgraph score --replay` over 14 days. Verify: the comment volume recorded here.
- [ ] 5.4 **(operator)** A week after production's first run: the comment messages a day, and the operator's verdicts on them, recorded here.

## Workflow follow-up

- `/opsx:verify`, then `/opsx:archive` once 5.4 is done.
