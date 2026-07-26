## Why

A backfill over 200 channels ended in a 24-hour FloodWait. The pacing that produced it was already deliberate — four seconds between requests, one channel at a time, never concurrent — and that is the useful part of the evidence. A fixed interval, however slow, does not protect against a limit that counts calls per day rather than measuring the rate they arrive at.

The working hypothesis is that the wait came from a per-method daily quota rather than from a burst: `channels.getFullChannel` and `contacts.resolveUsername` carry such quotas, and a full day is the shape a quota gives, not the shape a rate limit gives. This is unverified — the collector logs a FloodWait's duration but not the request that caused it, so the last incident cannot be attributed after the fact. Verification needs its own change; this one proceeds on the hypothesis because every item below is worth doing whichever way that question resolves.

Two things follow from it. Calling the expensive methods less often is worth more than spacing them more evenly — and right now the most expensive request in the walk is the one request nothing paces at all. `fetch_full_channel` runs before the per-window loop that owns the `sleep`, so the first request against a new channel follows the last request against the previous one with no gap whatsoever, and it runs unconditionally on every channel in every run, including channels that only need one more window of history.

Separately, and regardless of cause: a run that answers a day-long wait by sleeping for a day inside the process is the wrong shape. It holds an MTProto connection open for 24 hours to resume work that has no deadline, and it does so silently.

## What Changes

- **Pacing becomes a module.** `tg/pacing.py` owns the random source and every decision about how long to wait. Today the sleeps are scattered across three call sites in two modules and tests patch `asyncio.sleep` per module; adding three more behaviours to that arrangement would make the pacing untestable and blur the line between a pacing sleep and a FloodWait sleep.
- **Request gaps are randomized, with a rare long tail.** A gap is drawn per request from a band around the configured delay instead of being the delay exactly; a small fraction of gaps are drawn from a much longer range instead. One mechanism, two ranges — not two features. Applies to `backfill` and `resolve` alike.
- **A longer pause separates channels.** `backfill` waits a randomized 10–40 seconds between one channel and the next, taken before that channel's first request and only for channels that actually make one.
- **The metadata pass becomes conditional.** `GetFullChannelRequest` is skipped when `raw_channels` already holds a payload fetched inside a freshness window; the peer the history walk needs comes from the session's own cache instead. `--refresh-metadata` forces the pass.
- **A long FloodWait halts the run.** Above a configured threshold the collector stops rather than sleeping it off, reports how long the wait was and when work may resume, and returns the summary of what it had already committed.

Out of scope, deliberately:

- **Recording which request method a FloodWait came from.** The highest-value diagnostic available and the thing that would settle the hypothesis above, but it wants somewhere durable to be written and a way to read it back — its own change, not a line in this one.
- **A per-run or per-day request budget.** If the limit really is a counter, stopping before the counter is reached is the structural answer and it is stronger than everything here. It needs persisted state across runs, so it waits until there is evidence to size it against.
- **An inter-channel pause for `resolve`.** It has no channels to move between; each request is one lookup.
- Concurrency, alternate sessions, and any other way of going faster. Not now, not later.

## Impact

- Modified capability: `message-backfill` — pacing, the metadata pass, and what a long FloodWait does
- Modified capability: `forward-graph` — `resolve` inherits the same randomized pacing and the same halt
- New module: `src/itgraph/tg/pacing.py`, plus a row in the module map in `src/itgraph/CLAUDE.md`
- Modified: `tg/backfill.py`, `tg/resolve.py`, `cli.py` (one new flag)
- No migration. `raw_channels.fetched_at` already exists and is all the conditional metadata pass needs.
- Eight new settings. Named in `design.md` as an accepted cost, with the reasoning for not collapsing them.
- Test churn: the existing pacing tests assert exact sleep values and will not survive randomized gaps. They move from equality to band assertions, and the band becomes the contract.
- Runtime cost: roughly 80 minutes added to a full 200-channel run, against a run that already takes hours. The conditional metadata pass gives most of it back on every run after the first.
