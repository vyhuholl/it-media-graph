## Why

One shape of family is invisible to all four affiliation signals at once: an author's main channel with satellites around it, sharing a handle in their usernames. `itgraph affiliates` has never proposed such a group, and the reason is not a threshold set slightly too tight — it is four independent misses that happen to coincide.

The worked case, measured on the current database. Five channels — `tg_1red2black`, `braindump_1red2black`, `logs_1red2black`, `filebin_1red2black` (all seeds) and the discussion chat `chat_1red2black` — one author, and no candidate pair among them in any run:

| signal | why it is silent |
|---|---|
| shared username token | `1red2black` is carried by **5** usernames against `max_token_channels = 3`, so the token is discarded whole |
| description reference | only `tg_1red2black` has an `about` at all; the other three hold `''` though the metadata pass read them. It names `@chat_1red2black` — correctly suppressed as an already-recorded `linked_to` — and `@1red2black`, which the project's own parser never sees |
| outgoing concentration | `min_out_edges = 20` is cleared only by `tg_1red2black` (48 out-edges; the others have 6, 4, 0, 0), and its best share, `tg → filebin` at 22/48 = **0.46**, is under the 0.70 floor |
| mutual density | `tg → filebin` is 22 edges, `filebin → tg` is **1**; `tg → braindump` is 5, back **0** |

The last two are not miscalibration. A hub with satellites *is* one-directional by construction, and the satellites are too quiet to have a denominator — which is precisely the family shape the signals cannot see. It also costs the metric the affiliation pass exists to protect: 22 of `tg_1red2black`'s 48 outgoing edges point at the author's own file dump, and today they count as variety.

**Raising `max_token_channels` is not the fix.** Over 2 057 usernames the token frequencies are 1 952 at d=1, 145 at d=2, 30 at d=3, 19 at d=4, 7 at d=5, 3 at d=6, 4 at d=7. The d=4–7 band holds 33 tokens, and it mixes subjects (`code`, `math`, `python`, `career`, `learning`, `science`, `memes`, `events`, `talks`, `team`, `club`, `devs`, `live`, `neural`) with genuine families (`nazarov`, `live4dev`, `neurogen`, `ozon`, `atom`, `1red2black`). Frequency alone does not separate them, and the candidate list pays for the attempt: 186 pairs at cap 3, **349** at 5, 474 at 8, 599 at 10. Worse, the pairs sought arrive last — strength is `(M + 1 − d) / M`, which at `d = M` is `1/M`, so at cap 5 the nine `1red2black` pairs score **0.12, the bottom of 349**. The parameter is anti-correlated with what it is being raised to find.

**What does separate them is already on disk: whether a member's own description names that token as a handle.** `tg_1red2black`'s `about` reads "Блоггер @1red2black". Measured over the 528 stored descriptions, the rule "a username token that a channel carrying it names as `@handle` in its own description" fires on **9 groups and nothing else** — no subject word in the d=2–10 range qualifies. Seven of the nine are families the operator already confirmed or already linked by hand (`eleday`, `habr`, `selectel`, `blognot`, `nikitonsky`, `veai`, `techtrainfest`): the signal agrees with decisions already made rather than contradicting them. The two that add anything are `1red2black` and `atom`. Net cost after the existing filters — 27 pairs in the groups, 8 dropped as already one family, 6 as a chat with its parent — is **13 pairs for review, none of them already proposed by another signal**, against 186 today.

The reading is cheap because the evidence is a fact about the corpus rather than a ratio: 403 handles in stored descriptions currently name no channel the inventory holds and are counted and dropped. This change reads a few of them as what they are — an author signing their own work.

Why now: 47 families over 131 channels have been confirmed by hand, 124 pairs confirmed against 116 rejected, and 79 still pending. The manual review is working; what it is not being shown is a whole category.

## What Changes

- **A fifth signal: the named handle token.** A username token carried by two or more channels, where at least one of those channels names it as a handle in its own stored description, proposes every pair among them. It reads the inventory's usernames and the descriptions the metadata pass already stored, makes no network request, and adds no parsing pass of its own.

- **Its strength does not decay with the size of the group.** Unlike the rarity signal it replaces for this case, the handle *is* the claim, and five channels signing one handle are not weaker evidence than two. A cap (`max_handle_token_channels`, default 10) exists only as a combinatorial guard on d(d−1)/2, not as a statement about credibility, and it is a parameter like every other threshold.

- **The existing shared-token signal is untouched**, cap included. The new signal is additive: nothing that is proposed today stops being proposed, and no subject word becomes evidence.

- **Handle recognition inside this signal does not require a leading letter.** `normalize_username` enforces `^[a-z][a-z0-9_]{3,31}$` because everything crossing it feeds a *lookup*, and `@1red2black` is not a resolvable Telegram username. This signal never resolves anything — it matches text against tokens the inventory already carries — so the rule that protects a lookup would here discard the strongest case in the corpus. Measured: the strict rule finds 8 groups and misses `1red2black` entirely; allowing a leading digit adds that one group and no others. `normalize_username` itself does not change, and no other caller's behaviour moves.

- **Candidates sharing a handle are presented as one group.** The `1red2black` family is nine pending pairs and a single decision; `itgraph family` already takes any number of channels. Storage stays pairwise — a rejection is a statement about a pair, and families remain the transitive closure of confirmed pairs — but the review list shows the group as a block rather than as nine lines to work through separately.

- **The evidence is stored like every other signal's**: which handle fired, how many channels carry it, and the run's new threshold and weight, so a proposal stays checkable without re-running detection.

- **Coverage is reported.** The new signal shares the description denominator with the reference signal — 528 of 3 547 channels — and a run that found little because it could read little must say so.

Out of scope, deliberately:

- **The `(M + 1 − d) / M` strength formula, and the value of `max_token_channels`.** The formula's collapse at the cap is real and is the reason raising the cap does not work, but the new signal does not use it and nothing here depends on fixing it. Re-calibrating the rarity signal against a corpus that has grown from 504 seeds to 566 seeds and 2 057 usernames is its own change, with its own measurements.
- **Handles from other platforms.** `tg_1red2black`'s description also carries `youtube.com/@1red2black`, and cross-platform identity is a phase of its own in `docs/PLAN.md`. The `@handle` form is read wherever it appears in the text, including inside a URL, but no non-Telegram link is followed or resolved.
- **Confirming anything automatically.** A group with a signed handle is still a proposal. Nothing here writes a family link.
- **Backfilling descriptions.** 3 019 channels have none, which bounds this signal as it bounds the reference signal. That is a quota-bearing metadata pass, not a code change.

## Capabilities

### New Capabilities

None. This adds a signal to an existing capability and changes how its candidates are presented.

### Modified Capabilities

- `channel-affiliation`: two new requirements — **Named Handle Token Signal**, and **Handle Groups Are Reviewed Together** for the grouped review; **Shared Username Token Signal** gains the boundary that its cap governs only the rarity reading of a token, so the two requirements cannot be read as contradicting each other; **Thresholds And Weights Are Parameters** gains the new cap.

  **Signal Coverage Is Reported** is deliberately left alone: its scenario for a signal that could not run for lack of data is already general, and the new signal falls under it without a word changing.

## Impact

- `src/itgraph/affiliation/signals.py` — the signal, its own handle pattern, and the `HandleSignal` evidence type
- `src/itgraph/affiliation/detect.py` — merging it into the candidate list; the new threshold and weight, and their validation
- `src/itgraph/affiliation/run.py` — the summary line and the coverage lines
- `src/itgraph/db/models.py` + one Alembic revision — two evidence columns on `affiliation_candidates`, the threshold and weight on `affiliation_runs`. Additive and nullable; no existing column changes
- `src/itgraph/db/affiliation.py` — storing and reading the new evidence
- `src/itgraph/cli.py` — `--max-handle-token-channels`, `--weight-handle`, and the grouped output
- Tests: the signal over synthetic inventories, the digit-leading handle, a subject word named as a handle by a non-member producing nothing, group presentation, and that a re-run leaves decisions untouched
- `src/itgraph/README.md`, `README.md` — the fifth signal and what it is for

No change to collection, to the raw layer, or to any pass that spends a request. Every number above came from data already on disk, and the change is re-runnable against it.
