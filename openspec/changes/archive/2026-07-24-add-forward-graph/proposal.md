## Why

Three months of history for 200 channels is sitting in the raw layer, inert. The repost graph is the main asset this project was started for, and it is one derivation pass away.

It is also the first change that grows the inventory beyond what the operator already subscribed to. Every channel referenced by a forward or a mention is a candidate, and these references are the cleanest discovery signal available: people repost their own crowd.

Derived data is disposable — it can always be rebuilt from the raw layer. That is what makes this change cheap to get wrong and cheap to iterate on, in contrast to collection, where a mistake costs a re-fetch.

## What Changes

- `edges` — one row per observed reference between channels: source, target, kind, the message it came from, and when it was published
- `itgraph derive` — rebuilds the derived tables from `raw_messages`; running it twice produces the same result
- Newly referenced channels enter the inventory as unreviewed candidates with discovery source `forward` or `mention`, identified only by their Telegram id
- `itgraph resolve` — a separate pass, and the only part of this change that talks to Telegram: fills in username and title for channels discovered by reference
- `unresolvable` handling: references that cannot be turned into a channel are recorded as such rather than retried forever

Out of scope, all of it re-derivable later at no cost:

- Edge weights, time decay and clustering — the analysis this feeds, not this change
- Language detection, external links from descriptions
- `getChannelRecommendations` as a second discovery source
- Any triage or scoring of the candidates this produces

## Impact

- New capability: `forward-graph`
- Modified capability: `channel-inventory` — channels can now enter without a username or title, and the review queue has to account for that
- New code: `derive/` (edge extraction), `tg/resolve.py`, the `edges` model, two CLI commands
- One migration: `edges`, plus the columns tracking resolution state on `channels`
- `itgraph resolve` is subject to the same pacing and FloodWait rules as backfill; `itgraph derive` touches no network at all