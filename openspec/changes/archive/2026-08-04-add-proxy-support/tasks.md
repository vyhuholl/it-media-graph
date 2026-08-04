## 1. Settings

- [x] 1.1 `uv add python-socks` — a main dependency, not a group. Telethon imports it at client construction when a proxy is set, so "optional" would mean a machine configured for a proxy fails at the moment it first connects
- [x] 1.2 `config.py`: `proxy_type` (`socks5` / `http`, as a `StrEnum` so an unsupported value is a validation error rather than a `TypeError` inside Telethon), `proxy_host`, `proxy_port`, `proxy_username`, `proxy_password` as a `SecretStr`. All optional; unset means direct
- [x] 1.3 A validator refusing a partial configuration: a host without a port, a port without a host, credentials without a host. Same reasoning as the pacing ranges already there — a wrong value must fail at import, not hours into a run on a machine nobody is watching
- [x] 1.4 `tests/test_config.py`: each partial combination refused with a message naming what is missing; a complete configuration accepted; unset accepted; the password absent from `repr(settings)`, as the api hash already is

## 2. The connection

- [x] 2.1 `tg/client.py`: build the proxy argument in the shape Telethon expects and pass it to `TelegramClient`. `build_client` stays the only place that knows — no pass, no command and no other module learns a proxy exists
- [x] 2.2 **No fallback, anywhere.** Do not wrap the connect in a `try` that retries without the proxy, and do not accept a library option that would. A proxy that silently fails open is worse than none: the collector runs, the logs are green, and the address is wrong the whole time. This is the one line of this change that must not be "improved" later
- [x] 2.3 Report the route at connect time — direct, or the proxy host and port. Never the password. This is the only way to tell a proxied deployment from one whose `.env` did not get copied, and that state otherwise looks exactly like success
- [x] 2.4 `tests/test_tg_client.py`: a configured proxy reaches `TelegramClient` in the expected shape; an unset proxy passes nothing at all (not `None` in a tuple, nothing); credentials are included when set and omitted when not; the report names the host and does not contain the password

## 3. Documentation

- [x] 3.1 `.env.example`: the proxy block, commented out, with a line saying an unreachable proxy stops collection by design
- [x] 3.2 Root `CLAUDE.md`: the proxy password among the values that are never committed, beside the api hash and the bot token
- [x] 3.3 `src/itgraph/README.md`: how to configure it; that only MTProto is proxied and the bot is not; that there is no fallback and why

## 4. Close out

- [x] 4.1 `make validate` green