## Context

Small change, one real decision, and the reason it needs a design document at all is that the decision is about a failure mode rather than about a feature.

Everything else here is mechanical: Telethon takes a `proxy=` argument, `build_client` is the only place a client is constructed, and the settings follow the pattern every other setting follows. The part worth writing down is what happens when the proxy is unavailable — because the obvious library behaviour and the correct behaviour are not the same, and the difference is invisible until it matters.

The surrounding facts, so the trade is legible:

```
  what runs where, after the move
  ────────────────────────────────────────────────────────────
  itgraph watch     MTProto, user account   → through the proxy
  itgraph backfill  MTProto, user account   → through the proxy
  itgraph resolve   MTProto, user account   → through the proxy
  itgraph bot       Bot API, HTTPS          → direct
  itgraph derive    no network at all       → n/a
  itgraph alerts    no network at all       → n/a
```

## Goals / Non-Goals

**Goals:**

- Every MTProto connection goes through the configured proxy, or no connection happens.
- Which path was taken is visible without instrumenting anything.
- A misconfiguration fails at import, not at the first request on an unattended machine.
- Nothing outside `build_client` and `config` learns that a proxy exists.

**Non-Goals:**

- Proxy health checking, rotation, or failover between proxies. One proxy, and if it is down the collector is down — which is the correct outcome and not a limitation to be engineered away.
- MTProxy. Different connection class, different purpose.
- Hiding the bot. It has nothing to hide.

## Decisions

### Failing closed is the whole point

A configured proxy that cannot be reached raises, and the command exits non-zero. There is no fallback to a direct connection under any circumstance.

This is worth stating as a rule because the alternative is so easy to arrive at by accident — a `try`/`except` around the connect, a retry that quietly drops the proxy, a library option that falls back on its own. Each of those produces a collector that works, which is exactly the problem: the operator sees a running process and green logs, and the account is reaching Telegram from a datacenter address the whole time. A safety feature whose failure mode is *appearing to have worked* is worse than not having the feature, because it converts a known risk into an unknown one.

The cost is real and accepted: a flaky proxy stops collection. That is the right way round. The loop already survives being stopped — the eleven-hour outage proved it, and a missed sample is dropped rather than replayed — so an hour of proxy trouble costs an hour of early curves and nothing else. An hour of unproxied collection costs a risk that cannot be measured or undone.

### The connection reports which way it went

At connect time, one line: direct, or the proxy's host and port. Never the password.

Cheap, and it is the only way an operator can tell a proxied deployment from an unproxied one without a packet capture. The specific failure this guards against is a deployment that comes up with the proxy settings absent — a `.env` that did not get copied, a systemd unit missing an `EnvironmentFile` — where everything works, nothing errors, and the address is wrong. That state has to be visible in the first line of the log rather than inferred later from a ban.

### A partial configuration is refused at import

A host without a port, credentials without a host, a type this does not support: `ValidationError` when settings load, not `TypeError` when something finally connects.

The same argument as the pacing ranges and the sample offsets already in `config.py`: a value that is wrong is discovered on the machine nobody is watching, hours into a run, and reads as a bug in the collector rather than as a typo in a file. Settings validation is where this project puts that class of error, and this one belongs there too.

### Proxy or no proxy is a property of the client, not of any pass

`build_client` assembles the tuple and hands it to Telethon. `backfill`, `resolve`, `metadata` and `watch` are unchanged and unaware.

That falls out of `client.py` already being the only place a `TelegramClient` is constructed — a rule the project made for a different reason and which pays for itself here. It also means the tests that matter are in one file, and that a future pass gets proxying without being written to.

### SOCKS5 and HTTP, and `python-socks` as the dependency

The two protocols residential proxy vendors actually sell. Telethon speaks them through `python-socks`, which is a real dependency rather than an optional extra — it is imported at client construction when a proxy is set, so a machine configured for a proxy without the library fails at the worst moment.

Added to the main dependencies rather than to a group. A group would say "this is optional", and on the machine this change exists for it is not.

## Risks / Trade-offs

**What is actually being avoided is a solitary address, not a datacenter one.** `docs/PLAN.md` says "residential IP rather than datacenter", and the operator's own history shows that line has never literally held: Telegram is blocked in their country, so every connection to this account already goes through a commercial VPN, whose exit is a datacenter address. On that footing the account walked 211 thousand messages in eleven days and hit exactly three rate limits, all of them `ResolveUsernameRequest` against its daily quota — a per-method counter with nothing to say about addresses. Zero on `messages.getHistory`, which ran thousands of times.

So the axis that matters is whether other people's ordinary Telegram traffic comes from the same address. A VPN exit is shared with a great many of them; a residential proxy is shared with residents; a VPS's own address is used by one thing, and that thing is a collector. Only the third stands out, and not for being a datacenter.

→ The cheapest correct arrangement is therefore likely the VPN the operator already pays for — a SOCKS5 endpoint where the provider offers one, WireGuard on the box otherwise — rather than buying residential exits from a vendor whose other tenants are unknown. This design is indifferent to which: the setting takes a host and a port either way, and encoding an assumption about the vendor is exactly what it avoids.

**A rotating exit is worse than a stable one, whatever kind it is.** Changing address on every connection is more unusual than changing location a few times a week. → Prefer a stable endpoint.

**The move is less risky than it first appears, and the reason is worth recording** so nobody re-derives the wrong caution. A *new login* from an unfamiliar place can prompt a confirmation; an *existing authorized session* whose address changes does not re-authenticate at all. Switching VPN regions already does the latter routinely, and this account currently holds sessions showing two different countries. Moving the session file to a machine on a different exit is that same operation. → Keep `device_model`, `system_version` and `app_version` unchanged, and move when somebody can answer a prompt if one appears — but expect none.

**A re-login on the new machine costs days of collection, and looks like ordinary setup.** The session file carries the entity cache that `cached_peer` reads, and a fresh session has none — so every seed channel is skipped for want of a cached peer, recoverable only through `resolve` at a couple of hundred usernames a day. → Documentation, prominently, in the README section this change adds. It cannot be enforced in code: `login` on a new machine is a legitimate operation and the collector cannot tell a deliberate re-auth from a mistake.

**Collection stops when the proxy does.** → Accepted, by the argument above. Worth a note in the README that this is designed behaviour, so an operator meeting it does not go looking for the fallback that deliberately is not there.

## Migration Plan

No migration, no schema, no stored data. The settings default to unset, which is a direct connection — so this change is inert until someone configures it, and the laptop keeps working exactly as it does now.

Rollback is unsetting the settings.

## Open Questions

- **Whether the connect-time report belongs in `client.py` or in the CLI.** It is a log line about a connection, which argues for `client.py`; but the bot prints its database role in `cli.py`, which argues for symmetry. Currently specified as `client.py`, where the fact actually lives.
