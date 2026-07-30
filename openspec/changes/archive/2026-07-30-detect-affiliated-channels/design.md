## Context

The role metrics rank channels by how many *different* sources they repost. An author running several channels and reposting between them scores as a connector while talking to nobody but themselves. Nothing in the inventory records that two channels share an author, so nothing downstream can subtract them.

Everything needed to guess at it is already collected: 504 seeds, 15 651 edges, and 202 stored `GetFullChannelRequest` payloads. No network request is involved anywhere in this change, which is what makes it cheap to iterate on — a threshold can be re-tried as often as the operator likes.

Two measurements over that data shape the design more than anything in the original sketch.

**The descriptions speak in `@mentions`, not links.** Across 195 non-empty descriptions there are 155 `@mention`s and only 34 `t.me`-shaped substrings, of which 22 parse to a channel (the rest are invite links and service paths, correctly refused). So `parse_tme_link` is the smaller half of the job and `normalize_username` is the larger one. Stripping trailing punctuation off a link before parsing recovers exactly 0 additional references today — worth doing defensively, not worth designing around.

**The signals do not corroborate each other.** Mutual `about` ∩ shared token = 0 pairs. Mutual `about` ∩ mutual edges = 0. Concentration ∩ shared token = 6 of 19. The combined score is therefore not a consensus measure; it is mostly an interleaving of four nearly disjoint lists, and the weights decide the merge order. That is worth saying plainly, because it is the reason the design cannot use a conjunctive rule and the reason the weights have to be tunable.

## Goals / Non-Goals

**Goals**

- Record which channels share an author, as a reviewed fact, without ever guessing it into the database.
- Rank pair proposals by evidence, and keep the evidence readable after the fact.
- Make every threshold and weight a parameter, so tuning is a command line rather than an edit.
- Give analysis a single cheap expression for "which family is this channel in".

**Non-Goals**

- Subtracting affiliated edges from the metrics. That is the notebook work this unblocks.
- Automatic merging at any confidence. No threshold on this data separates a second channel from a close collaborator.
- Discovering channels named in a description but absent from the inventory. 134 of the 171 handles parsed out of descriptions point outside it — a real discovery lead, and a different change.
- Transitive closure. If A is confirmed with B and B with C, the operator confirms A with C or does not.

## Decisions

### 1. `channels.operator_id`, and the family key that falls out of it

```
channels
  ...
  operator_id  bigint NULL  FK → channels.tg_id  ON DELETE SET NULL
               CHECK (operator_id IS NULL OR operator_id <> tg_id)
```

Non-canonical members name the canonical channel; the canonical channel names nobody. This is the shape `linked_to` already uses, and it makes the family of any channel a single expression:

```sql
COALESCE(operator_id, tg_id)   -- the family key
```

which is correct for a member, for a canonical channel, and for a channel with no family at all — a solo channel is its own family of one, so the analysis rule "drop edges whose endpoints share a family key" excludes nothing for it without needing a special case. An edge is intra-family exactly when the two keys are equal.

*Alternative considered — the canonical channel points at itself.* Then the family key is just `operator_id` and no `COALESCE` is needed, but `operator_id IS NULL` stops meaning "no family recorded" and every insert path has to decide whether to self-populate the column. Rejected: the `COALESCE` is written once in a view or a helper, and the null-means-nothing invariant is worth more.

*Alternative considered — a separate `families` table with a membership row per channel.* Correct, and one join heavier on every analytical query for a fact that is at most one value per channel. Rejected as premature; a family has no attributes of its own.

**Depth is exactly one, and it is enforced in application code.** `operator_id` must name a channel whose own `operator_id` is null, or the family key expression above needs transitive closure to be right. A `CHECK` cannot see another row, so the options are a trigger — which would be the project's first, on its most-written table — or the single function that writes the column. The write path is one function by construction (detection never writes it; only confirmation does), so that function enforces it and a test pins it. *Residual risk is stated below:* a hand-written `UPDATE` can still create a chain, and the family key would then be quietly wrong rather than loudly broken.

### 2. Two tables: one per run, one per pair

```
affiliation_runs                       affiliation_candidates
  id                bigserial PK         channel_a        bigint ─┬─ PK, FK → channels
  ran_at            timestamptz          channel_b        bigint ─┘  CHECK (channel_a < channel_b)
  -- parameters, one column each        score            double precision
  min_out_edges     int                  run_id           bigint FK → affiliation_runs
  max_share_min     double precision     -- evidence, one nullable column per signal
  min_token_length  int                  about_direction  enum(a_to_b, b_to_a, mutual) NULL
  max_token_channels int                 shared_token     text NULL
  min_mutual_edges  int                  shared_token_channels int NULL
  edge_kinds        text[]               out_share        double precision NULL
  weight_about      double precision     out_share_edges  int NULL
  weight_token      double precision     out_share_src    bigint NULL
  weight_share      double precision     edges_a_to_b     int NULL
  weight_mutual     double precision     edges_b_to_a     int NULL
  -- coverage                            -- decision
  channels_scored   int                  decision      enum(pending, confirmed, rejected)
  with_description  int                  origin        enum(signal, operator)
  refs_outside_inventory int             canonical_id  bigint NULL FK → channels
                                         decided_at    timestamptz NULL
                                         decision_note text NULL
```

**The pair is stored once, unordered, by `CHECK (channel_a < channel_b)`.** Ordering by id rather than by which signal fired first is what makes "a pair is proposed once" true regardless of evaluation order, and makes the primary key do the deduplication for free.

**Evidence gets real columns, not a JSONB blob.** The project uses JSONB for the raw layer and explicit columns for everything derived — `edges` is the precedent. The operator reads this table by hand and from a notebook, where nine typed columns beat one document. A fifth signal will cost a migration, which is the same trade `EdgeKind` already makes and for the same reason: a new signal is a deliberate change, and it can afford one.

**Parameters live on the run, not on the pair.** The spec requires that a proposal be readable back together with the thresholds it was computed under. Copying eleven parameters onto each of a hundred candidates is the alternative; a `run_id` is one column. Re-running updates a candidate's score, evidence and `run_id` in place — the pair is unique on `(channel_a, channel_b)`, so `ON CONFLICT DO UPDATE` refreshes the measurement while leaving `decision`, `origin`, `canonical_id`, `decided_at` and `decision_note` alone. That is exactly the "evidence is refreshed, decisions are not" scenario.

**`canonical_id` is on the candidate, not only on `channels`.** It records what the operator decided at the moment they decided it. Re-canonicalizing a family later rewrites `channels.operator_id` for every member; without this column, which channel was originally judged the main one is unrecoverable.

### 3. Signal strengths are normalised to `[0, 1]`, then weighted and summed

Each signal answers with a strength, the score is `Σ wᵢ · sᵢ`, and signals that did not fire contribute nothing:

| signal | strength |
|---|---|
| description reference | `0.5` one-way, `1.0` both ways |
| shared username token | `(M + 1 − d) / M`, where `d` is the number of channels carrying the token and `M` the configured cap |
| outgoing concentration | the observed share itself, already in `[T, 1]` |
| mutual density | `min(1, min(n_ab, n_ba) / (2 · K))`, `K` the configured minimum each way |

The token formula is the one that carries the design's main correction: `d` is the token's document frequency across the inventory, so a token on two channels scores near the top of its range and a token on `M` channels scores near the bottom. Rescaling concentration from the threshold instead of using the raw share was rejected because a pair firing exactly at the threshold would then contribute exactly zero and drop out of the ranking it just entered.

Defaults, taken from the measurements rather than from intuition: `min_out_edges 20`, `max_share_min 0.7`, `min_token_length 4`, `max_token_channels 3`, `min_mutual_edges 5`. Weights `about 1.0`, `share 0.8`, `token 0.6`, `mutual 0.5`. Since the signals barely intersect, these weights mostly decide which list is read first — they are a starting point for tuning, not a calibration.

### 4. Candidate formation is driven by the signals, never by the pair space

504 channels is 127 000 pairs; the signals are sparse, so each one *emits* pairs instead of being asked about all of them: an inverted index from token to channels, a grouped scan of `edges` for concentration and mutuality, a pass over parsed descriptions. Everything fits in memory by a wide margin — 15 651 edges, 500 usernames, 195 descriptions — so the computation is plain Python over rows fetched once, not SQL. That keeps every signal unit-testable on a synthetic inventory with no database round-trip, which is what the spec's scenarios need.

### 5. Both edge kinds count, and which kinds count is a parameter

Measured separately at the default thresholds, concentration finds 10 candidates over forwards alone, 13 over mentions alone, and 19 over both. Neither kind is the signal by itself. Pooling is the default and `--edge-kind` narrows it, because a self-promoting mention and a self-repost are different behaviours and the operator may want to look at them apart.

### 6. Scope is the whole inventory, not just seeds

10 of the 19 concentration candidates point at a channel with status `candidate` and 2 at a `rejected` one. Filtering to seeds would drop most of the signal for no gain — a family link on a non-seed is still true, costs nothing, and matters the moment that channel is accepted. Status is shown next to each candidate so the operator can skip what they do not care about. No status parameter is added; there is nothing to tune.

### 7. Reading a description: a plain-text scan, reusing the per-link parser

`extract_references` reads Telegram entity offsets and cannot be reused — `ChannelFull.about` is a plain string with no entities. A new pure function in `derive/references.py` scans text for `t.me`-shaped substrings and `@mentions` and hands each hit to the existing `parse_tme_link` / `normalize_username`. Trailing punctuation is stripped before parsing, which recovers nothing on today's data and costs one `rstrip`.

An `@mention` in a description often names the author's personal account rather than a channel. Nothing special is done about it: a handle that matches no inventory row forms no candidate, and the inventory-membership check already filters it out. It is counted into `refs_outside_inventory` and reported.

### 8. Two commands, and why not one

- `itgraph affiliates` — run detection, refresh candidates, print the ranked list. Takes every threshold and weight, plus `--limit`.
- `itgraph family` — record a decision: confirm a pair naming the canonical side, reject one, or withdraw a confirmation.

Detection and decision are separate commands because they are separated in the spec: one may never write `operator_id` and the other exists only to. *Alternative considered — `itgraph affiliate` as the decision verb.* Rejected on ergonomics: `affiliate` and `affiliates` differ by one character and one of them writes to the inventory.

New code lives in `src/itgraph/affiliation/` — outside `tg/`, because it touches no network, and outside `derive/`, because it does not read the raw message layer.

## Risks / Trade-offs

- **A confirmed family that is not one silently removes real cross-author edges from the metrics.** → Confirmation is manual, the evidence behind it is stored, and withdrawal is supported. Families will number in the tens, which is small enough to re-read.
- **The description signal speaks about 40% of seeds.** 302 of 504 have no stored description, so its silence is mostly ignorance. → The run reports the coverage denominator, and the spec forbids reporting a signal that could not run as a signal that found nothing. Running `itgraph metadata` over the remainder is the fix, and it is out of scope here.
- **Depth-one is enforced in application code, not by the database.** A hand-written `UPDATE` can build a chain, after which `COALESCE(operator_id, tg_id)` returns a wrong family rather than an error. → One write path, a test on it, and the family summary in `itgraph channels` makes a malformed family visible. A trigger stays available if this ever bites.
- **Tuning changes the candidate set between runs.** → Decisions are keyed by pair, not by run, so they survive; `run_id` records what each measurement was taken under.
- **The token signal is the noisiest, and its cap is doing all the work.** At `max_token_channels 3` it yields 44 pairs, at no cap 216. → The cap is a parameter with a deliberately tight default; the shared token is stored with the candidate so a bad cap is visible at a glance rather than inferred from the count.
- **`ON DELETE SET NULL` on `operator_id` would silently dissolve a family** if a channel were ever deleted. → Inventory records are never deleted, by an existing spec requirement, so the clause is unreachable; it matches `linked_to` rather than inventing a second convention.

## Migration Plan

One additive Alembic revision: the `operator_id` column with its check and foreign key, and the two new tables. Nothing existing changes shape, so there is no data migration and no backfill — the first `itgraph affiliates` run populates the candidates from data already in the database.

Order, following the project's rules: take a full dump first (an `alembic upgrade` on the working database already does), verify the revision up and down on a scratch database whose name ends in `_test`, then upgrade the working one.

Rollback is the downgrade: drop the two tables and the column. It destroys every confirmation and rejection recorded up to that point, and nothing else — those are hand-made decisions that exist nowhere else, which is what the pre-upgrade dump is for.

## Open Questions

1. ~~**Command naming.**~~ **Settled: `itgraph affiliates` and `itgraph family`.** `mark` stays the status verb; a family is a different fact about a channel and gets its own command.
2. **Should mutual density require the two channels to be within some volume ratio of each other?** Two large peers reposting each other heavily look identical to an author's two channels on this signal alone. A size ratio would separate them, but `participants_count` is only known for the 202 channels with a stored description — the same coverage problem, on the signal that currently has none.
3. **Whether the mention half of concentration should count a `t.me` link differently from an `@mention`.** They are the same edge kind today and the raw layer can tell them apart, so this is recoverable later rather than a decision that has to be made now.
