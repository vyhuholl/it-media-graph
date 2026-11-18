# Design

## Context

Telegram's affiliation detection (`src/itgraph/affiliation/`, tables
`tg_affiliation_runs`/`tg_affiliation_candidates`, view
`tg_channel_families`) proposes pairs from five signals; the operator
decides through `itgraph family`. YouTube stores what the API said only in
`yt_raw_*` payloads — `yt_channels` holds the review and nothing else, and
references between channels are derived on demand, never stored
(`youtube-mention-graph`).

Every signal decision below was measured against the live inventory
(887 seeds, 1127 payloads, 795 descriptions, 64 769 stored videos) on
2026-11-18, the way the Telegram signals were measured before being
implemented.

## Goals / Non-Goals

**Goals:**

- The same semi-automatic loop on YouTube: propose with evidence, decide by
  hand, families as the transitive closure of confirmed pairs.
- Organisation families and franchises (Google's product channels, PyLadies
  chapters) recognised as families, per the operator's decision — on
  YouTube a token on many channels can still be a family, which is the
  opposite reading from Telegram's rarity cap.

**Non-Goals:**

- No change to Telegram detection or families (operator's decision:
  YouTube only).
- No reading of the video reference graph: no concentration or mutual
  density signals, so `youtube-mention-graph`'s "no measure over the graph"
  stays untouched. Affiliation reads channel payloads only.
- No token search over `customUrl` (operator's decision: handles are fused
  words — `@microsoftazure`, `@brocodez` — and yield nothing).
- No bot integration, no alerting, no scheduled runs.

## Decisions

### D1. Three signals, two of them Telegram's, one new

| Signal | Source | Measured on the corpus |
|---|---|---|
| Shared title tokens (rarity-weighted) | `payload.snippet.title` | 198 pairs at cap 3, 352 at cap 5; top tokens are subjects (`pyladies` 45, `code` 14, `data` 14) |
| Description references | `payload.snippet.description` | 26 references resolve into the inventory (17 bare `@`, 9 `@handle` links, 9 `/channel/` id links, overlapping); ~37 more name channels outside it |
| Corroborated token groups | description reference + shared title token | 11 tokens corroborated: `google` 28, `jetbrains` 9, `david` 8, `bombal` 6, `unity` 3, … — families and one name-noise block |

Outgoing concentration and mutual density are **not ported**: the reference
graph is too sparse to support them. No seed reaches even 10 distinct
reference targets (Telegram's floor is 20; at a floor of 10 the corpus
yields zero sources), and only 24 pairs have a reference each way at all,
where Telegram's threshold is 5 edges each way. Porting them would add two
dead parameters and two always-empty coverage lines.

### D2. The corroborated token group is the named-handle analog

Telegram's named-handle signal reads an author signing their work
(`Блоггер @1red2black`); its precision comes from requiring the signer to
carry the token. YouTube descriptions rarely sign that way, but they do
link siblings (`Main channel: youtube.com/@googledevelopers`). The rule:

- A description reference (any form the reference signal reads) resolves to
  another inventory channel, and both endpoints' titles share a token
  → every carrier of that token is proposed as one group, flat strength
  1.0, whatever the carrier count.

Rarity deliberately does not apply — an organisation's 28 channels are not
weaker evidence than an author's 2. The carrier-count cap (default 50,
binding nothing today: `google` 28 and `pyladies` 45 both fit) exists only
to bound the pairs one token can produce, exactly as Telegram's
`max_handle_token_channels` does. Franchise brands count as families per
the operator's decision, which is why the default admits 45.

Corroborated tokens are withheld from the rarity signal (one observation,
one count), mirroring `detect.py`'s `signed` exclusion.

Known noise: `david` — 8 carriers, ~6 of them David Bombal's, corroborated
by their cross-links. The cost is one block and one `--reject-token david`
(see D5); the alternative, requiring corroboration *and* rarity, would
also drop `google`.

### D3. Storage mirrors Telegram, with UC-ids and no copied text

`yt_affiliation_runs` (parameters, coverage) and `yt_affiliation_candidates`
(one row per pair: score, per-signal evidence, `decision`/`decided_at`/
`decision_note`/`origin`) mirror the Telegram tables. Differences:

- `channel_a`/`channel_b` are `Text` UC-ids, not `BigInteger`. Pairs are
  stored lexicographically ordered (`a < b`) — the deterministic analogue
  of Telegram's smaller-id-first.
- Evidence columns: `ref_direction` (enum: one-way a→b, one-way b→a,
  mutual), `shared_token` + `shared_token_channels`, `corroborated_token`
  + `corroborated_token_channels`. No `out_share`/`edges` columns — those
  signals do not exist here.
- Nothing YouTube said is copied: no titles, no handles, no description
  text. Display joins `yt_raw_channels.payload` at read time; a candidate
  for a gone channel (payload removed) shows ids and statuses only.

`yt_channel_families` is the same recursive-closure view as
`tg_channel_families`, keyed on confirmed pairs; families derive, nothing
per-channel is stored. Confirmation, rejection, withdrawal and the
merge-on-bridge behaviour come out of that shape exactly as on Telegram —
`db/yt_channels.py` (or `db/channels.py`'s YouTube counterpart) mirrors
`confirm_affiliation`/`reject_affiliation`/`withdraw_affiliation`, plus the
token rejection below.

### D4. Reuse `derive/yt_references.py`'s reading functions

The description signal must read exactly the link forms
`youtube-mention-graph` specifies: handle links with host variants, id
links, bare `@handles` the inventory carries, custom URLs and shorteners
counted and never followed, ambiguity naming no channel. Those rules
already exist as `read_link`, `read_text`, `normalize_handle` and the
`Known`/resolve machinery. Detection builds its own `Known` from channel
payloads (handles from `customUrl`, ids, no videos) and calls the same
functions — no second grammar to drift, and the existing tests cover the
edge cases. Channel descriptions are a new *source* for these readers, not
a new reader.

### D5. `--reject-token`: group rejection, decided scope

`itgraph yt family --reject-token TOKEN` records a rejection for every
pair the token's group proposed that still awaits review, with a note
naming the token. Already-decided pairs are untouched — a rejection never
rewrites a decision, in either direction. Pairs a later run proposes
between new carriers and old ones are new questions and get proposed; the
rejection stays pair-scoped, as on Telegram. Without this, a noisy block
like `david` would cost ~20 pairwise rejections; with it, one.

### D6. Defaults, from the corpus

- `min_token_length` 4, `max_token_channels` 3 (198 pairs; 5 gives 352 —
  tuning headroom, documented in `--help`).
- `max_corroborated_channels` 50 (`google` 28 and `pyladies` 45 fit;
  nothing binds).
- Weights: corroborated group 1.0, description reference 1.0, title token
  0.6 — groups and references interleave at the top, tokens below. As on
  Telegram, the weights order a reading list; they are not calibration.
- Tokenisation: split titles into maximal runs of letters/digits per
  Unicode (`str.isalnum` per character, the `_handle_char` philosophy — a
  token in Hindi is a token), lowercase, minimum length in characters.
  Cyrillic and the 57 non-en/ru seeds participate on the same rule.
- No status filter on detection (mirrors Telegram): a family link on a
  rejected or candidate channel is true, costs nothing, and matters when
  the channel is accepted. The *reading* filters to pairs with at least
  one seed, exactly as Telegram's `seeds_only` does.

### D7. CLI shape

- `itgraph yt affiliates [--min-token-length …] [--max-token-channels …]
  [--max-corroborated-channels …] [--weight-token …] [--weight-reference …]
  [--weight-group …] [--limit N] [--all] [--any-status]` — coverage lines,
  then the ranked list, corroborated groups as blocks with the ready
  `itgraph yt family …` confirm line (ids, wrapped).
- `itgraph yt family CHANNEL... [--note …] | --reject A B | --reject-token
  TOKEN | --withdraw A B` — channels by UC-id or `@handle` (resolved
  against stored `customUrl`; an ambiguous handle fails naming both).
  Reject/withdraw take exactly two, as on Telegram.

Module layout mirrors Telegram: `src/itgraph/yt/affiliation/`
(`signals.py`, `detect.py`, `run.py`) and `src/itgraph/db/yt_affiliation.py`;
decision writes live with the YouTube inventory module, next to Telegram's.

### D8. Backup and restore

`yt_affiliation_candidates` carries operator decisions, so it joins
`INVENTORY_TABLES` in `db/backup.py` — and `tg_affiliation_candidates`
joins with it, for the same reason: today it rides only the weekly full
tier, which is a consistency gap this change closes while touching the
list. `files/neutralise.sql` gains the `yt_affiliation_candidates`
`decision_note` line (mirroring the `tg_` one) and `files/readback.sql`
the matching count; the statement-removal tests follow for both files.

### D9. Nothing for the infrastructure repository

No new workload, timer, secret, outbound connection or dashboard. The only
deploy-side files touched belong to this role (`neutralise.sql`,
`readback.sql`). No requirements to hand to the infrastructure repository.

## Risks / Trade-offs

- [A corroborated block is huge — `google` alone is 378 pairs] → It is one
  decision, not 378: the block presents one ready confirm line, and a
  rejection is one `--reject-token`. Pairs are rows in a table nobody
  scrolls; the review surface stays one screen.
- [Name tokens get corroborated by cross-linking (`david`)] → Accepted
  noise, priced at one `--reject-token`. Tightening (requiring rarity too)
  would drop the org families this change exists to catch.
- [Corpus growth moves the caps' meaning — more channels, more carriers per
  common token] → Every threshold is a parameter, re-running is free (no
  network, no quota), and re-runs never touch decisions. Tuning is cheap
  by construction.
- [Descriptions are ~90 % covered; the reference signal's silence on the
  rest is ignorance, not evidence] → Coverage is reported per run, and a
  one-way reference is not penalised for an unchecked return link — the
  Telegram finding, carried over deliberately.
- [Two channels carrying the same handle make references ambiguous] →
  Reused resolver already treats an ambiguous handle as naming no channel;
  the spec has a scenario for it.

## Migration Plan

One Alembic revision: two tables, one view, indexes mirroring the Telegram
pair unique constraint and score index. No data to backfill (no decisions
exist). Downgrade drops the view and tables — verified on a scratch
database first, as every migration is. Deploy is the usual tag → test
environment → production; the restore files and the backup table list ship
in the same role, so a restored copy and the backup schedule learn the new
table in the same release that creates it.

## Open Questions

None blocking. Exact evidence column names (`ref_direction` vs
`reference_direction`) are left to implementation review; nothing depends
on the choice.
