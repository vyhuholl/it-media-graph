# Design

## Context

See proposal.md for why. What shapes the approach:

- **Two kinds of evidence, one of them undated.** A reference to a channel the inventory holds is an edge in `tg_edges`, with the publication time of the message that made it. A mention of a name the inventory does not hold is a row of `tg_pending_mention_sources`: the username and the channel, nothing else. A forward always names a channel by id, so it is always an edge. Only mentions wait as names.
- **`itgraph derive` reads everything, every 15 minutes.** It streams the whole raw layer through the parser and writes edges and pending rows additively. `tg_raw_messages` has no index on time, so reading "the last fortnight's posts" again for a ranking would be a scan of its own.
- **Resolution has two daily quotas.** `contacts.resolveUsername` for a name and `channels.getChannels` for an id: Telethon's `get_entity` on a username or on a `PeerChannel`. Both are spent by `itgraph resolve` and `itgraph add` today. The account met the first three times in July, the longest wait 85,490 s.
- **The poll loop holds the session.** It is the only process that can make a request unattended. Since `walk-new-seeds` it walks new seeds when nothing is due, so it has an idle path, and a turn at resolution fits after it.
- **The resolution pass's own steps already do the right thing with an answer.** `resolve_channel` and `resolve_pending` record a failure, refuse a user or a bot, create a candidate channel from a name, clear the pending row and persist the peer. The loop needs those steps, not a second copy of them.
- **A long rate limit stops the loop's schedule today.** A wait past `flood_abort_threshold` postpones every poll, since a poll and a walk share `messages.getHistory`. A wait on resolution must not, because the limit is on another method.

## Goals / Non-Goals

**Goals:**
- A ranking that can be asked for at any time, reproducible from what is stored, and never stored itself.
- The ranking's head identified without an operator, with the account never spending more than ten resolutions in a day whoever makes them.
- A person's username never written anywhere new, and never printed.

**Non-Goals:**
- The daily selection and its delivery: change 3. Nothing here records what was shown.
- A better order than families: authority (PageRank) waits until the list shows families are not enough.
- Retrying failed resolutions.

## Decisions

### 1. The ranking is worked out on reading, by a pure module

`derive/rising.py` takes plain rows and a moment and returns the ranking, the rows waiting for an identity, and the counts of what was left out. `db/rising.py` loads its rows in one `REPEATABLE READ` transaction:

- the seeds that are not chats, each with its family (`tg_channel_families`, or its own id);
- every edge from them to a channel in `candidate` or `maybe`;
- every source of a pending username that is such a seed, with its two times;
- each target's state: username, `is_chat`, `resolved_at`, `resolve_last_error`; and each pending name's attempts and error.

Nothing it returns is stored, as with every other ranking in `derive/` (`yt_mention_candidates`, `tg_youtube`). At today's size this is about 35 thousand edges and 8 thousand source rows: milliseconds, and no scan of the raw layer.

What is rising, how it ranks and what is left out are the spec's requirements, and the module is their only implementation. `itgraph rising` and the loop both call it.

### 2. The window and the threshold are settings; families are the unit

`rising_window_days` (14) and `rising_min_families` (2) are what the operator accepted. A family is the key `tg_channel_families` gives, or the seed's own id. The candidate's own family is its key there, if it is confirmed in one: an author naming their own new channel is not evidence that anyone else noticed it.

"Before the window" counts the families with a reference older than the window's start. For an edge that is its time. For a pending source it is its first time. The order is families in the window, then families before, fewest first, then the key — the channel's id, or the username for a name — so a tie never depends on the order rows come back in.

### 3. A pending source remembers when, written by `derive`

`tg_pending_mention_sources` gains `first_referenced_at` and `last_referenced_at`. `derive` already knows each mention's publication time when it records the source. The upsert becomes `ON CONFLICT DO UPDATE` with `least` and `greatest`, guarded by a `WHERE` so that a row whose times would not change is not written — re-derivation stays a no-op. A rebuild empties and refills them with everything else.

The columns are nullable. Rows that existed before the revision are filled by the first derivation after the release, within 15 minutes, since every derivation reads the whole raw layer. The ranking treats a source without times as no evidence in the window, so a row is never counted before its time is known.

Rejected: parsing the last fortnight's posts again for each ranking. `tg_raw_messages` has no time index. A ranking would scan it, and the exporter or a scheduled pass could never afford to ask.

### 4. A journal of resolutions, read for the cap, naming nothing

A table `tg_resolutions`:
- `id`;
- `requested_at`;
- `command` — `tg_collection_command`: `resolve`, `add`, `watch`;
- `kind` — `id` or `username`;
- `outcome` — `channel`, `not_channel` or `failed`, a rate limit counted as failed.

It is indexed on `requested_at`, and holds no username and no id, since a name can be a person's.

A row is written with the answer, in the transaction that records it: `resolve_channel`, `resolve_pending` and `itgraph add`'s lookup each write one. The cap is `count(*) where requested_at > now() - interval '24 hours'`, a rolling day rather than a calendar one: Telegram does not say when its counters reset, and a rolling window cannot be spent twice across a midnight.

A process killed between sending a request and writing its answer leaves that request out of the count. It undercounts by one at most, and only for that one.

The loop's resolutions are recorded under `watch`, in the journal and in `tg_flood_events` alike, because that is the command that made them. The README's rule that "a `ResolveUsernameRequest` under `watch` is a regression" becomes "past the cap". The series counts by command, and the alert asked of the infrastructure repository is on `command="watch"` above the cap, which only a bug could reach. The operator's own runs can pass it legitimately.

Rejected: counting from `tg_channels.resolve_last_attempt_at` and `tg_pending_mentions.last_attempt_at`. Each keeps only the newest attempt, a pending row is deleted when it resolves, and neither says which command asked.

### 5. One resolution at a time, after polls and walks, at most every 20 minutes

In `tg/watch.py`, when no poll is due and no walk has a page to take, the loop asks `tg/rising.py` for one resolution. It returns at once, asking nothing, when:
- fewer than `rising_resolve_gap_minutes` have passed since the journal's newest `watch` row (read from the journal, so a restart does not reset it);
- the journal holds `resolution_cap` rows or more in the last 24 hours;
- a recorded wait on resolution has not ended (Decision 6);
- or nothing rising awaits an identity.

Otherwise it takes the first awaiting candidate in rank order. Awaiting means a channel never resolved, with no failure recorded, or a pending username with no attempt. It resolves that one with the resolution pass's `resolve_channel` or `resolve_pending`, paced by `backfill_request_delay` as that pass is, and journals the answer.

A name that is a channel becomes a candidate record (`discovered_via = mention`). The next derivation turns its pending sources into edges, so the evidence carries over with their times. A failure is recorded where the resolution pass records it, which also takes it out of "awaiting": it is not tried again (proposal, "Not covered").

Ten resolutions 20 minutes apart take a little over three hours. At the measured 2 a day the cap is never what limits the loop.

### 6. A rate limit on resolution holds resolution alone, and is never slept off by the loop

`waiting_out_floods` gains a `halt_after` argument, defaulting to `flood_abort_threshold`. The loop passes `0`, so every `FloodWaitError` that reaches it — Telethon itself sleeps off those under `flood_sleep_threshold`, 60 s — is recorded and raised as `FloodWaitTooLong` at once. The loop catches it in `tg/rising.py` and does not postpone polls or the walk.

Whether a wait holds is read from `tg_flood_events`: a row for `ResolveUsernameRequest` or `GetChannelsRequest` whose `occurred_at + seconds` is in the future, whichever command recorded it. A restart therefore asks nothing during a wait, and a manual run's wait holds the loop too, since the limit is the account's.

The loop also holds resolution in memory until the wait's end, whatever method the wait named: one whose request Telegram did not name is recorded as `unknown`, which the record's question does not ask about. A restart forgets that hold, and then the gap and the cap bound what it could ask.

A resolution by the loop that passes its request deadline is recorded as a failure, as `itgraph resolve` records one, so the loop does not try it again; the loop then drops its connection, as after a timed-out poll. Decided in apply and confirmed by the operator on 2026-11-17.

**Not covered by the requirements** (decided in apply): after a ranking with nothing awaiting, the loop reads it again only after `rising_resolve_gap_minutes`, timed in memory, rather than on every idle minute; the gap, the cap and a recorded wait are asked of the journal every idle tick.

### 7. `itgraph rising` prints channels, never names

The report prints, in rank order, each rising channel that has a username and is not a chat: rank, `@username`, title, families in the window and before, the first reference in the window. Then:
- how many await an identity — channels by id and pending names together, as a count;
- how many were left out and why — chats, no username, not a channel, failed;
- the journal's last 24 hours by command, against the cap.

A pending username is never printed. It may be a person's, and the loop resolves the head within a day anyway. The command opens no client and takes no lease, so it runs beside the loop, on the host through `scripts/on-host.sh` without `--session`.

### 8. What the restore and the roles need

The journal names nothing and nothing acts on it, so `files/neutralise.sql` and `files/readback.sql` gain no statement; a restored copy's journal is history like any other. The reader reads the new table through its default privileges. The exporter is granted `SELECT (requested_at, command)` on `tg_resolutions` and nothing else of it, which a role test holds. The bot gets nothing here.

## Risks / Trade-offs

- **[An unattended process spends the account's tightest quota]** → this is the account's main risk, so it is bounded three ways:
  - the cap counts every command's resolutions, the operator's included;
  - any wait stops resolution at once and holds it for as long as Telegram says;
  - an alert fires if the loop's own count ever passes the cap.

  At 2 a day the loop spends a twentieth of what a manual run of `resolve --limit 50` spends.
- **[The first ranking after the release undercounts names]** → pending sources have no times until the first derivation after the release fills them, within 15 minutes. Until then names are not rising; channels are unaffected.
- **[The loop cannot be tried in a test environment]** → nothing collects there. Mitigation: the ranking, `itgraph rising` and the journal's arithmetic are tried on the test environment's restored copy, since `itgraph-detect` derives there too. The loop's turn is tested against the fake client. In production the series and `itgraph rising` show the first resolutions.
- **[A failed resolution is never retried]** → as with `itgraph resolve` today. The operator can still run `itgraph resolve <tg_id> --retry-failed` for a channel, which the journal counts.
- **[A person's username in the ranking]** → names are counted, never printed. A resolution that finds a user writes `not_channel` to the journal and the existing failure to the pending row, as `itgraph resolve` already does.

## Migration Plan

1. Release, then deploy to the test environment. The revision adds the two columns, the table and the grant. After the next `itgraph-detect`, `scripts/on-host.sh rising` prints a ranking of the restored copy, and the series reads 0 for every command.
2. Deploy to production. Within 15 minutes `derive` fills the sources' times. `itgraph rising` shows the head. Over the next hours the series shows the loop's resolutions, at most ten in 24 hours.
3. Ask the infrastructure repository for the alert on `itgraph_telegram_resolutions_24h{command="watch"} > 10` and a panel.

Rollback: revert the pin. The revision's downgrade drops the table and the two columns. A channel the loop created is a candidate like any other.
