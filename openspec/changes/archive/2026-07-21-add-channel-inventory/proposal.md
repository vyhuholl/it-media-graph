## Why

Nothing can be collected before there is a registry of channels to collect from. Every later capability — history backfill, the forward graph, candidate discovery — keys off a channel row and its status.

The initial set comes from the operator's own subscriptions, which are unlabelled: a personal dialog list mixes IT channels with everything else. The inventory is only useful once channels can be reviewed and marked as in or out of scope.

Rejections matter as much as acceptances. They are the first labelled dataset for the classifier that will later triage discovered candidates, and they cannot be reconstructed after the fact.

## What Changes

- Telethon client that authenticates from an existing session file
- `channels` table: identity, status, manual labels (`kind`, `reject_reason`), discovery provenance
- `itgraph dump-dialogs` — writes every channel and chat from the operator's subscriptions into `channels` as unreviewed candidates; idempotent on re-runs
- `itgraph mark` — sets status, kind and rejection reason for one channel
- `itgraph channels` — lists the inventory filtered by status, so review progress is visible

Out of scope: message history, forwards, FloodWait handling, language detection, automatic candidate discovery. All of them depend on this inventory and follow in later changes.

## Impact

- New capability: `channel-inventory`
- New code: `src/itgraph/tg/` (client, dialogs), `src/itgraph/db/` (models, first migration), three CLI commands
- Interactive Telegram login (phone, code, 2FA) is a one-off manual bootstrap documented in the README, not a requirement of this change; the code assumes a session file already exists
- No existing capability is affected — this is the first one