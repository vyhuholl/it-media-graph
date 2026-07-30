## 1. Schema: the family pointer and where candidates live

- [x] 1.1 `db/models.py`: `Channel.operator_id` — nullable `BigInteger`, foreign key onto `channels.tg_id` with `ON DELETE SET NULL`, matching `linked_to` rather than inventing a second convention. `CHECK (operator_id IS NULL OR operator_id <> tg_id)` in `__table_args__`, named through the existing convention. Docstring says what null means — no family recorded, *or* this is the canonical channel of one — and that the family key is `COALESCE(operator_id, tg_id)`
- [x] 1.2 `db/models.py`: `AffiliationRun` — one row per detection run, holding every threshold and weight as its own column, plus the coverage the run measured (`channels_scored`, `with_description`, `refs_outside_inventory`). Parameters live here and not on each candidate; a hundred candidates would otherwise carry eleven copies of the same eleven numbers
- [x] 1.3 `db/models.py`: `AffiliationCandidate` — `(channel_a, channel_b)` composite primary key with `CHECK (channel_a < channel_b)`, so an unordered pair is one row and the key does the deduplication whatever order the signals fired in. Evidence as explicit nullable columns per signal (`about_direction`, `shared_token`, `shared_token_channels`, `out_share`, `out_share_edges`, `out_share_src`, `edges_a_to_b`, `edges_b_to_a`), `score`, `run_id`, and the decision fields (`decision`, `origin`, `canonical_id`, `decided_at`, `decision_note`). No JSONB — the raw layer is where documents belong, derived tables get real columns
- [x] 1.4 `db/models.py`: the three new enums — `AffiliationDecision` (pending/confirmed/rejected), `CandidateOrigin` (signal/operator), `AboutDirection` (a_to_b/b_to_a/mutual) — through `_pg_enum`, storing values not names like every other enum here
- [x] 1.5 Alembic revision: the column, its check and foreign key, the two tables. Additive only, no backfill — the first detection run populates candidates from data already stored
- [x] 1.6 Verify the revision on a scratch database whose name ends in `_test`, reading `alembic upgrade --sql` first. Check the downgrade actually drops all three things, and that `ON DELETE SET NULL` is unreachable in practice because inventory rows are never deleted
- [x] 1.7 Test in `test_db.py`: a channel cannot name itself as operator (the check fires); `operator_id` naming a channel outside the inventory is refused by the foreign key

## 2. Reading a description

- [x] 2.1 `derive/references.py`: `extract_text_references(text) -> list[Reference]` — a pure scan of plain text for `t.me`-shaped substrings and `@mentions`, handing each hit to the existing `parse_tme_link` / `normalize_username`. `extract_references` cannot be reused: it reads Telegram entity offsets, and `ChannelFull.about` is a plain string carrying none. Docstring says which of the two a caller wants and why there are two
- [x] 2.2 Same function: strip trailing punctuation off a link before parsing. It recovers zero references on today's data — measured — and costs one `rstrip`; the note belongs in the docstring so the next reader does not re-measure it
- [x] 2.3 Tests in `test_references.py`: a description with an `@mention`, with a bare `t.me/name`, with both, with a link followed by a full stop, with an invite link, with a service path, with a non-Telegram URL, and with nothing. The `@mention` case carries the weight — 155 of the 177 handles in real descriptions are mentions, not links

## 3. The four signals

- [x] 3.1 New package `src/itgraph/affiliation/` with an empty `__init__.py`; add its rows to the module table in `src/itgraph/CLAUDE.md`. Outside `tg/` because it makes no request, outside `derive/` because it does not read the raw message layer
- [x] 3.2 `affiliation/signals.py`: a `Signal` result carrying the pair, the strength in `[0, 1]` and the evidence fields it filled. Pure functions over plain data structures — no session, no engine — so every scenario in the spec is testable without a database
- [x] 3.3 `affiliation/signals.py`: the description signal. A reference found one way is evidence at strength `0.5`; found both ways, `1.0`. A reference whose target has no stored description is **not** penalised — 31 of 37 point at exactly that, so requiring mutuality would measure metadata coverage instead of affiliation. A handle matching no inventory row forms no candidate and is counted into `refs_outside_inventory`. A description linking its own channel yields nothing
- [x] 3.4 `affiliation/signals.py`: the shared username token signal. Split on `_` and `-`, keep tokens at least `min_token_length` long, build an inverted index token → channels, and emit a pair only for tokens carried by at most `max_token_channels` channels. Strength `(M + 1 − d) / M`. The document-frequency cap is the whole signal: without it `channel`, `tech`, `news`, `jobs` and `data` alone produce most of the 216 raw pairs. A channel with no username participates in nothing here and in everything else
- [x] 3.5 `affiliation/signals.py`: the outgoing concentration signal. Group edges by `(src, dst)`, skip any source with fewer than `min_out_edges` outgoing edges, emit the top target when its share reaches `max_share_min`. Strength is the share itself — rescaling it from the threshold would give a pair firing exactly at the threshold a strength of zero and drop it out of the ranking it just entered. Evidence records the share, its denominator, and which side was the concentrated one
- [x] 3.6 `affiliation/signals.py`: the mutual density signal. Both directions must carry at least `min_mutual_edges`; strength `min(1, min(n_ab, n_ba) / (2 · K))`. Evidence records both counts
- [x] 3.7 `affiliation/signals.py`: an `edge_kinds` parameter threading through both edge-based signals. Measured at the defaults, concentration finds 10 candidates over forwards alone, 13 over mentions alone, 19 over both — neither kind is the signal by itself, so the default is both
- [x] 3.8 Tests in a new `test_affiliation_signals.py`, one per scenario in the spec: each signal fires and does not fire at its boundary; the token cap suppresses a common token and admits a rare one; concentration ignores a channel under the edge floor whatever its share; mutual density rejects a one-directional relationship; a channel without a username still scores on the other three

## 4. Ranking

- [x] 4.1 `affiliation/detect.py`: combine signal results per pair — union, not intersection. One signal is enough to propose; the score is `Σ wᵢ · sᵢ` over the signals that fired. A conjunctive rule would propose almost nothing: mutual `about` ∩ shared token is 0 pairs, mutual `about` ∩ mutual edges is 0, concentration ∩ shared token is 6 of 19
- [x] 4.2 Same module: normalise every pair to `(min(a, b), max(a, b))` before merging, so a pair reached by two signals from opposite directions is one candidate
- [x] 4.3 Same module: drop the pairs the spec excludes — a channel with itself, a chat with the parent it is already `linked_to`, two channels already sharing a family key, and any pair already confirmed or rejected. The last two are filtered for *display*; the row itself stays so the decision remains inspectable
- [x] 4.4 Same module: the default parameters, in one place, with the measurement each came from in a comment — `min_out_edges 20`, `max_share_min 0.7`, `min_token_length 4`, `max_token_channels 3`, `min_mutual_edges 5`, weights `about 1.0`, `share 0.8`, `token 0.6`, `mutual 0.5`. State plainly that the weights order four nearly disjoint lists rather than calibrate one measure
- [x] 4.5 Same module: validate parameters before doing any work — a share outside `[0, 1]`, a non-positive minimum, an unknown edge kind. Fail naming the parameter, write nothing
- [x] 4.6 Tests in `test_affiliation_detect.py`: one signal proposes; several signals rank above one; a pair reached from both directions appears once; a `linked_to` chat is never paired with its parent; a settled pair leaves the review list but stays readable; an out-of-range parameter fails before anything is written

## 5. Storing runs and candidates

- [x] 5.1 `db/affiliation.py`: record a run — insert the `AffiliationRun` row with the parameters and the measured coverage, return its id. Everything a candidate needs to be re-read under the thresholds it was computed with hangs off this row
- [x] 5.2 `db/affiliation.py`: upsert candidates. `ON CONFLICT (channel_a, channel_b) DO UPDATE` refreshing `score`, every evidence column and `run_id`, and touching none of `decision`, `origin`, `canonical_id`, `decided_at`, `decision_note`. This is what makes re-running safe: the measurement is refreshed, the decision is not
- [x] 5.3 `db/affiliation.py`: read candidates back — ranked, optionally limited, optionally including the settled ones, joined to `channels` so the output can show username, title and status without a second query
- [x] 5.4 Tests in `test_affiliation_db.py`: a second identical run writes no second row and changes nothing but `run_id`; a run under new thresholds updates the score and leaves a recorded decision intact; the pair check refuses a row inserted the wrong way round

## 6. `itgraph affiliates`

- [x] 6.1 `cli.py`: the `affiliates` command — every threshold and weight from 4.4 as an option, plus `--limit`, `--edge-kind` and a flag to include already-decided pairs. Argument parsing here, logic in `affiliation/`, command body short enough to read at a glance
- [x] 6.2 Same command: load channels, edges and stored descriptions in one pass each and compute in memory. 15 651 edges, 500 usernames and 195 descriptions fit by a wide margin, and 504 channels is 127 000 pairs — so the signals emit pairs rather than the pair space being enumerated and asked about
- [x] 6.3 Same command: print the ranked list — both channels with username, title and status, the score, and one column per signal showing what it found. A ranking whose reasoning is not on screen is not reviewable
- [x] 6.4 Same command: report coverage before the list — how many channels were scored, how many have a stored description, how many references pointed outside the inventory. 302 of 504 seeds have no description, so a short list must not read as a small problem. A signal that could not run anywhere says so, rather than reporting nothing found
- [x] 6.5 Tests in `test_cli.py`: the command makes no Telegram request and needs no session; every option reaches the pass; `--limit` bounds the output to the highest-scoring pairs; **no channel's `operator_id` is written, whatever the scores** — the one thing detection may never do

## 7. `itgraph family`

- [x] 7.1 `db/channels.py`: `confirm_affiliation` — write `operator_id` on the non-canonical side, mark the candidate confirmed with its `canonical_id` and `decided_at`. Creates the candidate row with origin `operator` when none exists, so a pair no signal found is still recordable
- [x] 7.2 Same function: enforce depth one — refuse when the named canonical channel is itself a member of a family. A `CHECK` cannot see another row and a trigger would be the project's first, on its most-written table; this is the only write path, which is what makes application enforcement honest rather than convenient
- [x] 7.3 Same function: refuse a pair whose sides already belong to two different families, naming both. Merging two families is a decision, not a side effect of confirming a pair
- [x] 7.4 `db/channels.py`: `reject_affiliation` — mark the candidate rejected with an optional note, write no `operator_id`. And `withdraw_affiliation` — clear `operator_id` and return the candidate to pending
- [x] 7.5 `db/channels.py`: `recanonicalize_family` — point every member at the new canonical channel and clear that channel's own pointer, in one transaction. No member may be left naming the former canonical
- [x] 7.6 `cli.py`: the `family` command — two channel references accepted as id or username the way `mark` accepts them, `--canonical`, `--reject`, `--withdraw`, `--note`. Reuse `_channel_ref` and the existing not-found and ambiguous-username errors
- [x] 7.7 Tests in `test_affiliation_family.py`: confirming writes the pointer one way only; rejecting writes none; withdrawal clears it; a chain is refused; two families are refused with both named; re-canonicalizing moves every member and leaves nobody naming the old one; a pair with no candidate row is recordable and lands with origin `operator`
- [x] 7.8 Test in the same file: confirming a family leaves every edge between the two channels untouched — the repost happened, and excluding it is analysis, not deletion

## 8. The inventory shows families

- [x] 8.1 `db/channels.py`: the listing gains a family filter, returning every member including the canonical one — `WHERE COALESCE(operator_id, tg_id) = :family`, the same expression the analysis will use
- [x] 8.2 `cli.py`: `itgraph channels --family <ref>` lists one family and marks which member is canonical; the unfiltered summary reports how many families are recorded and how many channels belong to one
- [x] 8.3 Tests in `test_channels.py`: the filter returns the canonical channel too; a channel in no family is its own family of one; the summary counts families rather than members

## 9. Close out

- [x] 9.1 `make validate` green — lint, mypy, pytest. No loosened config, no `# type: ignore` added to make an error disappear
- [x] 9.2 `README.md`: the new commands, their options and defaults, and where they sit in the workflow — after `derive`, before the role metrics. Say that `itgraph metadata` coverage bounds the description signal, and that 302 of 504 seeds currently have no description
- [x] 9.3 `src/itgraph/CLAUDE.md`: rows for `affiliation/signals.py`, `affiliation/detect.py` and `db/affiliation.py`; `db/channels.py` owns the family link now
- [x] 9.4 `openspec validate detect-affiliated-channels --strict` green
- [x] 9.5 Take a full dump before upgrading the working database, per the backup rule, then `alembic upgrade head` against it
- [x] 9.6 Run `itgraph affiliates` against the real inventory and reconcile the counts with the measurements this change was designed on. **Done: 217 candidates over 2093 channels, every gap accounted for, none of them a bug.**

  | signal | designed on | run | why |
  |---|---|---|---|
  | mutual description | 1 | 1 | — |
  | one-way description | 36 pairs (37 refs) | 22 | 13 discussion-chat pairs excluded |
  | concentration | 19 | 17 | 2 discussion-chat pairs excluded |
  | mutual density ≥ 5 | 45 | 45 | — |
  | shared token, cap 3 | 44 | 23 seed-seed | wider denominator, see below |

  Two of the three gaps have one cause: **the probe scripts had no way to exclude a channel and its own discussion chat.** Computed without that exclusion the signals reproduce the design numbers exactly — 36 description pairs and 19 concentration candidates — and the exclusion then removes 13 and 2 respectively. A channel describing its own chat, or forwarding almost exclusively into it, is a relationship already recorded in `linked_to`; proposing it as an affiliation would be a row the operator dismisses once per run forever. The probe over-counted; the command is right.

  The token gap is the one worth keeping. The probe counted a token's document frequency **over seeds only** (44 pairs); the command counts it over the whole inventory of 2093 channels, which suppresses 21 seed-seed pairs whose shared token is `blog`, `chat`, `devs`, `education`, `learning`, `memes`, `moscow`, `python`, `secrets` and a handful of company names. Those are subjects, not authors — exactly what the cap exists to catch, and it only catches them when the denominator is the whole inventory. The wider denominator is the better measurement, not a stricter one.

- [x] 9.7 Drop the scratch database used to verify the revision

## 10. A tie between two shared tokens was not deterministic

Found by `make validate` failing once and passing on re-run — the only symptom it ever had.

- [x] 10.1 `affiliation/signals.py`: break a tie between two equally rare shared tokens on the longer token, then alphabetically. `_tokens` returns a **set**, and set iteration order for strings moves with `PYTHONHASHSEED`, so two channels named `fake_gonzo_main` and `fake_gonzo_pod` — sharing `fake` and `gonzo` at identical rarity — reported a different token from run to run. The spec requires a reproducible ranking, and the stored evidence is what the operator reviews the pair on, so "which token" is not cosmetic
- [x] 10.2 Tests in `test_affiliation_signals.py`: the tie resolves to the same token every run, and independently of the order the usernames arrive in
- [x] 10.3 `make validate` green under several fixed `PYTHONHASHSEED` values, not just the default
