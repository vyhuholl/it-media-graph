## Context

See `proposal.md` — Why, for the motivation and the measurements. What matters here is the constraint they impose: the evidence this signal needs is a handle like `@1red2black`, and the project's existing text reader refuses it on purpose. `_TEXT_MENTION` is `@([A-Za-z][A-Za-z0-9_]{3,31})` and `normalize_username` is `^[a-z][a-z0-9_]{3,31}$`, both in `derive/references.py`, because everything crossing them feeds a *lookup* — a `pending_mentions` key, a row in the resolve queue, a `get_entity` call that spends quota. A handle beginning with a digit is not a Telegram username and must never reach any of them.

So the design's central question is not what the signal computes — that part is four lines of set intersection — but where a second, looser reading of the same text is allowed to live without leaking into the first.

Everything else is already in place. `Inventory` carries `usernames` and `descriptions`; `detect()` merges independent signals into one candidate per pair; `affiliation_candidates` holds one nullable column group per signal and refreshes evidence without touching decisions. This change adds a fifth signal to that machinery rather than changing it.

## Goals / Non-Goals

**Goals**

- Read an author's own handle as evidence, including one that could never be a Telegram username, without any parser that resolves channels learning to accept it.
- Keep the new signal's noise floor where the measurement put it: 9 groups, 13 reviewable pairs, no subject word.
- Present a family of five channels as one review instead of nine, without giving a group a decision of its own.
- Keep the whole thing offline and re-runnable, like every other signal.

**Non-Goals**

- Changing `normalize_username`, `extract_text_references`, or anything downstream of them.
- Reading handles out of post text. Descriptions are a 528-row corpus a human has effectively vetted; `raw_messages` is not, and it is a different signal with a different noise profile.
- Following a non-Telegram link. `youtube.com/@1red2black` contributes its `@handle` substring and nothing else — no request, no cross-platform identity.
- Any change to how a decision is recorded. Confirmation stays pairwise.

## Decisions

### 1. The loose handle pattern lives in the signal, not in `derive/references.py`

`affiliation/signals.py` gets its own private pattern, `@([A-Za-z0-9][A-Za-z0-9_]{3,31})`, and applies it to `Inventory.descriptions` directly. `derive/references.py` is not touched.

The justification is not "two readers are fine" — it is that the two readers answer different questions and have different failure modes:

| | `extract_text_references` | this signal |
|---|---|---|
| asks | is this a channel I can look up? | is this string a token my inventory already carries? |
| a false positive costs | a resolve attempt, a `pending_mentions` row, quota | nothing — the join finds no token |
| bounded by | the regex alone | a closed set of 2 170 tokens |

**The pattern can afford to be sloppy because the join is against a closed set.** A match that is not a username token of at least two channels forms no candidate and is never stored, looked up, or reported. That is what makes loosening safe here and unsafe there, and it is why the loosening cannot be expressed as a flag on the shared parser.

*Alternative considered — a `strict=False` parameter on `extract_text_references`.* Rejected: it puts the dangerous reading one keyword away from every existing call site, and the signal does not want `Reference` objects anyway. The `t.me/c/<id>` form carries no username and is meaningless here.

*Alternative considered — relaxing `normalize_username` and filtering downstream.* Rejected outright. It would let `1red2black` become a `pending_mentions` key and a resolve attempt that can never succeed, spending the one budget this project protects.

### 2. The naming channel must carry the token

A token `t` is a *named handle* when some channel `c` satisfies both `t ∈ tokens(username(c))` and `t ∈ handles(about(c))`. The signal then proposes every pair among the channels carrying `t`.

The second half of that conjunction is the whole precision of the signal, and it is measured rather than assumed. Dropping it — accepting a handle named in *any* description — admits `@yandex`, carried by 13 usernames and named by a channel that is not one of them, and turns 27 pairs into 105. With it, the result is 9 groups and no subject word in the d=2–10 range.

The reading is "an author signing their own work". A third party naming a brand is a different fact, and on this corpus it is the noisy one.

Matching is on lowercased text both sides, agreeing with `_tokens` and with the stored username's `.lower()` in `load_inventory`.

### 3. A signed token is read once, by this signal only

When a token is both a named handle and rare enough to clear `max_token_channels`, only the handle signal contributes. The rarity signal skips it.

Both signals otherwise read *the same observation* — these usernames share this token — and summing them would count one fact twice, ranking a pair with a signed token above a pair with a token plus an independent edge signal. The score orders a reading list, and that ordering would be wrong.

Measured cost of the rule on today's data: **zero shown pairs change.** Seven of the nine groups sit at d ≤ 3 where both could fire (`eleday`, `habr`, `blognot`, `nikitonsky`, `selectel`, `techtrainfest`, `veai`), and every one of their pairs is already filtered out as an existing family or as a chat with its parent. The two groups that produce anything, `1red2black` at d=5 and `atom` at d=4, are over the rarity cap and were never going to fire twice. So this is settled on principle while it is free, rather than discovered later when it is not.

### 4. Flat strength, weight 1.0

Strength is `1.0` for every pair in a group, independent of `d`. The handle is the claim; five channels signing one handle are not weaker evidence than two, and the rarity signal's `(M + 1 − d) / M` — which collapses to `1/M` exactly at the cap — is the shape this change exists to route around.

`Weights.handle` defaults to `1.0`, which places a handle group level with a mutual description reference (`about` at `1.0 × 1.0`) and above everything else the corpus produces: the strongest concentration reaches `0.8`, the strongest mutual density `0.5`, a one-way description `0.5`. That ordering matches the measured precision — seven of nine groups reproduce families the operator already confirmed by hand — and, as with every other weight, it decides a reading order rather than calibrating a probability.

A flat strength means handle pairs do not order *among themselves*. That is deliberate: they are read as groups, and a group's position in the list is that of its highest-scoring pair, ties broken on the pair itself. Nine groups is a screen, not a ranking problem.

### 5. Two nullable columns, and grouping is a `GROUP BY` at read time

```
affiliation_candidates            affiliation_runs
  handle_token          text NULL   max_handle_token_channels int NULL
  handle_token_channels int  NULL   weight_handle             double precision NULL
```

Every pair in a group repeats the same handle and the same count. Redundant, and exactly what `shared_token` / `shared_token_channels` already do — which is the point: grouping the review list is then `GROUP BY handle_token` over rows the reader already has, with no join and no second source of truth for who is in the group.

*Alternative considered — a `handle_groups` table with a membership row per channel.* Rejected. A group has no attributes beyond the handle and its size, both derivable; decisions are per-pair, so the table would carry no decision column; and membership stored separately can disagree with the pairs, which is the failure the `channel_families` view was built to make impossible.

The run's two columns are **nullable rather than defaulted**. A run recorded before this change genuinely had no such parameter, and a server default would claim it ran under one it never saw.

`upsert_candidates` adds both evidence columns to its `set_`, nulls included, under the existing rule: a signal that no longer fires must stop claiming it did.

**When two signed handles fire on one pair**, the stronger claim wins by the same total order `_beats` already uses for the rarity signal — longer token, then alphabetical. Arbitrary as a claim, essential as an ordering: `_tokens` returns a set, and its iteration order for strings moves with the interpreter's hash seed, so without this the same input reports a different handle from run to run.

### 6. Grouping is rendered, not stored

`list_candidates` keeps returning ranked pairs and `--limit` keeps bounding pairs, so the existing scenario "at most that many candidates are shown" stays literally true. `cli.py` collects rows by `handle_token` before printing and emits each group as a block headed by the handle, its channel count and its pair count; ungrouped candidates print exactly as today. A block truncated by `--limit` states how many of its pairs are not shown.

Each block also prints the `itgraph family …` line that would confirm it, with the channels already filled in. Nine pairs is one decision, and retyping five usernames is where that decision gets made wrong.

`_evidence` gains one part, `handle:<token>/<count>`, in the shape the other four already use.

### 7. The signal stays pure

It takes `usernames` and `descriptions` — both already on `Inventory` — and returns `HandleSignal` values. `load_inventory` is unchanged, no query is added, and the signal is testable on a synthetic mapping with no database, like the other four.

## Risks / Trade-offs

- **A handle that is also a common word would propose a group of strangers.** A channel named `code_daily` whose bio reads "@code" would drag in every `*_code_*` channel — `code` carries 4 today, so 6 false pairs. → Does not occur on the current corpus, and the blast radius is bounded twice: by `min_token_length`, and by `max_handle_token_channels`. The handle is stored and printed with every pair, so a bad group is visible at a glance rather than inferred from a score.
- **The signal cannot tell a personal brand from a corporate umbrella.** `atom` is four Rosatom channels, not one person. → For the metric this exists to protect — how many *distinct* sources a channel reposts — one operator reposting itself is the same defect either way. It is a proposal; the operator decides, and a rejection is recorded so it is not asked twice.
- **`max_handle_token_channels = 10` binds nothing today.** The largest group is 5. → That is the intent: it is a combinatorial guard, not a threshold to tune. A brand suffix landing on 40 channels would otherwise be 780 pairs in one block.
- **Coverage bounds the signal exactly as it bounds the description signal.** 528 of 3 547 channels have a description, so silence is mostly ignorance. → Already covered by the existing coverage requirement and reported by `DetectionSummary`; the wording of the "run `itgraph metadata` first" line needs to stop naming one signal.
- **Two readings of the same text can drift apart.** A future change to `_TEXT_MENTION` will not reach the signal's private pattern. → Intended, and the reason for Decision 1; the divergence is documented at both sites so neither reads as an oversight.
- **A confirmed family that is not one silently removes real cross-author edges from the metrics.** Unchanged by this change, and worth restating because it now arrives via a signal that fires on whole groups: five channels confirmed at once is five channels' worth of edges reclassified. → Confirmation stays manual, per-pair, withdrawable, with its evidence stored.

## Migration Plan

One additive Alembic revision: two nullable columns on `affiliation_candidates`, two on `affiliation_runs`. Nothing existing changes shape or nullability, no data migration, no backfill — the first run after the upgrade populates the new evidence for the pairs it proposes, and older runs keep their two NULLs as an accurate statement that they had no such parameter.

Order, per the project's rules: a full dump first (an `alembic upgrade` on the working database already takes one), the revision verified up and down on a scratch database whose name ends in `_test`, then the working database.

Rollback is the downgrade: drop four columns. It destroys the handle evidence on existing candidates and no decision — `decision`, `origin`, `decided_at` and `decision_note` are untouched by this change — so a rollback costs one re-run of `itgraph affiliates`.

## Open Questions

1. **Whether a handle named in a post should count, not only in a description.** `raw_messages` is a corpus three orders of magnitude larger, and an author's pinned "my other channels" post is the same signature. Deferrable: it changes no requirement here, adds no column, and its noise profile has to be measured before it can be weighted.
2. **Whether the group block should offer to confirm interactively rather than printing the command.** Ergonomics only; the recorded decision is identical either way.
