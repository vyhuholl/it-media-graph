## Context

Every channel now in the inventory arrived one of two ways: `dump-dialogs` read it out of the collecting account's dialog list, or derivation found a reference to it and `resolve` turned that handle into an identity. Both are discovery. Neither takes an instruction.

The operator has a hand-written list of about 100 channels — already decided, not discovered — and no command that accepts it. The workaround is to subscribe from a Telegram client and re-run `dump-dialogs`, which spends a `channels.joinChannel` per channel, imports the whole public dialog list of whatever account did the subscribing, and does not save the `contacts.resolveUsername` that opening a channel by name costs anyway.

Two constraints shape everything below.

**The quota.** `contacts.resolveUsername` has no batch form, no substitute inside MTProto, and a ceiling of roughly two hundred a day (measured in `prioritize-resolve-queue`). A hundred channels is about half of that, drawn from the same budget `itgraph resolve` is spending on its own queue. Telethon makes no attempt to soften this: `get_entity` on a string always reaches `_get_entity_from_string`, which issues `ResolveUsernameRequest` unconditionally — deliberately, so that a cached username cannot silently miss a rename ([`telethon/client/users.py:336-339`](../../../.venv/lib/python3.14/site-packages/telethon/client/users.py#L336-L339)). There is no free repeat and no free re-run.

**The account.** This runs from the operator's personal account, because the collection account is currently under a day-long FloodWait on this exact method. A day-long wait is not pacing feedback; it is the anti-abuse tier. Whatever this command does, it must not be capable of putting the personal account there by looping over a file at speed.

## Goals / Non-Goals

**Goals**

- A channel enters the inventory by name, with no join, no subscription and no dialog import.
- A run is bounded by requests, resumable from the same input file, and costs nothing for channels already held.
- The pass is indistinguishable from `resolve` in how it treats Telegram: paced, sequential, FloodWait waited out or halted on, every limit recorded.
- Reviewing on the way in is possible, and a review already made is untouchable.

**Non-Goals**

- Making the resolution cheaper. It cannot be; this removes the join, not the lookup.
- A new queue, table or backfill state. The input file is the queue and the inventory is the progress marker.
- Remembering failures across runs. See the trade-off at the end — deliberately not solved with a table.

## Decisions

### 1. The pass lives in `tg/manual.py` and borrows the whole networked discipline

`resolve_inventory` already encodes what a well-behaved networked pass looks like: one session for the run, `pace()` before every request, the request itself wrapped in `waiting_out_floods` so a `FloodWaitError` is slept off rather than misfiled as a transient failure, a `FloodRecorder` carrying the run's identity, a commit per item, and `FloodWaitTooLong` unwinding the current item and stopping the run with everything before it kept.

None of that is re-derived. `add_channels` has the same skeleton as [`resolve_inventory`](../../../src/itgraph/tg/resolve.py#L104) and calls the same three helpers. The reason to insist: `FloodWaitError` is a subclass of `RPCError`, so a lookup left outside `waiting_out_floods` gets caught by the ordinary failure handler and the loop moves straight to the next username — asking again at the exact moment Telegram said to stop. That is the failure mode that turns a limit into a ban, and it is one forgotten wrapper away.

### 2. Input: usernames as arguments, or `--from-file`

A hundred usernames do not belong on a command line. The file is one entry per line; blank lines and `#` comments are skipped so the operator can annotate and comment out.

Each line is normalised before anything is spent:

```
@durov            ─┐
durov              ├─→  durov
t.me/durov         │
https://t.me/durov ─┘

t.me/+AbCdEf       ─┬─→  rejected, named, nothing spent
t.me/joinchat/…    ─┘
```

Invite links are refused at parse time rather than attempted. They resolve through `messages.checkChatInvite` and only for channels the account was already let into — a different method, a different quota and a different meaning, and the proposal put them out of scope. Failing on the line rather than on the request keeps a typo'd list from costing anything.

The file is parsed and de-duplicated case-insensitively in full before the first request. Sixty duplicate lines should cost one lookup, and a malformed line at position 90 should be reported before ninety requests have been spent, not after.

### 3. One skip query, before any request

```sql
SELECT lower(username) FROM channels WHERE lower(username) = ANY(:names)
```

Once per run, not once per username: a hundred round trips to learn what one statement answers. The set is held in memory and each newly created channel is added to it, so the run stays consistent with itself without re-querying.

Matched case-insensitively, the way [`find_channel`](../../../src/itgraph/db/channels.py#L185) matches, because the inventory stores usernames as Telegram spells them and a hand-written list will not.

This single query does three jobs: it is the resume mechanism (re-run the same file, already-added channels cost nothing, the run continues where it stopped), the quota guard (nothing is spent re-learning a channel already held), and the reason `--limit` can be counted in requests rather than lines.

### 4. `--limit` counts requests, not input lines

Skipped usernames are free, so counting lines would make the flag mean something different on every run. `--limit 50` means "spend at most fifty `contacts.resolveUsername` and stop", which is the only reading that lets an operator budget against a daily ceiling shared with `resolve`.

### 5. Insert-or-update is decided by the upsert, not by the skip set

The skip set answers "is this username in the inventory", which is not quite "is this channel in the inventory". A channel discovered by forward and never resolved has a row with an id and no username; it is invisible to the skip query, so the lookup happens, and [`create_resolved_channel`](../../../src/itgraph/db/channels.py#L332) upserts onto the row that already exists.

So whether a row was created is read off the write itself, with the `xmax = 0` trick [`upsert_channels`](../../../src/itgraph/db/channels.py#L151) already uses: zero on a freshly inserted row, the locking transaction's id on the update path. `create_resolved_channel` gains that return value.

It matters because of the next decision.

### 6. `--seed` and `--kind` apply only to rows this run created

A pre-existing record is refreshed in identity and never in judgement — not its status, not its kind, not its rejection reason, not its review timestamp. That is the inventory's standing rule ("no import path may overwrite a manual review", [`db/channels.py:3-4`](../../../src/itgraph/db/channels.py#L3-L4)), and `--seed` is exactly the flag that would break it by accident: re-running a file against a channel rejected last week would silently un-reject it.

`mark` stays the only way to change a decision, and the run reports which usernames it left alone so the operator can see what was not touched.

**`--seed` and `--kind` are refused together with `--from-file`**, before anything is spent. A typo resolves to whoever holds the misspelled name, and `--seed` is what would put that channel into scope without a human ever seeing its title. Usernames typed on the command line have just been read by the person typing them; a hundred-line file has not necessarily been read by anyone since it was written. Restricting the flag to the argument form removes exactly the case where nobody looked, and leaves the case where the operator is adding two or three channels they are actively looking at.

For a file, the review step stays what it already is: `itgraph channels --status candidate`, then `mark`. Which is the same two commands the operator would run anyway to check what a hundred lines actually resolved to.

*Alternative considered:* have `add` call `mark_channel`. Rejected — it writes unconditionally, which is right for a command whose whole purpose is recording a decision and wrong for one whose input is a list that may be re-run.

### 7. A pending mention the addition makes redundant is cleared

If a username in the list is also sitting in `pending_mentions`, creating the channel makes that queue row a request that can now only return a channel the inventory holds. `delete_pending_mention(session, username.lower())`, lowercased to match how the queue stores it — the same statement, for the same reason, that `_resolve_channel` runs at [`resolve.py:245`](../../../src/itgraph/tg/resolve.py#L245).

The queue's `NOT EXISTS` guard from `prioritize-resolve-queue` already makes such rows invisible, so this is hygiene rather than protection. It is worth doing anyway at the point the redundancy is created: that guard exists because the previous asymmetry silently accumulated 365 of these.

### 8. `CollectionCommand` gains a value, and the migration is not quite routine

A limit hit by `add` belongs to no existing value. Filing it under `resolve` would keep the *method* honest and the *attribution* false, in the one table whose documented purpose is making the command-to-method mapping checkable ([`models.py:140-151`](../../../src/itgraph/db/models.py#L140-L151)) — and with `add` and `resolve` spending the same rationed method in the same days, "which command burned the quota" is the question that table will actually be asked.

This is not a new problem here. [`4bb75804d3cd`](../../../src/itgraph/db/migrations/versions/4bb75804d3cd_metadata_collection_command.py) added `metadata` for exactly this reason and settled how it is done, and this revision follows it rather than inventing a second answer:

**Upgrade.** One statement, `ALTER TYPE collection_command ADD VALUE IF NOT EXISTS 'add'`, and nothing else in the revision. Postgres 16 will run `ADD VALUE` inside a transaction block; what it refuses is *using* the new value before that transaction commits. So the constraint is not "this needs an autocommit block" but "this revision must not also write a row with the value" — which it does not. `IF NOT EXISTS` makes a re-run a no-op, which is what a half-applied upgrade needs.

**Downgrade: deliberately empty.** Postgres has no `DROP VALUE`, so removing one means rebuilding the type and rewriting every column that uses it — a destructive operation to undo a purely additive one. An unused enum value costs nothing and strands nothing. Rows written while it existed keep reading correctly; only the code that would write new ones goes away.

*An earlier draft of this design had the downgrade rebuild the type and fail if any row carried the value, on the grounds that an empty downgrade makes `alembic downgrade` untrue.* That reasoning does not survive contact with the precedent: it would trade a revert that does nothing for a revert that can destroy evidence of real rate limits, in order to undo a change that took nothing away. The existing revision is right and this one matches it.

### 9. Failures are reported and optionally written back out, never stored in the database

A username that resolves to nothing, or to a user or a bot, creates no row and leaves no trace in the database. `pending_mentions` has a row to mark; a line in a text file does not, and giving `add` a failure table would mean a table, a migration and a queue for what is currently a list the operator maintains by hand.

The run ends with the failures named and grouped by why. `--failures-out PATH` additionally writes them as a file in the same format `--from-file` reads: one username per line, with the reason as a trailing `#` comment. So the next run's input is produced rather than retyped, and the reasons travel with it — a name that does not exist should be corrected before it is retried, while a transient failure can be re-fed unchanged.

The option is optional and has no default. Writing a file nobody asked for, into a directory chosen by guesswork, is not a favour; and a run with no failures writes nothing even when the flag is given, so an empty file never has to be interpreted.

### 10. A recent limit on the same method is reported, not obeyed

`flood_events` already knows whether `ResolveUsernameRequest` has been limited lately, and this command is about to spend that exact method. Before the first request, `add` looks for such an event in the last 24 hours — the shape of a per-day quota — and if it finds one, says so: the method, when, and how long the wait was.

It then proceeds. **The table cannot say which account was limited**, because it records the run rather than the identity behind it, and this command is expected to run from a different account than the collection passes. A guard that refused would be guessing about the one fact it does not have, and would be wrong precisely in the case this change was written for. So the warning states what is recorded and leaves the judgement where the information is.

This is also why the new `CollectionCommand` value earns its migration: after this change, the same warning can distinguish a limit that `resolve` earned from one that `add` earned.

## Risks / Trade-offs

**A hundred requests is half a day's quota, shared with `resolve`.** → `--limit` is the instrument; the recommended sequence below splits the list across sittings. `itgraph floods` shows both commands' spend against the same method, which is what decision 8 buys.

**This is the operator's personal account.** A day-long FloodWait on a collection account is an inconvenience; on the account the operator actually uses, it is worse. → Default pacing, `--limit`, and starting with a small slice rather than the full file. The command cannot make this safe on its own — it can only refuse to be the thing that makes it worse.

**A typo resolves to whoever holds the misspelled name.** `@durvo` is a real lookup with a real answer, and nothing about it fails. → Narrowed rather than removed: `--seed` cannot be combined with `--from-file` (decision 6), so an unreviewed list can only ever produce candidates. A typo added from a file lands as a candidate with a title that will not match what the operator expected, which is what the review step is for.

**A failed username costs a request and is not remembered across runs.** Re-running the same file re-spends it. → `--failures-out` turns the failures into the next run's input, so the file shrinks to what is worth retrying. Not eliminated: the operator has to use the flag, and a run that is killed rather than finished writes nothing.

**A channel that renamed is not recognised by the skip query.** Its old username is in the inventory, the new one is in the file, so a request is spent — and correctly, since the upsert lands on the same id and refreshes the name. → Accepted: one request to learn a real fact, and there is no way to know it without asking.

**The session's entity cache could have answered some of these for free.** Ruled out in the proposal, and worth restating as a live trade-off rather than a closed question: on a personal account it might be a meaningful share of the list, and the cost of being wrong is a row pointing at the wrong channel.

## Migration Plan

One Alembic revision, one enum value, no table and no data move.

1. `uv run alembic upgrade head` — takes a full dump first, as every upgrade on the working database does.
2. Verify the revision on a scratch database whose name ends in `_test`: upgrade, then downgrade, and confirm the downgrade refuses once a `flood_events` row carries the new value.
3. `uv run itgraph add --from-file channels.txt --limit 25` — a first slice, small enough to see the shape of the output and the pacing before committing the day's budget.
4. Repeat against the same file. Added channels are skipped for free, so the file is the queue and no bookkeeping is needed between runs.
5. Review what landed: `uv run itgraph channels --status candidate`, then `mark` — or pass `--seed --kind …` on a list that has been re-read.

Rollback is a revert plus `alembic downgrade -1`, which succeeds while no flood event has been attributed to `add` and refuses afterwards. Nothing depends on the value but the rows that carry it.

## Open Questions

Settled by the operator, recorded here because the reasoning belongs with the decisions rather than in a chat log: `--seed` is restricted to command-line usernames (decision 6), a recent limit produces a warning and not a refusal (decision 10), and `--failures-out` is an optional argument with no default (decision 9).

Genuinely still open:

- **Is 24 hours the right lookback for the warning?** It matches the shape of a daily quota, but a limit hit 20 hours ago has almost certainly expired while one hit 20 minutes ago has not, and the warning currently treats them alike. Reporting the event's own timestamp and duration rather than a verdict is the hedge; a computed "expires at" would be better and needs the wait length, which the table does record.
- **What is the actual failure rate on a hand-written list of this size?** It decides whether `--failures-out` is a convenience or the main way the command is used, and it cannot be guessed — dead channels, renames and typos have very different rates and nobody has measured them here.
- **Should `add` learn to read the identity of a channel it already resolved this week?** Not the session cache (ruled out in the proposal), but the inventory's own `resolved_at`. It would matter only for a re-run against a file whose channels were added under different names, which may never happen.
