# Design

## Context

Two destination shapes exist today (`bot/destinations.py`): Telegram's —
`alert_chat_id`, no topic — and YouTube's — `youtube_alert_chat_id` with
`youtube_alert_topic_en/ru`. The delivery loop (`bot/app.py`) walks queues ×
destinations and, per destination, sends what is due, the digest of what
quiet hours and the cap held (`_maybe_digest`, due at `digest_hour`, once a
day, in-memory `last_digest_at`), the weekly list and the rising offers.
Alerts carry no rendered text: evidence is read at render time, from
`tg_alerts`/`yt_alerts` plus the raw layer (`alert_evidence`,
`video_evidence`), and the wording — `SPIKE_LABEL`, `_magnitude`,
`_usual`, the cascade's families line, `z {z:.1f}` — lives once in
`bot/render.py`. The bot's quiet windows are `alert_quiet_from/to_hour` and
`youtube_alert_quiet_from/to_hour` (image defaults 02:00/07:00, both in
`Europe/Moscow`; the role does not override them, so today they coincide
with the collector's `itgraph_quiet_from/to_hour`). Sending goes through
`Sender.send`, which already takes `preview=False`.

The role (`deploy/ansible/itgraph`) passes the bot's Secret
`ALERT_CHAT_ID`, and — only where `itgraph_youtube_alert_chat_id` is
non-zero — `YOUTUBE_ALERT_CHAT_ID` with both topic ids. The infrastructure
repository wires those inputs from its vault in `group_vars/all/main.yml`
and documents the vault's shape in two `vault.yml.example` files; the
production vault itself lives in a private inventory on the controller,
outside it.

Measured on the production database, the day of 2026-11-18 (07:00–02:00
MSK) held 12 directly delivered Telegram alerts and 3 English YouTube
ones; the busiest day in a week held 15. A summary is therefore a message
of at most a few kilobytes, and one Telegram message almost always fits.

## Goals / Non-Goals

**Goals:**

- One forum chat, three topics; every existing message type keeps its own
  wording and finds its topic without a new routing concept.
- A daily summary per destination, assembled from what the queue already
  recorded, restart-safe, with no new scheduled job on the host.
- The role's inputs and the settings stay honest: one chat, three topics,
  all or none.

**Non-Goals:**

- No new CronJob and no change to the host's schedules: the summary rides
  the bot's own poll, like the digest.
- No new metrics series for the summary (the digest has none either).
- No backfill of past days: the first deployment may send exactly one
  summary for the day before it, and then the days close forward.
- The status command's wording is not redesigned; the two existing texts
  are joined.

## Decisions

### One chat in the settings, a topic for every destination

`youtube_alert_chat_id` is removed; `alert_chat_id` names the one forum
chat. A new `telegram_alert_topic` (env `TELEGRAM_ALERT_TOPIC`) joins
`youtube_alert_topic_en/ru`, and a single validator refuses the four set
unevenly — the old YouTube-only validator's reasoning, widened: half a
chat's topics would strand one audience's messages exactly as half of
YouTube's did. `telegram_destination` gains `topic_id`; `youtube_destinations`
reads the chat from `alert_chat_id`; all three destinations name the
setting `ALERT_CHAT_ID` in errors, because that is now the one setting an
operator acts on. `check_chats` is unchanged: with every destination
topical, it asks Telegram for the one chat and requires a forum.

The general topic keeps working as a command surface (`handlers.py`'s
filter admits any topic of the chat), and the `rv:`/`yv:` button filters,
which test the chat id, need no change: both platforms' buttons now live
in one chat, and a card's buttons still answer only their own handler.

### `/status` answers both platforms

The chat no longer implies the platform, so the handler answers with both
texts in one message — Telegram's state, then YouTube's — in the topic it
was asked in. The alternative, routing by the topic asked in, was
rejected: it makes General special and buries the cross-platform answer,
which is the common question in a one-chat world.

### The day summary rides the bot's pass, due at the quiet window's start

A new per-destination step beside `_maybe_digest` runs on every poll. It
is due when the destination's zone has reached `quiet_from` and the day
that just ended has no marker (below). At a 60 s poll, the summary leaves
within a minute of the window's start — "as soon as quiet hours begin",
which is what was asked. A bot that was down at the start sends the day's
summary at its next pass, however late in the day, exactly as the digest
does; an empty quiet window (equal bounds) defines no day and sends
nothing.

This binds the summary to the *bot's* quiet window
(`alert_quiet_from_hour`, `youtube_alert_quiet_from_hour`), not to the
collector's `itgraph_quiet_from_hour` the operator named. They coincide
today (both 02:00–07:00, Moscow), and the bot's window is the truthful
one: it is the window that holds the alerts the summary counts. The role
gains no new knobs for it.

### The day is the quiet window's complement, and the summary counts only direct deliveries

Day *D* of a destination is `[D quiet_to, D+1 quiet_from)` in its zone;
the summary for *D* is due at *D+1*'s `quiet_from` and is labelled with
*D*'s date («Сводка за 18.11.2026»). It covers exactly the alerts of that
destination with `delivery = 'direct'` and `delivered_at` inside the
window (YouTube's per `route`, which delivery already records). Held
alerts are excluded on purpose — the morning digest is their summary —
and offers and weekly lists are not alerts and never were in the queues.
Selection by `delivered_at` is also what the operator asked for ("алерты,
которые пришли не в quiet hours"): an alert raised before the window but
delivered after its start belongs to the day the operator read it in.

### A marker row per destination and day, not bot memory

Which day a destination has been sent is a row — table
`bot_daily_summaries`: `destination` (the destination's constant key:
`telegram`, `youtube_en`, `youtube_ru`), `day` (the label date), unique
together, `sent_at` (null until the whole summary has gone out). The bot
inserts the row *after* every part is sent. The digest could keep its
state in memory because its queue empties and a false "due" finds
nothing; this summary's input — delivered rows — never empties, so memory
would resend yesterday on every restart, and restarts are what deploys
do. The write is the bot's alone, granted with the table as every
queue-side table's migration grants it; no column of the alert tables
changes.

On a partial failure — a summary of several parts whose second send
fails — there is no per-part state, and the next pass resends the whole
summary. This is at-least-once, the same guarantee every send here has,
and the case is rare twice over: a day that long is uncommon, and a send
that fails mid-summary is rarer. Per-part bookkeeping was rejected: it
would add a column that exists only to soften a duplicate of a recap.

### Wording: the alert's own headline, the post's clock, no preview

Each entry is three lines, nothing else:

```
👁 Просмотры — заметно выше обычного (z 3.7)
Neural Shit · 08:56
https://t.me/NeuralShit/7901
```

- The headline is built from the same pieces an alert uses — `SPIKE_LABEL`
  / `YT_LABEL`, `_magnitude`, `_usual`, the cascade's families line — so
  the summary and the alerts can never disagree about what z 3.7 is
  called. YouTube's entries use the same shape and drop the "для канала"
  suffix the single alert carries (the operator's choice: the summary's
  reader has learned the scale by now). A comment entry keeps its count
  and usual; the cascade keeps its families and loses its carrier list.
- The second line names the channel and the post's clock time (`%H:%M`,
  the destination's zone) — the day is already in the header, and the
  entry is a record of when the post happened, not of how old it was when
  an alert fired. YouTube's second line is the video's title, then its
  channel, then the time.
- The third line is the link, where one exists; a channel without a
  username goes without, as its alerts do.
- Entries are ordered by publication time — the reader re-reads the day
  as a timeline — with `raised_at`, then id, breaking ties so a post's
  spike precedes its cascade deterministically.
- The header is `<b>Сводка за 18.11.2026:</b>` — no count, unlike the
  digest's header: the operator's example carries none, and the count of
  a recap is not information the way the count of withheld alerts is.
- Everything but the header is escaped, plain text; the whole message is
  sent with `preview=False`, as the weekly lists are, so no video's
  preview swamps the list; longer days go through `message_parts`, each
  entry whole.

### Migration, grants, restore

One migration: the table, a unique constraint on `(destination, day)`, an
index nothing needs beyond it (the bot asks for one row a day), and the
bot's grants following the pattern of the rising-offers migration.
`neutralise.sql` gains
`UPDATE bot_daily_summaries SET sent_at = now() WHERE sent_at IS NULL` —
the copy closes what production had left half-sent, and no day the dump
knows of is re-summarized into the test chat; `readback.sql` gains the
matching check that no open marker remains. Both statements join the
suite's statement-removal test, as the rule for anything that acts on
restored data requires.

### The role's inputs

`itgraph_telegram_alert_topic` (int, required) replaces
`itgraph_youtube_alert_chat_id` (removed). `tasks/inputs.yml` asserts the
three topic ids non-zero together with the required chat; the
YouTube-keys-when-configured branch of the bot's Secret
(`__itgraph_youtube_alert_keys`, `__itgraph_has_youtube_alerts`) is
folded away — the four values are the chat's inseparable shape now, and
the bot's pod always holds all of them. The synthetic inputs
(`deploy/ansible/inputs/{a,b,e2e}.yml`) grow the Telegram topic id; the
runbook's «Чаты оповещений» is rewritten for one chat and three topics,
including how the new topic's id is taken from a link to a message in it.

## The infrastructure repository

The operator's work, outside this repository; nothing here is changed by
this change's code. The wiring lives in `group_vars/all/main.yml` — four
lines mapping `vault_itgraph_*` to the role's inputs; `site.yml` itself
runs the role by name and pins, and needs no edit. Nothing else in that
repository names these variables: not the verify role (it checks
workloads, images and replicas, and this change adds no CronJob and moves
no schedule), not the monitoring, not the restore catalog. The production
vault is not in that repository at all — it is the private inventory's,
edited on the controller.

In order:

1. **In Telegram** — create the topic «Telegram» in the YouTube alerts
   forum (a new topic, not a renamed General), post any message in it,
   and read the topic's id from the message link's second number. The bot
   is already a member of the chat and needs no new rights; it cannot
   create the topic itself and must not be made able to.
2. **The test vault** (`inventories/test/group_vars/all/vault.yml`):
   `vault_itgraph_alert_chat_id` takes the forum chat's id (the value
   `vault_itgraph_youtube_alert_chat_id` had), the new
   `vault_itgraph_telegram_alert_topic` takes the new topic's id, the
   English and Russian topic ids stay, and `vault_itgraph_youtube_alert_chat_id`
   is removed.
3. **`group_vars/all/main.yml`** — drop the `itgraph_youtube_alert_chat_id`
   line, add `itgraph_telegram_alert_topic: "{{ vault_itgraph_telegram_alert_topic }}"`,
   and update the comment beside them (one forum chat, a topic per
   audience, the bot a member and no administrator).
4. **Both `vault.yml.example` files** (the test inventory's and
   `inventories/production.example`'s) — the same rename, and their
   comments' description of the chats: from "one chat for Telegram's
   alerts and a forum for YouTube's" to one forum chat with a Telegram,
   an English and a Russian topic.
5. **Commit and push**, then `make itgraph vX.Y.Z` in the test
   environment: the pin and `site.yml` in one command, as always. The
   vault and wiring edits must be in place before this run — the new
   release's role requires the new input and refuses without it, and the
   old release's role would ignore the topic and run YouTube-alert-less
   against the mismatched vault, so the two sides of the rename move in
   one sitting, test environment first.
6. **Production** — the same vault rename in the private inventory on the
   controller, then `make itgraph vX.Y.Z PROD=1` from the controller as
   always.
7. **In Telegram, last** — delete the old «Telegram ITGraph Alerts» chat.
   Nothing references it once production runs the new release; deleting
   it before that would strand Telegram's alerts against an unreachable
   chat and fail their sends.

## Risks / Trade-offs

- [The new topic's id is typed by hand into two vaults] → The runbook
  already teaches reading it from a message link, and the bot's start
  check plus the first message into the topic expose a wrong id within
  minutes, in the test environment first.
- [A day's summary can duplicate on a partial failure] → Accepted:
  at-least-once, like every send; the duplicate is a recap, not an alert,
  and the case needs a multi-part day and a failing send at once.
- [The first deployment sends one stale summary — the day before it] →
  Accepted and useful: it proves the whole path in the test environment
  the evening of the deploy. Backfilling markers to suppress it was
  rejected as ceremony around a message the operator wants to see anyway.
- [One chat now concentrates a burst] → Each destination keeps its own
  cap, quiet window and digest; a burst in one topic still cannot spend
  another's, and the summary itself counts nowhere.
- [The operator's phrase named `itgraph_quiet_from_hour`, the code binds
  to the bot's own window] → They are the same hour today, and the bot's
  window is the one that holds what is counted; if the two ever diverge,
  the summary follows the bot's, which is the only honest reading of
  "the day the alerts were sent in".

## Migration Plan

1. Release this change (code, role, migration, docs) as `vX.Y.Z`; a tag
   runs the suite and the role's checks before it publishes anything.
2. The infrastructure repository's steps 1–6 above, test environment
   first: the marker table's migration runs as every deploy's does,
   before the bot's new image starts.
3. Delete the old Telegram chat (step 7).
4. Rollback, if needed: pin the previous release in the same sitting. The
   previous role expects `itgraph_youtube_alert_chat_id`, so the vault
   rename must be reverted with it — the mirror of the forward order; the
   marker table being newer than the old release's schema head is the
   ordinary case a downgrade's dump-guard exists for, and the guard's
   rules apply unchanged. The safest rollback is forward: fix and release
   again, as the repository's discipline prefers.

## Open Questions

None blocking. The exact part header for a multi-part summary's later
parts («Сводка за 18.11.2026 — продолжение») is a wording detail that
cannot change the approach and is decided at implementation.
