## Why

The inventory names the channels worth watching but holds nothing about what they publish. Every capability that follows — the forward graph, candidate discovery, engagement baselines — reads message history, and none of them can start before that history exists locally.

Fetching it is the one operation in this project that is both expensive and risky: it is slow, rate-limited, and carries the account-ban risk recorded in `docs/PLAN.md`. So it has to be resumable, conservative by default, and strictly confined to channels that were reviewed and accepted. Rejected channels keep their labels, which is what the later classifier needs; the handful of recent posts it also needs is fetched by the triage flow that produces those labels, not here.

Payloads are stored verbatim. Parsing will change repeatedly as new entities and metrics are wanted, and re-fetching to recover from a parsing decision is not an option.

## What Changes

- `raw_messages` — verbatim message payloads, one row per channel and message id
- `backfill_state` — per-channel cursor, progress and failure record, so an interrupted run resumes rather than restarts
- `itgraph backfill` — walks in-scope channels, fetches history back to a configurable date, honours FloodWait, paces itself, and can be limited to a few channels for a cautious first run
- A per-channel metadata pass via `GetFullChannelRequest`: stores the raw payload and resolves `linked_to` for discussion chats
- `last_post_at` on `channels`

Out of scope: extracting anything from the stored payloads. Forward edges, mentions, external links, language detection and automatic candidate discovery all read this data and belong to the derivation change that follows. Comments in linked discussion chats are also out of scope: they are an order of magnitude larger and are the first data to carry personal information, so they get their own change.

## Impact

- New capability: `message-backfill`
- Modified capability: `channel-inventory` — `linked_to` populated, `last_post_at` added, linked chats leave the review queue and are explicitly not backfilled
- New code: `tg/backfill.py`, `tg/full_channel.py`, raw models, one CLI command
- One migration: raw tables plus `last_post_at`
- First change whose commands hit Telegram at volume, from a newly created collection account; default pacing is deliberately slow