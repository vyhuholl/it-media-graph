# Proposal

## Why

YouTube channels from one author or one organisation — Google's product
channels, an author's main and satellite channels — are several rows in the
inventory and one actor in the graph. Telegram has had semi-automatic family
detection since `2026-07-30-detect-affiliated-channels` (`itgraph affiliates`
proposes, `itgraph family` decides); YouTube has nothing, and its 887 seeds
now hold families nobody can see. The same review loop is wanted there,
built on the data the YouTube collector already stores and adapted to what
that data can actually support.

## What Changes

- `itgraph yt affiliates` — detection over stored data only: three signals
  (shared title tokens, description references, reference-corroborated token
  groups) merged into one ranked candidate list with per-signal evidence,
  stored in two new tables. Re-runnable; refreshes evidence, never a
  decision.
- `itgraph yt family` — the operator's decision: confirm a group (every pair
  recorded), `--reject` a pair, and `--reject-token` to reject every pair a
  token's group proposed — a noisy group costs one review, not twenty. A
  family is the transitive closure of confirmed pairs, read through a new
  `yt_channel_families` view; no canonical channel, no stored family column.
- Organisation families count: a token block corroborated by an explicit
  reference between its carriers proposes groups of any size an operator
  runs — Google's 28 channels arrive as one block, not as rarity-capped
  pairs. Franchise brands (PyLadies chapters) are families too, by the
  operator's decision.
- Deliberately not ported from Telegram: outgoing concentration and mutual
  density (the corpus cannot support them — no seed reaches even 10 distinct
  reference targets, and 24 pairs have an edge each way where Telegram's
  threshold is 5), and token search over handles/customUrls (YouTube handles
  are single fused words; the tokens live in titles).
- `yt_affiliation_candidates` carries operator decisions, so it joins the
  hand-reviewed backup tier, and its `decision_note` joins the restore's
  neutralisation and read-back.

## Capabilities

### New Capabilities

- `youtube-affiliation`: recognising that several YouTube channels share an
  author or an organisation — signals computed from stored payloads propose
  ranked candidate pairs and groups with their evidence; only the operator's
  decision makes a family.

### Modified Capabilities

(none — `channel-affiliation` stays Telegram-only by the operator's
decision; no requirement of any existing YouTube spec changes. The new
capability reads channel descriptions, which `youtube-mention-graph`
deliberately does not, and stores nothing that spec forbids storing: its
"No Measure Over The Graph" requirement bounds `yt mentions`, not this.)

## Impact

- New tables `yt_affiliation_runs`, `yt_affiliation_candidates` (Alembic
  migration), new view `yt_channel_families`; families derive from confirmed
  pairs, nothing stored per channel.
- New module `src/itgraph/yt/affiliation/` (signals, detection, run) and
  `src/itgraph/db/yt_affiliation.py`, mirroring the Telegram layout.
- New CLI commands under the `yt` group.
- `db/backup.py`: `yt_affiliation_candidates` (and Telegram's
  `tg_affiliation_candidates`, for the same reason) join `INVENTORY_TABLES`.
- `files/neutralise.sql` and `files/readback.sql` learn `decision_note`;
  their statement-removal tests follow.
- No network, no quota: detection reads `yt_raw_channels` and
  `yt_raw_videos` payloads only. No raw layer writes.
