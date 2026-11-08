# Proposal

## Why

Comments are measured and never alert. The score every metric shares — the log of observed over expected, divided by a measured spread — cannot describe a counter that is zero in half its readings: replayed over the 30 days to 2026-11-07, it raises 0.1 comment alerts a day at the shared threshold, while the operator, reading the posts it misses, finds them to be natural reactions to news. Comments are the one metric that says people are arguing or asking, so they are wanted in the first release.

## What Changes

- **Comments are scored as counts.** The expectation is the mean comment count of the channel's newest posts at eight hours, zeros included, scaled to the post's age; the score is how improbable the observed count is under a negative binomial with that mean and its kind's dispersion, read as the z with the same tail. Views, reactions and forwards keep the score they have.
- **The baseline refresh measures what that needs:** the comment curve as a ratio of sums, which keeps the zeros the median of fractions threw away, and the dispersion per channel kind by maximum likelihood, stored with the run. A comment curve or dispersion that a kind is too thin for is borrowed from the pool, as curves are today.
- **Comments may raise an alert.** The shared threshold stays z 3.7; a comment reading under a configured minimum count, 10 by default, raises nothing. Replayed on the code as built over the last 16 days of production readings, with baselines refreshed daily, that adds 0.75 messages a day after folding, against about eight today. The first, offline estimate was 1.3.
- **A comment alert says the count:** "💬 Комментарии — 117, обычно около 1 (z 14.2)". The count and the expectation that decided it are recorded on the alert, since a later baseline run would give another expectation.
- **`docs/PLAN.md`'s "Comments are measured but do not alert"** is rewritten with what was measured and decided.

**Out of scope:**

- A filter for giveaways and contact exchanges. Two of the top twenty posts were such; the operator judged the rest natural and decided against filtering.
- Changing how the other three metrics are scored, or their threshold.
- Comments' content, and who commented: nothing here reads either.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `virality-scoring`: comments are measured against a mean that keeps their zeros and scored by a count model with a dispersion per kind; a minimum count is a parameter; comments may alert.
- `alert-delivery`: a comment alert records the count and the expectation that decided it, and states both.

## Impact

- **Code:** `scoring/score.py` (the count score), `scoring/curves.py` (the count curve, the dispersion, the mean reference), `scoring/refresh.py`, `scoring/run.py`, `db/baselines.py`, `db/alerts.py`, `bot/render.py`, `config.py`. No new dependency: the tail is the regularised incomplete beta, written out.
- **Schema:** one Alembic revision: `metric_baselines.dispersion`, and `alerts.observed` and `alerts.expected`, all nullable.
- **Rollout:** comments start scoring at the first baseline refresh after the deploy, which stores the dispersion; until then a run without it scores no comments.
- **Volume:** about 0.7 more messages a day, on the same delivery cap and quiet hours.

## Decision record

Brought as questions in the explore session of 2026-11-07. The operator's answers, with the three channels named in them replaced by what they are, since this repository is public: «По вопросам всё как ты рекомендуешь. Выгрузку делай сейчас.», and after reading the top twenty: «Нет, таких постов там всего два — пост для обмена контактами в LinkedIn у [канала вакансий] и розыгрыш мерча у [сообщества]. И ещё один пост от [агрегатора] я не смогла вытащить — его уже удалили. Всё остальное — естественная, не накрученная реакция на новости. … Фильтр конкурсов не нужен. Change создавай.» So, confirmed as of that date:

1. A comment alert means unusually many comments for this channel, with an absolute minimum.
2. One or two comment messages a day on top of the others.
3. One post, one alert, under its strongest metric; a comment alert states the count and the usual count.
4. No filter for giveaways or contests.
5. The negative binomial with a dispersion per kind, the shared z 3.7 and a minimum of 10 comments, as measured.
6. Shown that the code as built gives 0.69 messages a day rather than 1.3, below the one or two of item 2, the operator kept z 3.7 and a minimum of 10 over a minimum of 5 (0.94) or a threshold of its own (z 3.3, 1.44): «1». Re-measured after `/opsx:verify` corrected the dispersion's fit: 0.75, 1.06 and 1.38. The first week in production is read before anything moves (`tasks.md`, 5.4).

Nothing here is uncovered by the requirements.
