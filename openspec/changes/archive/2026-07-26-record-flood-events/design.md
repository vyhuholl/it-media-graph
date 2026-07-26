## Context

Everything the collector sends passes through `waiting_out_floods` in `tg/backfill.py`. That was already true before this change, and it is what makes it small: there is exactly one place where a `FloodWaitError` is caught, and it already has the exception in hand.

What it does with it today:

```python
except FloodWaitError as exc:
    seconds = getattr(exc, "seconds", 0)
    if seconds > settings.flood_abort_threshold:
        raise FloodWaitTooLong(seconds) from exc
    logger.warning("FloodWait for %ds — sleeping it off", seconds)
    await asyncio.sleep(seconds)
```

`exc.request` is right there, unread.

Two things in Telethon shape the rest of this design, and both were verified against the installed version rather than assumed.

**`FloodWaitError` always carries the request.** `FloodWaitError.__init__(self, request, capture)` sets `self.request` unconditionally, on both paths that raise it.

**Telethon keeps its own per-method flood ledger.** In `client/users.py`, a flood is recorded as `self._flood_waited_requests[request.CONSTRUCTOR_ID] = time.time() + e.seconds`, and a later call to *the same method* is refused up front:

```
r.CONSTRUCTOR_ID in self._flood_waited_requests
  → diff = due - now
  → diff <= 3                       : forget it, send anyway
  → diff <= flood_sleep_threshold   : sleep locally, then send
  → otherwise                       : raise FloodWaitError(request=r, capture=diff)
                                      without sending anything
```

That last branch matters twice over. It is independent evidence that flood waits are tracked per request type — the hypothesis `harden-collection-pacing` had to leave unverified now has something behind it. And it means some of the events this change records cost no network request at all: Telethon short-circuited. A table that cannot tell those apart will overstate what a run actually spent.

## Decisions

### The recording goes in `waiting_out_floods`, and nowhere else

One chokepoint, already holding the exception, already the only place that decides sleep-or-halt. Recording anywhere else would mean a second place that has to agree with this one about what a flood is.

### The method name is unwrapped before it is stored

`type(exc.request).__name__` is wrong often enough to matter. Telethon nests requests inside `InvokeWithLayerRequest`, `InitConnectionRequest`, `InvokeAfterMsgRequest` and four others, and unwraps them by walking `.query` — the tuple is `telethon.errors.rpcbaseerrors._NESTS_QUERY`, and the walk is what `RPCError._fmt_request` does to build its message.

Storing the wrapper name would file `getFullChannel` and `resolveUsername` under the same label, which defeats the entire change. So the walk is reimplemented rather than borrowed: `_fmt_request` is a private static method that returns a formatted string, not a name, and the tuple it uses is private too. Matching class names by suffix (`Request`) against a known set is the version-tolerant approach the project already takes in `classify` — Telethon generates these classes and the set moves between versions.

An unrecognised shape stores whatever the outermost class is called. A slightly wrong name is recoverable; a crash inside a flood handler is not.

### A missing request stores `unknown`, and is not an error

`exc.request` is `None` in the test fakes, and nothing guarantees Telegram never produces a flood outside a request context. `unknown` is a legitimate value, and it is better than the alternatives: refusing to record loses the duration too, and inventing a name would put a lie in the table that answers "what tripped it".

### The write must not be able to destroy the error it is describing

This is the one way this change can do real damage, so it gets stated plainly.

`waiting_out_floods` is called from inside `backfill_channel`, which is mid-transaction: it commits per batch, and a flood can arrive with rows pending. Writing the event on that session would mean either committing the caller's half-finished batch or rolling it back — and rolling it back inside the exception handler for a *different* error is how a run loses history it already fetched.

So the event is written on its own short-lived session, from its own `Database`, committed immediately and independently of whatever the caller is doing. The cost is a second connection for the duration of one insert, on a path that is by definition about to sleep for minutes.

And the write is wrapped: **any** failure to record is logged and swallowed. Telemetry that can turn a survivable rate limit into a crashed run is worse than no telemetry. The one thing this change must not do is make FloodWait handling more fragile than it was.

### What a row holds

| column | why |
|---|---|
| `occurred_at` | the question is always "when, relative to the other ones" |
| `method` | the whole point |
| `seconds` | separates a burst from a quota at a glance |
| `command` | `backfill` or `resolve`; the two share methods, so the method alone does not say which run spent it |
| `channel_id` | nullable, FK to `channels`; a flood during resolution or outside a walk has none |
| `halted` | whether this one stopped the run or was slept off |

`command` cannot be inferred inside `waiting_out_floods` — both commands call it. It is passed in by the caller, which means a small signature change and is the reason this is not a two-line diff.

### Pre-emptive floods are not distinguished, and the read-back says so

Telethon's short-circuit produces a `FloodWaitError` that never reached the network, and nothing on the exception marks it as such. Inferring it — a flood for a method already seen recently, with a duration that looks like a countdown — would be guesswork stored as fact.

So the table does not claim to separate them, and `itgraph floods` says as much in its output rather than letting a reader assume every row cost a request. Making the distinction properly needs the request-level seam below.

### Counting every request needs a different seam, and this is not it

Floods tell you what tripped after it tripped. A budget needs to know what you have spent while you are spending it, and `waiting_out_floods` cannot answer that: it wraps *operations*, not requests. `fetch_full_channel` is one call to it and two TLRequests — `channels.getChannels` and `channels.getFullChannel` — so counting there would undercount, and undercount unevenly by method.

The right seam is `TelegramClient.__call__`. Every high-level helper bottoms out there — `iter_messages` sends `GetHistoryRequest` through it, `get_entity` sends `ResolveUsernameRequest` — so wrapping it on the instance built in `tg/client.py` would see every request exactly once, with its real method name, before it goes out.

That is the foundation for a request budget, and it would also make pre-emptive floods identifiable. It is deliberately not in this change: it touches the client lifecycle for every command including `login` and `dialogs`, and coupling it to a change whose value is a table nobody has read yet would be putting the risky half first. Recorded here so the next change does not have to rediscover it.

### `flood_sleep_threshold` stays where it is

Telethon sleeps through anything under 60 seconds itself, so this table will only ever hold long floods. That is a real blind spot: a rising rate of *short* floods is the early warning that throttling has started, and it is invisible.

Setting `flood_sleep_threshold=0` would route every flood to `waiting_out_floods`, which already handles them correctly, and close the gap. The reason not to do it here is that `waiting_out_floods` does not wrap everything: `tg/dialogs.py` and `tg/auth.py` call Telethon directly, and lowering the threshold would make them start raising where they currently sleep. Fixing that is a separate, bigger change — and bundling a behaviour change into an observability change is how an observability change gets blamed for an outage.

Worth doing next. Not worth doing here.

## What this change does not claim

It will not prevent a FloodWait, and it will not shorten one. It produces the evidence that two decisions already had to be made without: whether the per-method quota hypothesis holds, and whether two commands share a limiter. Both of those are currently answered by reasoning about Telethon's source, which is a good deal weaker than answering them from what actually happened to this account.
