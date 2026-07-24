## Why

Derivation reads every channel with stored history, without asking whether it is still in scope. A channel backfilled as a seed and later rejected keeps producing edges, and — the part that actually matters — keeps introducing the channels it references into the inventory as fresh candidates.

Stray edges could be filtered at query time; the inventory rows cannot. Once a rejected channel's circle has been written into `channels`, it is in the triage queue, and no downstream filter takes it back out. That is what makes scope a derivation concern rather than an analysis one.

The timing matters more than the size of the change. The next change turns 218 discovered channels into decisions, and every rejection made there should stay contained rather than seeding the next round of candidates.

## What Changes

- Derivation selects its sources explicitly by status, rather than taking whatever has stored history
- Discussion chats are excluded as sources, so the comments phase cannot later turn user messages into channel edges by accident
- Rejected channels stay valid edge *targets* — a seed reposting a rejected channel is an observation about the seed, and dropping it would understate both its activity and the variety of what it reposts
- References from out-of-scope sources create no inventory rows and no pending mentions
- Rebuild semantics are stated plainly: a rejection takes effect on the next `derive --rebuild`, because ordinary runs never delete

Out of scope: retracting channels already discovered through a source that was rejected afterwards. Those rows stay, keeping their original provenance — channels are never deleted, and a rebuild stops adding more rather than undoing the past.

## Impact

- Modified capability: `forward-graph`
- Modified code: the source selection in derivation, and nothing else
- No migration, no network access
- Takes effect on existing data only after `derive --rebuild`

**Operational note.** Nothing has been rejected after being backfilled so far, so the rebuild should leave the edge count where it is. That makes it a cheap verification rather than a risky one: a materially different count after this change means the new predicate is wrong, not that the guard is working.