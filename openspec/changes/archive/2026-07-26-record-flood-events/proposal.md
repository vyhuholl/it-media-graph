## Why

A backfill over 200 channels ended in a 24-hour FloodWait, and the question that decides what to do about it — *which request tripped it* — cannot be answered. The collector logs the duration and nothing else. The incident is over, the log line is gone, and the only evidence left is a number that fits several different explanations.

That gap has already cost two decisions. `harden-collection-pacing` proceeded on an unverified hypothesis about per-method daily quotas, saying so in writing, because there was nothing to check it against. And the operational question that came up straight afterwards — whether a FloodWait in `resolve` leaves `backfill` safe to run — has no answer for the same reason: `resolve` and `backfill` share `contacts.resolveUsername` and `channels.getChannels`, so the answer depends entirely on which method was hit, and that is exactly what is not recorded.

Telethon already knows. `FloodWaitError` carries the `TLRequest` that caused it, on both paths that raise it. The information exists at the moment the exception is caught and is thrown away one line later.

The reason this needs a table rather than a better log line is that the question is always asked after the fact, usually a day later, and often about a run that happened while nobody was watching. A log line answers it only for someone who was already reading the log.

## What Changes

- **`flood_events`** — one row per FloodWait the collector sees: when, which method, how many seconds, which command was running, which channel it was working on, whether it was slept off or halted the run.
- **The method name is unwrapped**, not `type(exc.request).__name__` taken at face value. Telethon nests requests inside `InvokeWithLayerRequest` and friends, and the outer name would file every event under the wrapper.
- **`itgraph floods`** — read the table back: recent events, and a per-method count over a window, which is the form that answers "is this the same thing that bit me last time".
- **One migration**, creating the table.

Out of scope:

- **Counting requests, not just floods.** This change is post-mortem instrumentation: it says what tripped, after it tripped. Knowing you are *approaching* a quota needs a count of every request by method, which needs a different seam — see `design.md`, which identifies it. That work is the prerequisite for a request budget, and this change is the prerequisite for sizing one.
- **Lowering `flood_sleep_threshold` so short floods become visible.** Genuinely valuable and genuinely a behaviour change; `design.md` sets out what it would take and why it is not being bundled with an observability change.
- **Alerting.** The alert bot exists, but a table nobody has read yet is not a thing to page on.
- Anything that changes how a FloodWait is handled. Waits are slept or halted exactly as they are today.

## Impact

- Modified capability: `message-backfill` — rate limit compliance gains a recording obligation
- New model: `FloodEvent` in `db/models.py`; new module `db/floods.py` for the writes and the read-back
- Modified: `tg/backfill.py` (`waiting_out_floods` — the one place every request already passes through), `cli.py` (one new command)
- One migration. The table references `channels.tg_id` nullably, so a flood raised outside a channel walk still records.
- No personal data. A method name, a duration, a channel id already in the inventory.
- Writes must not be able to lose the original error: `design.md` covers the transaction problem this creates, which is the one place this change can do real damage.
