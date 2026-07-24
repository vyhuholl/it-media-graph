## Why

The graph currently records that one channel reposted another, but not what it reposted. Every question about individual posts — which ones get picked up, by how many channels, how quickly — is unanswerable from it, and those questions are the layer the realtime alerting is meant to sit on.

The data has been collected all along: `fwd_from` carries both the original message id and its publication date. This change is a derivation over payloads already stored, so it costs a rebuild rather than a re-fetch. Doing it before the inventory grows keeps that rebuild small.

## What Changes

- `edges` gains the referenced message: its id, and the date it was originally published
- The gap between the two dates becomes available as travel time — how long a post took to reach each channel that picked it up
- Message-specific `t.me` links (`t.me/name/123`) now carry the referenced message id too, making them post-level references rather than channel-level ones
- `edges` gains the referencing message's group id, so a forwarded album counts as one event rather than one per attachment
- The migration empties `edges`; a rebuild repopulates it

Out of scope: any query or command that reads the new columns. Ranking posts by reach, travel-time analysis and virality scoring are exploratory work over derived data, not capabilities.

## Impact

- Modified capability: `forward-graph`
- Modified code: edge extraction and the `edges` model; nothing else
- One migration, which truncates `edges` — see below
- No network access at any point; no change to collection

**Operational note.** Derivation inserts with `ON CONFLICT DO NOTHING`, so existing rows would keep the new columns empty while a re-run reported success. Rather than leave that trap, the migration discards the derived edges outright and `itgraph derive` must be run again afterwards. Nothing is lost: the raw layer is untouched and the rebuild takes minutes.