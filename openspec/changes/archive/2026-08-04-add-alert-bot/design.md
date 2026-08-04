## Context

This is the first change where something leaves the machine and reaches a person, and the first with two long-running processes that are not the same kind of thing:

```
  itgraph watch                    itgraph bot
  ─────────────────────            ─────────────────────
  MTProto, user account            Bot API, official
  holds the session file           holds no session
  needs a residential IP           runs anywhere
  banning it costs the project     banning it costs a token
        │                                  ▲
        │ raw_messages, message_metrics    │ alerts
        ▼                                  │
   ┌─────────────────────────────────────────────┐
   │                  Postgres                    │
   └─────────────────────────────────────────────┘
                        ▲
                        │ edges
                itgraph derive → itgraph alerts
```

The asymmetry is the reason the interface between them is a table. They will not always be on one machine, and a function call would have to become a table later, at a moment when something else is also changing.

What this has to carry is small. The measured cascade rate is ~1.1 alerts a day at the only usable threshold, so nothing here is under load and nothing here can be tuned by watching it. Both facts push the same way: build the machinery that is unpleasant to fix in production, prove it with tests rather than with traffic, and let volume arrive next.

## Goals / Non-Goals

**Goals:**

- One alert per post per kind per band, enforced by the schema rather than by the pass remembering.
- Nothing silently withheld: what exceeds a cap is batched, not dropped.
- A queue either process can be moved away from without the other changing.
- A bot that cannot touch collection state even if its token is stolen.
- Labelled feedback from the first alert, because it is worthless to collect retroactively.

**Non-Goals:**

- Scoring of any kind. The next change adds a producer; if it has to modify anything specified here, this design drew the seam in the wrong place.
- Tuning. There is nothing to tune in a count over families, and one sample a day would not support it anyway.
- Muting, and volume management generally. See the decision below.
- Low latency. The median gap to a second family is 4h37m; this is a phenomenon that takes hours, and pretending otherwise would design for a requirement that does not exist.

## Decisions

### The alert stores what it took to decide, and re-reads the rest

```
  alerts
    id           bigint  pk
    kind         enum            -- repost_cascade, and what comes later
    channel_id   bigint  ─┐
    msg_id       bigint  ─┴── the post, FK → raw_messages
    band         int             -- which threshold tier was crossed
    value        float           -- what the measure actually was
    raised_at    timestamptz
    delivered_at timestamptz null
    delivery     enum     null   -- direct | digest
    attempts     int
    last_error   text     null

    unique (kind, channel_id, msg_id, band)
```

No copy of the reposters, no rendered text, no channel titles. Which families carried a post is a query over `edges` that the bot runs when it renders, and storing it would be the first derived measure to live in this table — the trade `Edge` already refuses when it carries two dates and declines to store the interval between them.

The consequence is that a digest read in the morning shows fresher numbers than the moment the alert was raised. That is accepted and mildly preferable: the question a reader has is "how far did this go", not "how far had it gone when a cron job noticed". `value` is stored anyway, because that is the number that crossed the threshold and the feedback record has to be about something that does not move.

Evidence as nullable per-kind columns was the alternative, following `AffiliationCandidate`. It is right there — four signals, one column group each, a migration per new signal, and the table says so out loud. It is wrong here: the next change brings three rate-based kinds with four numbers each, and a table with fourteen mostly-null columns describes the union of its producers rather than what an alert is.

### Escalation falls out of the unique constraint

`band` is which threshold tier the post crossed — 2 families, then 3. A post that reaches 2 raises one row; the same post reaching 3 raises a second, because the tuple differs. A post sitting at 2 forever raises nothing more, because it does not.

So there is no "have I already told them" bookkeeping, no counter to get wrong, and no way for a re-run to double-send. The cap on escalation is the number of configured bands, which is a list rather than a policy.

Bands default to `(2, 3)` and stop there for a measured reason: K≥4 fired once in two months. A band nothing ever crosses is not a safety margin, it is a line in a config file that misleads whoever reads it next.

### The poll is the correctness mechanism; the notification is the optimisation

The pass commits, Postgres delivers `NOTIFY` at commit, the bot drains the queue. And the bot also polls on an interval regardless.

That belt-and-braces is not redundancy to be tidied away later. `NOTIFY` is not durable: a bot that was down when the notification fired never learns about it, and there is no replay. The poll is what makes delivery guaranteed; the notification is what makes it fast. Anyone removing the poll because "we have NOTIFY" would be removing the part that works.

The undelivered set is `delivered_at IS NULL`, which is the same predicate for both paths — so the two mechanisms cannot disagree about what is outstanding.

### The bot claims rows; it does not take a lease

Rows are claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, marked delivered in the same transaction as the send is confirmed.

Deliberately not the session-lease pattern the collector uses, and the asymmetry is right. The collector's resource is a session file that genuinely cannot be shared — two writers corrupt it. The bot's resource is an outbound HTTP API that can be called from anywhere; the thing to prevent is not concurrent processes but the same alert being sent twice. That is a property of a row, so the protection belongs on the row, where it also degrades correctly if a second bot ever exists.

A send that fails increments `attempts` and leaves `delivered_at` null, so the next pass retries it. A row that has failed repeatedly is reported by `/status` rather than retried forever at the same rate.

### What exceeds the cap becomes a digest, never a drop

A daily cap on directly-sent alerts. Beyond it, rows stay undelivered and are collected into the next digest, which goes out at a configured hour along with everything raised during quiet hours.

At 1.1 alerts a day the cap will not bind for weeks. It is built now because it is cheap now and expensive later — the alternative is discovering, on the day the scoring lands, that a threshold was slightly too low and the bot has sent ninety messages. Silence is the failure mode that destroys trust in a monitoring tool; "and 14 more" is the failure mode that does not.

Quiet hours are the collector's window by default, in separate settings. They mean different things — the collector's is about not making requests, the bot's about not making noise — and they coincide today only because both are the operator's night. Sharing the default and not the setting is what lets them diverge without a migration.

### Muting is deferred, and the deferral is the point

No mute button, no per-channel suppression. It is a volume-management feature, volume arrives in the next change, and adding it now means designing suppression against a stream of one alert a day — where any rule at all looks correct because almost nothing tests it.

Feedback is 👍 / 👎 and nothing else. That is the part that cannot be added retroactively: a threshold argued about in three weeks will want the operator's verdict on the alerts that fired in the meantime, and those verdicts do not exist unless the button was there from the first message.

### The bot gets its own database role

Grants limited to `SELECT` on what it renders from and `INSERT`/`UPDATE` on the two alert tables. No write access to `channels`, `raw_messages`, `message_metrics`, `edges` or the backfill state.

Convention would be cheaper and is not enough here. This is the one credential in the project that plausibly ends up on a machine the operator does not own, and the difference between "the bot does not write collection state" and "the bot cannot" is the difference between a comment and a guarantee. The role and its grants are created by the migration; which connection string the bot is given is deployment, and stays deployment.

### The window is what makes it an alert rather than a report

A repost counts toward the threshold if it happened within the configured window of the post's publication — 6 hours by default.

The window is not a filter for correctness; a post that collects a second family after three days has still travelled. It is what distinguishes "this is moving now" from "this moved". Measured: a 6-hour window catches 73% of what a 24-hour one does at K=2, and the median crossing is at 4h37m, so most of what a longer window adds is arriving late enough that a reader would rather have seen it in a weekly summary.

It also removes the first-run problem structurally rather than by a special case. A pass run for the first time over a year of edges raises nothing about old posts, because no post outside the window can cross a within-window threshold. There is nothing to mark as already-delivered and no backfill guard to remember.

### Detection is a pass, like every other derivation here

Load edges in the window, group by referenced post, count distinct affiliation families excluding the post's own, compare to the bands, insert with `ON CONFLICT DO NOTHING`. Pure function in `alerts/cascade.py`, the load-detect-store-report shell in `alerts/run.py`, exactly the shape `affiliation/run.py` has.

Albums collapse to one post and intra-family reposts are excluded, as `notebooks/anomalous_posts.py` and `notebooks/export_graph.py` already do — a network reposting itself across its own channels is distribution, not a cascade.

Re-running writes nothing. That is the same discipline as `derive` and affiliation detection, and here it is what makes the pass safe to put on a short schedule.

## Risks / Trade-offs

**The machinery will be under-exercised.** One alert a day will not touch the cap, the digest, or the retry path before the next change loads them. → They are proved by tests rather than by traffic, which is what tests are for; but it should be expected that the first week of real volume finds something, and the next change should budget for that rather than assume this one is finished.

**Alerts arrive hours after publication.** The median crossing is at 4h37m and nothing here can shorten it — it is how long it takes several channels to independently pick a post up. → Stated in the spec and in the bot's own message, which carries the post's age. The risk is not the latency but a reader who expected minutes and concludes the system is broken.

**Alerts are only as fresh as the last derivation.** The pass reads `edges`, which `derive` produces. → An 11-second full derivation makes a schedule the answer; the pass reports how old the newest edge is, so staleness is visible rather than inferred from an absence of alerts. An absence of alerts is exactly what this system looks like when it is working, which is why that report matters more here than elsewhere.

**A stolen token reaches the database.** → The separate role bounds what it can do to the alert tables. It does not bound what it can *read*, and the rendering path reads post text and channel titles — the operator's own inventory. That is the residual risk, and it is a reason to keep the bot on the operator's machine until there is a reason not to.

**One alert a day may read as a broken bot.** → `/status` reports when the pass last ran, how many alerts it has raised, and how stale the edges are, so "nothing is happening" and "nothing has happened" are distinguishable without opening a database client.

## Migration Plan

One Alembic migration: `alerts`, `alert_feedback`, the `alert_kind` enum, and the bot's database role with its grants. Additive; nothing existing changes shape, so the downgrade drops two tables and the role.

`aiogram` arrives in a `bot` dependency group, so `uv sync` for the collector does not install it and the collector's machine does not need it.

Nothing starts the bot. Running it is an operator action, and the pass writing rows nobody reads is harmless — which means this can be deployed in two steps, with the pass running for a day before the bot is pointed at it. That is the recommended order: it makes the first message the operator sees a real one rather than a test.

Rollback is stopping the bot. Rolling back the pass is stopping the pass.

## Open Questions

- **Whether the digest hour and the collector's quiet-hours end should be one setting.** They coincide today. Kept separate here on the argument that they mean different things, but if they are still identical in a month, one of them is ceremony.
- **Whether the pass should run `derive` itself.** It costs 11 seconds and would make the alert's freshness self-contained; against that, every other pass in this project does one thing, and a pass that quietly rebuilds the edge graph is a surprise in the log. Currently specified as separate, with the staleness reported.
