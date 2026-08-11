## 1. Schema: two evidence columns, two run parameters

- [x] 1.1 `db/models.py`: `AffiliationCandidate.handle_token` (`Text`, nullable) and `handle_token_channels` (`Integer`, nullable). The docstring says why the pair of values repeats across every pair of one group rather than living in a table of its own: a group has no attributes beyond the handle and its size, decisions are per-pair, and membership stored separately can disagree with the pairs — which is the failure `channel_families` exists to make impossible
- [x] 1.2 `db/models.py`: `AffiliationRun.max_handle_token_channels` (`Integer`) and `weight_handle` (`Float`), both **nullable**, unlike every other parameter column on that table. A run recorded before this change genuinely had no such parameter, and a server default would claim it ran under one it never saw. The docstring says so, because the asymmetry otherwise reads as an oversight
- [x] 1.3 Alembic revision: four additive nullable columns, nothing existing changed in shape or nullability. No backfill — the first run after the upgrade populates the evidence for the pairs it proposes
- [x] 1.4 Verify the revision on a scratch database whose name ends in `_test`, reading `alembic upgrade --sql` first. Confirm the downgrade drops exactly those four columns and touches no decision column — `decision`, `origin`, `decided_at` and `decision_note` must survive a rollback, since they are hand-made and exist nowhere else
- [x] 1.5 Test in `test_affiliation_db.py`: a run row written before this change reads back with `NULL` for both new parameters, and reading it raises nothing

## 2. The signal

- [x] 2.1 `affiliation/signals.py`: a private `_HANDLE` pattern, `@([A-Za-z0-9][A-Za-z0-9_]{3,31})`. The docstring states why it is not `_TEXT_MENTION` and must never migrate there: the two answer different questions, and a false positive here costs nothing because the match is joined against a closed set of username tokens, while a false positive in `derive/references.py` costs a `pending_mentions` row, a resolve attempt and quota on a handle that can never resolve
- [x] 2.2 Same module: `HandleSignal` — pair, strength, the handle, and how many channels carry it. Same shape as the four existing signal results, so `detect` merges it without a special case
- [x] 2.3 Same module: `named_handle_tokens(usernames, descriptions, *, thresholds)`. Reuse `_tokens` for the inverted index, read handles out of each description with `_HANDLE`, and keep a token only when **a channel carrying it names it in its own description**. Emit every pair among the carriers
- [x] 2.4 Same function: the carrier requirement is the precision of the signal and the docstring carries its measurement — accepting a handle named in *any* description admits `@yandex`, carried by 13 usernames and named by a channel that is not one of them, and turns 27 pairs into 105
- [x] 2.5 Same function: strength is a flat `1.0`, independent of `d`. The docstring says why the rarity formula is not reused — `(M + 1 − d) / M` collapses to `1/M` exactly at the cap, which is the behaviour this change exists to route around, and five channels signing one handle are not weaker evidence than two
- [x] 2.6 Same function: apply the existing `min_token_length`, and the new `max_handle_token_channels` as a combinatorial guard. Note in the docstring that it binds nothing on today's corpus — the largest group is 5 — and exists so a brand suffix landing on 40 channels cannot become 780 pairs in one block
- [x] 2.7 Same function: when two signed handles reach one pair, break the tie the way `_beats` already does — longer token, then alphabetical. `_tokens` returns a **set**, whose iteration order for strings moves with `PYTHONHASHSEED`, and the stored handle is what the operator reviews the pair on, so "which handle" is not cosmetic
- [x] 2.8 `affiliation/detect.py`: `Thresholds.max_handle_token_channels = 10` and `Weights.handle = 1.0`, each with the measurement behind it in a comment. `1.0` places a handle group level with a mutual description reference and above everything else the corpus produces, which matches the measured precision — seven of nine groups reproduce families already confirmed by hand
- [x] 2.9 Tests in `test_affiliation_signals.py`, one per scenario in the spec: a signed handle proposes every pair in the group; a handle named by a non-carrier proposes nothing; a five-channel group scores no lower than a two-channel one; a handle beginning with a digit is read; a handle naming no inventory channel is evidence all the same and is **not** counted into `refs_outside_inventory`; a token over the cap proposes nothing; a handle under `min_token_length` proposes nothing; a group with no stored description anywhere proposes nothing and stays eligible for the other signals

## 3. Ranking

- [x] 3.1 `affiliation/detect.py`: merge `HandleSignal` into the candidate map — `score += weights.handle * strength`, filling `handle_token` and `handle_token_channels`. Its evidence fields are disjoint from the other four, so signals keep accumulating without overwriting each other
- [x] 3.2 Same module: a token that is a named handle is skipped by the rarity signal, so one observation contributes once. Summing both would count the same shared token twice and rank a pair with a signed token above a pair with a token plus an independent edge signal. The comment records that this changes **zero shown pairs** on today's data — the seven groups at `d ≤ 3` where both could fire have every pair already filtered as an existing family or as a chat with its parent — so it is settled on principle while it is still free
- [x] 3.3 Same module: `validate_parameters` refuses `max_handle_token_channels < 2` — a handle on one channel is shared with nobody — and a negative `weight_handle`, each naming the parameter, before anything is loaded
- [x] 3.4 Tests in `test_affiliation_detect.py`: a handle group proposes every pair among its channels; a pair reached by a handle and by another signal appears once carrying both evidences; a group pair already in one family, and a chat paired with its own parent, are both excluded from review; a signed token contributes exactly once; an out-of-range new parameter fails before anything is written

## 4. Storing and reading the evidence

- [x] 4.1 `db/affiliation.py`: `record_run` writes the two new parameters onto the run row
- [x] 4.2 `db/affiliation.py`: `upsert_candidates` adds both evidence columns to its `set_`, **nulls included** — a signal that no longer fires under changed parameters must stop claiming it did — and still touches no decision column
- [x] 4.3 `db/affiliation.py`: `CandidateRow` carries the handle and its count, read in the same joined query as the rest so the grouped output needs no second round-trip
- [x] 4.4 Tests in `test_affiliation_db.py`: a re-run under a lower `max_handle_token_channels` clears the handle evidence on a pair that no longer qualifies while leaving its confirmation or rejection exactly where it was; a re-run under unchanged parameters writes no second row

## 5. `itgraph affiliates`

- [x] 5.1 `cli.py`: `--max-handle-token-channels` and `--weight-handle`, defaults read from `DEFAULT_THRESHOLDS` / `DEFAULT_WEIGHTS` like every other option
- [x] 5.2 `cli.py`: `_evidence` gains `handle:<token>/<count>`, in the shape the other four parts already use
- [x] 5.3 `cli.py`: group the rows by `handle_token` before printing. Each group is a block headed by the handle, how many channels it names and how many pairs it holds; the block sits at the position of its highest-scoring pair, ties broken on the pair itself; ungrouped candidates print exactly as today
- [x] 5.4 Same rendering: `--limit` keeps bounding **pairs**, so "at most that many candidates are shown" stays literally true, and a block the bound truncates states how many of its pairs are not shown
- [x] 5.5 Same rendering: each block prints the `itgraph family …` line that would confirm it, channels already filled in. Nine pairs is one decision, and retyping five usernames is where that decision gets made wrong
- [x] 5.6 `affiliation/run.py`: the "produced nothing for lack of data" line stops naming a single signal — two signals read descriptions now, and both are silent when there are none
- [x] 5.7 Tests in `test_cli.py`: a group prints as one block with its counts and its confirm line; a bound that cuts a group reports the hidden pairs and still shows no more rows than asked; the new options reach the pass; **no family link is written, whatever the scores**

## 6. Close out

- [x] 6.1 `make validate` green — lint, mypy, pytest, ansible-lint. No loosened config, no `# type: ignore` added to make an error disappear
- [x] 6.2 `make validate` green under two fixed `PYTHONHASHSEED` values as well as the default, since 2.7 exists precisely because set iteration order moves with it
- [x] 6.3 `README.md`: the fifth signal, its two options and their defaults, and what it is for. Say that description coverage bounds it as it bounds the reference signal — 528 of 3 547 channels have one
- [x] 6.4 `src/itgraph/CLAUDE.md`: the `affiliation/signals.py` row mentions that it holds a second, deliberately looser handle pattern, so the next reader does not "fix" it by pointing it at `derive/references.py`
- [x] 6.5 `openspec validate detect-author-handle-families --strict` green
- [x] 6.6 Take a full dump before upgrading the working database, per the backup rule, then `alembic upgrade head` against it
- [x] 6.7 Run `itgraph affiliates` against the real inventory and reconcile against the numbers this change was designed on: **9 groups; 27 pairs among them; 8 dropped as already one family and 6 as a chat with its parent; 13 kept for review; 0 overlapping a pair another signal already proposed.** Two groups produce anything — `1red2black` (9 pairs) and `atom` (4) — and the other seven reproduce families already recorded by hand. Any gap is explained before this is ticked, the way the discussion-chat and denominator gaps were explained last time.

  **Done: 199 proposals over 3 547 channels, 10 awaiting review. Every designed number reproduced; one gap, and it is the reading filter rather than the signal.**

  | quantity | designed | run |
  |---|---|---|
  | pairs proposed in total | 186 + 13 | **199** |
  | new candidate rows, all pending | 13 | **13** (319 → 332) |
  | `1red2black` pairs stored | 9 | **9** |
  | `atom` pairs stored | 4 | **4** |
  | pairs overlapping another signal | 0 | **0** |
  | pairs shown for review | 13 | **10** |

  The one gap is the last row, and it is the same class of gap as last time: **the probe applied `_worth_proposing` and nothing else, while the command also applies the seeds-only reading filter.** Three of the four `atom` pairs have no seed on either side — `atom_auto` is `rejected`, `atom_potential_chat` and `atom_iznutri` are `candidate` — so they are stored, reachable with `--any-status`, and correctly not put in front of the operator. The fourth, `atom_potential` (seed) + `atom_iznutri`, is shown. The probe over-counted; the command is right.

  The family the change was written for arrives as one block of nine pairs with its confirmation line ready to run, and `chat_1red2black` is in it — paired with its three siblings, never with `tg_1red2black`, which `linked_to` already records.
- [x] 6.8 Drop the scratch database used to verify the revision
