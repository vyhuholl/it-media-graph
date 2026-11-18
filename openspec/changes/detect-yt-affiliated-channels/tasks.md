# Tasks

## 1. Signals (pure functions)

- [ ] 1.1 Implement `yt/affiliation/signals.py`: title tokenisation
  (Unicode alphanumeric runs, lowercased, min length), shared-title-token
  signal with rarity weighting and cap, corroborated tokens withheld —
  verified by unit tests over synthetic payloads (distinctive token
  proposes, common and short tokens do not, customUrl never read,
  Cyrillic token counts, one-observation-once exclusion)
- [ ] 1.2 Implement the description-reference signal over
  `derive/yt_references.py`'s `read_text`/`read_link`/`Known` machinery:
  handle links, id links, bare known handles; mutual > one-way;
  self-reference, ambiguous handle, custom URL and shortener produce
  nothing; unresolvable refs counted — verified by unit tests including
  the reused-grammar edge cases (host variants, trailing punctuation)
- [ ] 1.3 Implement the corroborated-token-group signal: reference between
  two carriers ⇒ whole group at flat strength, own carrier cap — verified
  by unit tests (group proposed, non-carrier and unresolvable references
  corroborate nothing, over-cap group skipped, larger group not weaker)

## 2. Detection and parameters

- [ ] 2.1 Implement `yt/affiliation/detect.py`: merge signals into one
  candidate per lexicographically ordered pair, weighted score, ranking
  with deterministic tie-breaks, `_worth_proposing` filters (self-pair,
  same family) — verified by unit tests (one signal suffices, corroboration
  ranks higher, pair proposed once regardless of direction)
- [ ] 2.2 Implement parameter validation (positive minima, caps ≥ 2,
  non-negative weights) and the coverage result (payloads, descriptions,
  unresolvable refs, per-signal "could not run") — verified by unit tests
  naming the refused parameter

## 3. Schema

- [ ] 3.1 Add the Alembic revision: `yt_affiliation_runs`,
  `yt_affiliation_candidates` (unique pair index, decision columns,
  `origin`), `yt_channel_families` view, enums `yt_ref_direction` /
  `affiliation_decision` / `candidate_origin` reused or mirrored —
  verified by `uv run alembic upgrade head` then `downgrade` on a scratch
  `_test` database (guard refuses otherwise)
- [ ] 3.2 Add SQLAlchemy models mirroring the Telegram affiliation tables
  with the differences design D3 names — verified by the migration and the
  suite's schema build staying green

## 4. Database layer

- [ ] 4.1 Implement `db/yt_affiliation.py`: inventory load (payloads,
  handles, statuses; no status filter), run recording, candidate upsert
  (decision columns excluded from the update set), candidate listing with
  seed filter and decided filter — verified by database tests: re-run
  refreshes evidence and leaves decisions, hidden non-seed pair appears
  when a channel becomes a seed without recomputation
- [ ] 4.2 Implement family reads: `yt_channel_families` view read,
  `family_of` coalesce — verified by a database test where a bridging
  confirmation merges two families and a withdrawal splits them

## 5. Decisions

- [ ] 5.1 Implement confirm / reject / withdraw for YouTube channels
  (UC-id or `@handle` refs, ambiguous handle fails naming both, unknown
  channel fails writing nothing, group confirm records every pair,
  operator origin without a prior candidate) — verified by database tests
  covering every spec scenario in "Family Confirmation"
- [ ] 5.2 Implement token rejection: reject every pending pair the token's
  group proposed, note naming the token, decided pairs untouched —
  verified by database tests including "a rejected token does not veto new
  carriers" (a new carrier's pairs are proposed on the next run)

## 6. CLI

- [ ] 6.1 Implement `itgraph yt affiliates` with all threshold/weight/
  limit/all/any-status options, coverage output, ranked list — verified by
  CLI tests (defaults documented in `--help`, invalid parameter refuses
  writing nothing, counts of proposed vs awaiting review stated)
- [ ] 6.2 Implement corroborated-group presentation: one block per token
  with channels/pairs counts, hidden-pair count under `--limit`, ready
  `itgraph yt family` confirm line, pairs several signals reached shown
  once with all evidence — verified by CLI tests over a synthetic
  inventory with a partly-decided group and a truncated block
- [ ] 6.3 Implement `itgraph yt family` with `--reject`, `--reject-token`,
  `--withdraw`, `--note`; reject/withdraw take exactly two — verified by
  CLI tests mirroring the decision tests' scenarios

## 7. Backup and restore

- [ ] 7.1 Add `yt_affiliation_candidates` and `tg_affiliation_candidates`
  to `INVENTORY_TABLES`; extend `files/neutralise.sql` (yt
  `decision_note`) and `files/readback.sql` (yt note count) — verified by
  the backup-tier test listing the hand-reviewed tables and the
  statement-removal tests for both SQL files
- [ ] 7.2 Update `src/itgraph/README.md`'s restore section if it
  enumerates neutralised notes — verified by reading the restored-copy
  check output naming the new table

## 8. Integration

- [ ] 8.1 Run `make validate` green (lint, typecheck, eight-worker suite,
  ansible-lint) and a manual smoke of `yt affiliates` / `yt family`
  against a local test database seeded with synthetic payloads
