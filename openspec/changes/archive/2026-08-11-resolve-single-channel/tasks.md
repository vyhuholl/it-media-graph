## 1. The queue, narrowed to one row

- [x] 1.1 `db/channels.py`: `channels_awaiting_resolution` gains `tg_id: int | None = None`, adding `WHERE tg_id = :tg_id` to the predicate it already has. One extra clause on the existing statement — **not** a second query and not a separate function, so `resolved_at IS NULL` and the `retry_failed` rule keep meaning the same thing for a named channel as for a whole run
- [x] 1.2 `db/channels.py`: two new `ChannelLookupError` subclasses beside `ChannelNotFoundError` — one for a channel already resolved, one for a channel that failed before and is not being retried. The second's message names `--retry-failed`, so the next command is in the output rather than in the README. `_run` already prints a `ChannelLookupError` and exits 1; nothing new is caught anywhere
- [x] 1.3 `db/channels.py`: `channel_to_resolve(session, tg_id, *, retry_failed=False) -> Channel` — calls `channels_awaiting_resolution(tg_id=...)` and returns the row; only when that comes back empty does it read the channel to say *why*: absent → `ChannelNotFoundError`, `resolved_at` set → already resolved, otherwise → failed before. The classification writes the sentence; the query decides membership. Docstring says that, because the ordering is the whole point of task 1.1
- [x] 1.4 Tests in `test_channels.py`: the filter returns the named row when it is in the queue and nothing otherwise; `channel_to_resolve` raises the right one of the three for an absent id, a resolved channel and a failed one; the failed one returns the row under `retry_failed=True`

## 2. Resolution works exactly one channel

- [x] 2.1 `tg/resolve.py`: `resolve_inventory` gains `tg_id: int | None = None` and passes it to `channels_awaiting_resolution`. `_resolve_channel` is untouched — a named run records, paces and persists peers exactly as an unnamed one does
- [x] 2.2 Same function: when `tg_id` is set, skip the mention-queue block entirely rather than bounding it to zero. Bounded, it would still run `pending_mentions_to_resolve` and could still emit the "no sources recorded" warning — both about a queue this run is not working
- [x] 2.3 `tg/resolve.py`: log which channel the run was narrowed to, the way the mention queue logs the evidence it ordered by. A one-request run should still say what it spent the request on
- [x] 2.4 Tests in `test_resolve.py`: with several channels awaiting resolution and a non-empty mention queue, naming one id makes exactly one request, for that channel, and no username is requested; the summary counts it the way an unnamed run counts it; a named channel that resolves to a user is recorded as not-a-channel, same as before
- [x] 2.5 Test in `test_resolve.py`: a named channel that failed before is not requested without `retry_failed`, and is requested with it — the queue's rule, reached through the argument

## 3. The command

- [x] 3.1 `cli.py`: `resolve` gains an optional positional `tg_id` argument, `metavar="TG_ID"`, typed `int | None` and defaulting to `None`, so a bare `itgraph resolve` is unchanged. Help text says it is the id as the inventory stores it — no `-100` prefix, and `itgraph list` prints the form the argument wants
- [x] 3.2 `cli.py`: `--limit` and `--min-sources` alongside `TG_ID` raise `typer.BadParameter`, the way `backup --full --inventory` does. One bounds a run that is already one request, the other orders a queue that is not worked. `--delay` and `--retry-failed` are accepted and both do what they say
- [x] 3.3 `cli.py`: when `tg_id` is given, call `channel_to_resolve` in its own database session **before** `connected("resolve")`, and let the raised error out. A refused run must not take the exclusive session lease — refusing after connecting can be the thing that stops `itgraph watch` from running, for a run that makes no request. The second read inside `resolve_inventory` is deliberate: it keeps the pass self-contained for a caller that is not the CLI
- [x] 3.4 Tests in `test_cli.py`: the argument reaches the pass; `--limit` and `--min-sources` with an id exit non-zero and connect to nothing; an absent id, a resolved channel and an un-retried failure each exit 1 with their own sentence and never construct a client

## 4. Close out

- [x] 4.1 `make validate` green — lint, mypy, pytest, ansible-lint
- [x] 4.2 `src/itgraph/README.md`: the `resolve` option table gains the argument, with the two refusals and the id spelling beside it. The section describes a command that works queues; it now also answers one question about one channel, and that is worth a sentence rather than a table row alone
- [x] 4.3 `src/itgraph/CLAUDE.md`: the `db/channels.py` row already says "resolution state" and stays true; check it and leave it alone if so
- [x] 4.4 `openspec validate resolve-single-channel --type change` green
