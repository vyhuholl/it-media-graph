# Proposal

## Why

The first run of YouTube family detection (`2026-11-18-detect-yt-affiliated-channels`)
found far more false pairs than true ones: 188 pairs await review, and most
are noise the ranking cannot separate from signal. The review loop answers
this one pair per `--reject` and one corroborated token per `--reject-token`,
but the noise is spread across many tokens and plain reference pairs —
clearing it costs a command per pair, which is exactly the work the ranking
was meant to order, not multiply. The operator does not want the detector
changed; the backlog needs one decision, stated once, behind a confirmation.
Telegram's review queue (74 pending pairs) earns the same door.

## What Changes

- `itgraph yt family --reject-all` and `itgraph family --reject-all`: record
  every pair the default review list shows — pending, with at least one
  channel in scope (a seed) — as not affiliated, in one statement.
- A typed confirmation gates it: the prompt states how many pairs will be
  rejected and that undoing is one pair at a time, and only the exact word
  `yes` proceeds. Anything else writes nothing.
- Already-decided pairs are untouched, as every rejection leaves them; pairs
  hidden because neither channel is a seed stay pending — a channel accepted
  later still gets its question asked, unprejudiced by a list it never
  appeared on.
- With nothing awaiting review the command writes nothing and asks nothing.
- Each rejected pair carries `decided_at` and a note naming the mechanism
  (`--reject-all`), overridable with `--note`.
- Deliberately not added: any change to detection, any bulk `--withdraw-all`
  (undo stays per pair, `--withdraw`, and the prompt says so), and any
  suppression of future proposals — a rejection stays pair-scoped, so a later
  run proposing new pairs is new review, not a regression of this one.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `channel-affiliation`: the Family Confirmation requirement gains the bulk
  rejection — a rejection is still a statement about pairs, but the pairs may
  now be "every shown pending one", behind a typed confirmation.
- `youtube-affiliation`: the same addition to its Family Confirmation
  requirement, alongside the existing `--reject-token`.

## Impact

- `src/itgraph/cli.py`: both `family` commands grow `--reject-all`;
  Telegram's `channel_refs` argument becomes optional, as YouTube's already
  is for `--reject-token`.
- `src/itgraph/db/channels.py` and `src/itgraph/db/yt_channels.py`: a
  `reject_all` function next to the existing decision functions — count then
  one UPDATE, filtered by `decision = 'pending'` and the same at-least-one-
  seed condition `list_candidates`' default reading uses.
- Tests for both commands and both DB functions; no schema change, no raw
  layer access, no network, no quota.
- Backups and restore need nothing: the candidates tables are already
  hand-reviewed-tier, and `decision_note` is already neutralised and read
  back.
