## Why

Collection is moving off the operator's laptop onto a VPS, and `docs/PLAN.md` has said from the beginning that it must not run from a datacenter address: "Run from a residential IP rather than a datacenter one" sits in the same list as "never join channels", which is the strongest ban trigger there is. A rented VPS is a datacenter address by definition, so either that line stops being true or the connection goes through something residential.

What that line was reaching for turns out to be narrower than it says. Telegram is blocked in the operator's country, so every connection to this account already goes through a commercial VPN — a datacenter exit — and on that footing it collected 211 thousand messages in eleven days without a single rate limit on the collection path. What distinguishes an address is not the datacenter but whether anyone else's ordinary traffic comes from it, and a rented VPS's own address is used by exactly one thing.

So what is needed is an exit shared with people, which the operator's existing VPN already is. What is missing is that `build_client` cannot use one. Telethon accepts a proxy; nothing in this project passes it, so today the choice is between a laptop and an unproxied datacenter connection, and neither is what was decided.

The account is worth more than the data it collects. It is aged, it has trust with anti-spam, and it has already walked 211 thousand messages of history — none of which is recoverable by making a new account, because a fresh number pulling hundreds of channels is the pattern this project has spent every design decision avoiding.

## What Changes

- **Proxy settings** — type, host, port, and optional credentials. Unset means a direct connection, which is what a laptop wants and what every test uses.
- **`build_client` passes the proxy to Telethon**, and that is the whole of the mechanism. Nothing else in the collector learns about it: the proxy is a property of how the client reaches Telegram, not of what any pass does.
- **No fallback to a direct connection, ever.** A configured proxy that cannot be reached fails the command. This is the one behaviour in the change worth arguing about, so it is stated as a rule rather than left to whatever the library does: a proxy that silently fails open is worse than no proxy at all, because the operator believes their address is hidden and it is not. The failure mode a safety feature must never have is *appearing to work*.
- **The connection says which way it went** — direct, or through which proxy host — at connect time. An operator who cannot tell has no way to notice that a deployment came up unproxied.
- **MTProto only.** The bot is Bot API over ordinary HTTPS, carries no ban risk, and routing it through a residential proxy would add a failure point to protect nothing.
- **`python-socks`** joins the dependencies, because Telethon needs it to speak SOCKS.
- The proxy password joins the things that are never committed, on the same footing as the api hash.

Out of scope, deliberately:

- **MTProxy.** Telegram's own proxy protocol needs a different connection class and is a different thing from a residential exit; the vendors that sell residential addresses sell SOCKS5 and HTTP. Adding it later costs a setting and a branch.
- **Proxying the bot**, for the reason above.
- **Systemd units, the VPS itself, and how the session file gets there.** Deployment, not behaviour — but see the impact note about the entity cache, which is the part of deployment most likely to go wrong and is documentation this change owes.
- **Backups leaving the machine.** Today they land in `~/itgraph-backups`, which on a laptop is a second copy and on a VPS is the same disk as the database. That is a real prerequisite for deploying anywhere remote and it is a different change: it touches where a dump carrying the operator's subscriptions is allowed to live, which deserves its own argument rather than a paragraph in this one.

## Capabilities

### New Capabilities

None. This changes how the client reaches Telegram, not what the system can do.

### Modified Capabilities

- `channel-inventory`: **Telegram Session Authentication** describes how a client is established — a session file, authorized, held exclusively. It gains a sibling requirement covering the transport that client uses: a configured proxy is used for every Telegram connection, a broken one fails the command rather than falling back, and which path was taken is reported. The existing requirement is untouched; connecting through a proxy changes nothing about sessions, leases or authorization.

## Impact

- `src/itgraph/config.py` — proxy type, host, port, username, password as a `SecretStr`. A validator, because a half-configured proxy (host without port, credentials without host) must fail at import rather than at the first connection on a machine nobody is watching.
- `src/itgraph/tg/client.py` — `build_client` builds the proxy tuple and passes it; the one place a `TelegramClient` is constructed stays the one place that knows about this.
- `src/itgraph/cli.py` — nothing, unless the connect-time report belongs there rather than in `client.py`.
- `pyproject.toml` — `python-socks`, via `uv add`.
- `tests/test_tg_client.py` — that the proxy reaches Telethon in the shape it expects; that an unset proxy passes nothing; that a partial configuration is refused. No network, as ever.
- `.env.example`, root `CLAUDE.md` — the proxy password among the values that never get committed.
- `src/itgraph/README.md` — how to configure it, and **the warning this change most owes the operator**: move `itgraph.session` to the new machine rather than running `login` there. The file is not only an auth key, it is the entity cache `cached_peer` reads, and a fresh session has none — so a re-login leaves every one of the 544 seed channels skipped for want of a cached peer, recoverable only through `resolve` at a couple of hundred usernames a day. That is days of stopped collection caused by a step that looks like setup.
- `docs/PLAN.md` — the residential-IP line predates the possibility of a proxy; worth a clause saying a proxy is how that requirement is met when the machine is rented.
