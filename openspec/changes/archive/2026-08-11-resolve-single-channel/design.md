## Context

See [proposal.md](proposal.md) — Why. What matters for the approach:

- `resolve_inventory` works two queues in one pass, sharing one `remaining` counter and one `FloodRecorder`. The by-id queue comes from `channels_awaiting_resolution(session, retry_failed=..., limit=...)` — `resolved_at IS NULL`, plus `resolve_attempts = 0` unless retrying, ordered by `tg_id`.
- The Telegram session is held under an **exclusive lease**: one process at a time, and a second command refuses immediately rather than waiting. Connecting is therefore not free even when no request is made — it can be the thing that makes `itgraph watch` unable to run.
- `_run` in [cli.py](../../../src/itgraph/cli.py) already turns `ChannelLookupError` into the sentence and exit 1. Any refusal expressed as one of those needs no new handling in the command body.

## Goals / Non-Goals

**Goals:**

- One named id, one request, and the same recording, pacing and FloodWait behaviour as any other run.
- The queue decides membership. "Is this channel resolvable right now" must not have a second answer written somewhere else.
- A refusal costs nothing: no Telegram connection, so no lease taken and no request spent.

**Non-Goals:**

- Changing what happens once a channel is requested. `_resolve_channel` is untouched.
- Any schema change. This is a filter and three refusals.
- Aiming the mention queue, or accepting a username. See the proposal's out-of-scope list.

## Decisions

### 1. The argument is a filter on the existing queue, not a second path

`channels_awaiting_resolution` grows `tg_id: int | None = None`, adding `WHERE tg_id = :tg_id` to the predicate it already has. `resolve_inventory` passes it through and, when it is set, skips the mention queue block entirely.

The alternative — a `resolve_one_channel` that looks the row up by id and calls `_resolve_channel` — is shorter and wrong in a specific way: it would resolve channels the queue refuses, so `retry_failed` and `resolved_at IS NULL` would mean one thing for a whole run and another for a named one. Reusing the predicate makes the two agree by construction, and the argument is then honestly describable as "the queue, narrowed to one row".

The mention queue is skipped rather than bounded to zero. Bounding it would still run `pending_mentions_to_resolve` and could still emit the "no sources recorded" warning, both about a queue this run is not working.

### 2. The refusal happens before connecting, and says which of three things went wrong

The filtered query returns zero rows for three different situations, and the operator needs to be told which:

| state | what the operator does next |
|---|---|
| no such channel in the inventory | `itgraph add`, or check the id |
| already resolved | nothing — it has a username and a title |
| failed before, not retrying | re-run with `--retry-failed` |

So a new `channel_to_resolve(session, tg_id, *, retry_failed)` in `db/channels.py` runs the filtered queue query and, **only when it comes back empty**, reads the row to say why: absent → the existing `ChannelNotFoundError`; `resolved_at` set → already resolved; otherwise → failed before. The classification exists to produce a sentence, not to decide membership — the query already decided that, which is what keeps decision 1 true.

The refusals are `ChannelLookupError` subclasses, so `_run` prints them and exits 1 with no new handling. The option conflicts are `typer.BadParameter`, matching `backup --full --inventory`.

The CLI calls `channel_to_resolve` in its own database session **before** `connected("resolve")`. That is a second read of a row `resolve_inventory` will query again a moment later, and it is deliberate: the point is that a refused run never takes the session lease and never announces the account to Telegram. Two cheap primary-key reads against a local database buy that. `resolve_inventory` keeps its own query rather than accepting the pre-flight's row, so calling it directly — as the tests do — behaves the same as calling it through the CLI.

### 3. `--retry-failed` still gates a retry, even when the channel is named

Naming a channel says *which*, not *anyway*. The alternative — treat the explicit id as intent enough to retry — reads well until the failure is the one that matters: a channel that failed because the session has no `access_hash` for it will fail identically until a `backfill` teaches the session one, and a named run that silently retries makes "I asked for this channel and got an error" the normal outcome. Keeping the flag makes the second attempt something the operator asked for, and the refusal names the flag, so the path from the first run to the retry is one line of output.

### 4. `--limit` and `--min-sources` are refused; `--delay` is kept

`--limit` bounds a run that is exactly one request; `--min-sources` orders a queue that is not being worked. Silently ignoring either would let `resolve 123 --min-sources 2` look like it did something conditional. `--retry-failed` is meaningful (decision 3) and `--delay` is harmless.

`--delay` is not just accepted but *applied*: the single request is paced like any other. There is no branch in the request path, which is the property worth keeping — the collector's pacing rule holds for every request the project makes, including the one a human is waiting on.

## Risks / Trade-offs

- **The named id is spelled differently from the stored one.** Some clients show a channel id with a `-100` prefix; `-1001234567890` and `1234567890` name the same channel, and only the bare form is in `channels`. The argument takes the id as stored — a prefixed one is refused as not-in-inventory, and a leading `-` has to be fenced off with `--` before click will read it as an argument at all. → Not normalised here: stripping the prefix is a second spelling rule on an id, and every other command in the project already takes the id the way `itgraph list` prints it. One line in the README beside the argument, and the refusal is the honest outcome.
- **Two database reads per named run.** → Both are primary-key reads on a local database, against a run that is about to make a network request with a paced delay in front of it. The lease not being taken on a refusal is worth more.
- **`channels_awaiting_resolution` grows a third keyword and a caller that wants exactly one row.** → The signature stays one predicate with optional narrowings, and `channel_to_resolve` is the only place that treats an empty result as an error. If a fourth narrowing ever appears, that is the point to reconsider, not this one.
