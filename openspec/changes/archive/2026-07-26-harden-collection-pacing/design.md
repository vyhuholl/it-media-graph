## Context

Every request this project makes to Telegram goes through one of two commands, and both are shaped by the same constraint: the account doing the collecting is a real personal account, and losing it costs more than any amount of history. The existing pacing was written to that constraint and is not wrong — it is just aimed at the wrong family of limit.

Telegram's flood limits come in two kinds that behave nothing alike:

```
                  cause                        duration      answered by
  ─────────────────────────────────────────────────────────────────────────
  rate limit      requests arriving too        seconds to    waiting, pacing
                  fast in a short window       minutes

  method quota    too many calls to one        hours to      calling it less
                  method within a day          a full day
```

Pacing answers the first. Only volume answers the second. A day-long wait is the second kind, which is why this change is not only about pauses: two of its five parts reduce calls or stop the run, and those are the parts that matter if the hypothesis holds.

What one run over 200 channels currently costs:

```
  per channel, every run:
    get_entity(username)     → contacts.resolveUsername   (session-cached after
                                                            the first encounter)
                             → channels.getChannels       ← network, always
    GetFullChannelRequest    → channels.getFullChannel    ← network, always,
                                                            never cached
    iter_messages × up to 20 → messages.getHistory

                       × 200 channels
    ≈ 400–600 per-channel requests  +  up to 4000 getHistory
      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      the ones that carry daily quotas, and the ones that repeat every run
      regardless of how much history is left to fetch
```

And where the gaps currently fall:

```
  channel N                                │ channel N+1
  …getHistory ──4s── getHistory ───────────┼─ getFullChannel ──4s── getHistory…
                                           ↑
                                  no gap at all, before the
                                  most quota-bearing request
```

## Decisions

### One module owns the random source and every pause

`tg/pacing.py`. Not because three sleeps are hard to write in place, but because after this change there are five distinct waiting behaviours — jittered gap, rare long pause, inter-channel pause, FloodWait sleep, FloodWait halt — and two of them are not pacing at all. Keeping the pacing decisions in one place gives the tests one seam to patch instead of one per module, and keeps "we are being polite" separate from "we were told to stop", which is a distinction the code should not lose.

The module decides durations and performs the sleeps. It holds no state beyond the random source, so nothing in it needs resetting between runs.

### The random source is `secrets`, and it buys less than it looks like

`secrets.SystemRandom()` — an alias for `random.SystemRandom`, drawing from the OS CSPRNG and offering the full `random` API, including `uniform`, which bare `secrets` does not have.

It is worth being clear about what this is and is not for. Cryptographic unpredictability is not a property anyone needs here: no adversary is predicting the next gap from the previous ones. The one real argument is negative — `random`'s module-level functions share a global, seedable state, and a `random.seed()` anywhere in the process or its dependencies could make the pacing reproducible and correlated. `SystemRandom` cannot be seeded, so that failure mode does not exist. That is a modest robustness gain at zero cost, which is enough reason, but it should not be mistaken for the change that makes the collector safe.

### Jitter is relative, not absolute

A gap is drawn uniformly from `[delay × (1 − j), delay × (1 + j)]` with `j` defaulting to `0.5`.

An absolute `±2` seconds gives the intended `[2, 6]` at the default delay of `4.0` and then breaks: `--delay 1` would produce a range reaching below zero, and `--delay 30` would produce a band so narrow it is indistinguishable from a constant. A fraction gives `[2, 6]` at the default — identical to the absolute form where it matters — and degrades sanely in both directions.

Worth stating plainly: jitter does not reduce the number of requests per day. The mean gap is unchanged, so a daily quota is reached at exactly the same time. Its value is against pattern detection, which is a mechanism nobody outside Telegram can confirm exists. It is in this change because it is nearly free, not because it is expected to be what helps.

### A long pause replaces the gap; it is not a second feature

With probability `p` (default `0.02`) the gap is drawn from `[20, 60]` seconds instead of from the jitter band. Not added to it — drawn instead of it. The long range already dominates the short one by an order of magnitude, so adding them would change the result by a few percent and cost a reader the ability to state the rule in one sentence.

This is the same mechanism as the jitter with a second range bolted on, and that is deliberate. The alternative considered was a single heavy-tailed distribution — `floor + expovariate(...)`, which produces many short gaps and occasional long ones from one parameter set. It is more elegant and strictly worse to live with: without a floor it emits bursts of requests milliseconds apart, its tail is unbounded and needs clipping anyway, and nobody reading the code in six months can say what gap it produces without running it. Two explicit ranges and a probability can be read aloud, and a test can assert them.

Expected cost at the defaults: `0.02 × 40s ≈ 0.8s` added to a mean gap of 4 seconds. A 20% slowdown on the per-request path, for a benefit as speculative as the jitter's.

### A configured delay of zero means no pacing at all

Not a jitter band around zero, not a long pause that might still fire — nothing. Zero is how the tests run and how an operator says "I know what I am doing"; a mechanism that sometimes sleeps 40 seconds despite being switched off would be a surprise in both cases.

### The inter-channel pause goes where the expensive requests are

10–40 seconds, drawn per transition, taken **between** channels rather than before each one: the first channel of a run is not delayed, and a channel skipped for being complete, at its ceiling, or without a username costs nothing, because it makes no request.

The framing that motivated this pause was human plausibility. The reason it earns its place is narrower and more concrete: a channel transition is exactly where the quota-bearing per-channel requests cluster, and it is currently the one place in the walk with no gap at all. Spacing that boundary is spacing the requests that are counted.

It lives in `backfill_channels`, after every skip guard and immediately before `backfill_channel`. Not inside `backfill_channel`, which has no way to know whether another channel preceded it.

Separately, an invariant worth having and cheap to hold: **no request in the module is issued without a gap before it**. That closes the unpaced metadata call whether or not the inter-channel pause happened to run.

Cost: 200 channels × 25 seconds mean ≈ 83 minutes per full run.

### The metadata pass becomes conditional, and gives up a probe to do it

`GetFullChannelRequest` is skipped when `raw_channels` holds a payload for the channel fetched within `channel_metadata_max_age_days` (default 30). A channel description and a linked discussion chat change on the order of months; re-reading them on every run buys nothing and spends the least cacheable request in the walk.

The catch is that the metadata pass returns more than a payload — it returns the resolved entity the history walk needs. Skipping it means getting that peer another way: `client.get_input_entity(username)`, which Telethon serves from the session file's entity cache and which therefore usually costs no network call at all. On a cache miss Telethon falls back to `resolveUsername` itself, which is the same request that would have been made anyway, so the skip is never worse than the pass.

Two consequences, accepted:

- **The reachability probe is lost.** `fetch_full_channel` ran first partly so an inaccessible channel would cost one request rather than failing part-way through a long walk. When it is skipped, an inaccessible channel is discovered by its first `getHistory` instead — one request either way, and the failure is classified identically. The probe was cheap insurance, not a guarantee, and it is not worth 200 quota-bearing requests per run to keep.
- **Anything that cannot supply the peer falls back to the full pass** rather than failing. A skip that turns into an error would be a regression against a path that works today.

`--refresh-metadata` forces the pass for every channel a run walks, for the case where a description or a linked chat is known to have changed.

### A long FloodWait halts the run, and the exception cannot be an `RPCError`

Above `flood_abort_threshold` (default 1800 seconds) the collector stops instead of sleeping. Below it, nothing changes: the wait is slept off and the request retried, exactly as now.

The threshold sits above Telethon's own `flood_sleep_threshold` of 60 — waits under a minute never reach this code — and below anything that reads as a quota. A half-hour of held-open connection is tolerable; a day is not, and a day-long sleep is also fragile in a way the alternative is not, since the machine will likely suspend before it elapses.

The implementation detail that decides the design: **`FloodWaitError` is a subclass of `RPCError`.** Re-raising it from `waiting_out_floods` would land it in `except (RPCError, OSError, ValueError, TypeError)` in `backfill_channels`, where it would be classified as a transient channel failure and the run would move immediately to the next channel — issuing a fresh request at the exact moment Telegram asked for silence. That is the behaviour `waiting_out_floods` was written to prevent, and it is the behaviour that turns a rate limit into a ban.

So the halt is signalled by a new `FloodWaitTooLong(RuntimeError)`, deliberately outside the `RPCError` hierarchy, carrying the wait in seconds and the timestamp work may resume at. Both `backfill_channels` and `resolve_inventory` catch it explicitly, stop their loops, and return the partial summary they had accumulated.

Nothing about a halt is a channel's fault, so the channel being walked is not recorded as a failure. Its committed progress stands — the cursor advances with the batch it describes, so a halted run resumes exactly like an interrupted one.

The CLI reports the halt alongside the partial summary and exits non-zero, so a scheduled run cannot look like a successful one.

### Eight settings, not collapsed

`pacing_jitter`, `pacing_long_pause_chance`, `pacing_long_pause_min`, `pacing_long_pause_max`, `backfill_channel_pause_min`, `backfill_channel_pause_max`, `flood_abort_threshold`, `channel_metadata_max_age_days`.

That is a lot of surface for one change, and the count was the main argument against doing all of this at once. They stay separate because each one is a thing an operator might genuinely need to move after an incident, and because the pairs are ranges — a range expressed as one number is a range with a convention attached, and the convention is the part people misremember. The defaults are the conservative values and none of them are expected to be touched in normal use.

## What this change does not claim

It does not claim to prevent another day-long FloodWait. If the cause was a daily quota, the parts that help are the conditional metadata pass — which removes roughly 200 quota-bearing requests from every run after the first — and the halt, which stops the run from continuing to spend against a limit it has already hit. The randomized gaps do not reduce request volume and cannot affect a counter.

It does not establish what actually tripped the limit. That needs the request method recorded at the point the FloodWait is caught, which is out of scope here and named in the proposal.

The honest summary is that this change makes the collector cheaper and better-behaved when it is told to stop, and adds some inexpensive camouflage whose value is unproven.
