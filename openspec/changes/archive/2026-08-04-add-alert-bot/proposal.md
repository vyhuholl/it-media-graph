## Why

`itgraph watch` has been collecting since 2026-08-03 and nothing reads what it writes. The project now measures what it set out to measure and still cannot tell anyone: the whole realtime goal is a delivery problem from here on.

The obvious move would be to build the scoring and the delivery together, and the measurements say not to. Two numbers decide the shape of this change.

**The first alert kind is thin, and it is thin by measurement rather than by guess.** Repost cascades — a post picked up by several unaffiliated channels — need no baselines at all: the threshold is a count over `edges`, which have existed for weeks. Counted over the densely-collected last 30 and 60 days, intra-family reposts excluded:

```
  distinct families        alerts/day        what it is
  reposting one post       (6h window)
  ────────────────────────────────────────────────────────────────
        1                     ~19            "somebody reposted this" — noise
        2                     ~1.1
        3                     ~0.35          one every three days
        4+                    ~0             one case in two months
```

There is almost no band between noise and silence. At the only usable threshold this produces **about one alert a day**, and the median gap to the second family is 4h37m — so it is not "right now" in the sense of minutes either. That is a real signal and not a product.

**The alert kinds with volume are not ready to be right.** Rate-based scoring — forwards, reactions and comments against views — could run today against the baselines already in the 208k collected messages, because a ratio is nearly age-free. "Nearly" is doing the work: forwards accrue more slowly than views, so a two-hour-old post's forward rate reads systematically low against a mature baseline. `notebooks/anomalous_posts.py` says exactly this about the mature posts it scores. The error is in the safe direction — under-alerting, not false alarms — but calibrating it against post age needs the snapshot series, which is days old.

So the split. **This change builds the delivery and one thin producer; the next brings volume into machinery that already works.** The parts that are unpleasant to debug in production — deduplication, rate limits, formatting, feedback, the bot process itself — get exercised on one alert a day while the baselines accrue. Building them at the same time as the scoring would mean debugging both at once, against data that does not exist yet, and mistaking a formatting bug for a threshold that is too high.

The honest cost, stated here so it is not discovered as a disappointment: for roughly two weeks this bot speaks about once a day.

There is a second reason the seam is worth drawing here rather than later. The bot is Bot API — official, no ban risk, and safe to run anywhere. The collector is MTProto from a user account and needs a residential IP. They are the same project and they do not have the same deployment constraints, so the interface between them should be a table rather than a function call from the start.

## What Changes

- A new **`alerts`** table: one row per thing worth telling the operator, carrying what fired, which post, and the evidence. Written by a detection pass, read by the bot — the only interface between the two, so that either can move to another machine without the other following.
- A new **`itgraph alerts`** pass: reads `edges`, finds posts that crossed the family threshold, and writes rows. Reads no snapshots, touches no network, and is re-runnable — a second run over unchanged edges writes nothing.
- A new **`itgraph bot`** process: aiogram, in its own dependency group, sending to the operator and to nobody else. It reads the alert queue and writes only feedback; **it holds no Telethon session and can write no collection state.** A bot that could touch the inventory would be a Bot API token with reach into the raw layer, and the token is the one credential in this project that lives on whatever machine the bot ends up on.
- **`LISTEN`/`NOTIFY`, not a broker.** The bot wakes on a Postgres notification and falls back to a poll. The same reasoning that rejected `arq` in the last change applies unchanged: Redis for one subscriber contradicts the Postgres-only rule and buys nothing.
- **Deduplication is a constraint, not a convention.** One alert per post per kind, enforced by a unique index rather than by the pass remembering. Escalation — the same post crossing a higher band — is a deliberate second row and is capped.
- **Rate limits with a digest overflow, never a drop.** A daily cap, and what exceeds it is batched rather than discarded. A monitoring tool that silently withholds is worse than one that says "and 14 more".
- **Quiet hours**, matching the collector's: alerts raised overnight are held and delivered as a morning summary. The measured posting trough is 02:00–07:00 MSK at 3.2% of posts, so this costs very little and the loop is asleep for most of it anyway.
- **Feedback buttons** on every alert, storing the operator's verdict. Cheap now, painful to retrofit, and the only labelled data any later threshold work will have. It is also the one thing a one-alert-a-day period is good for.
- **Album parts collapse to one post** and **intra-family reposts are excluded**, as `notebooks/anomalous_posts.py` and `notebooks/export_graph.py` already do. A network reposting itself across its own channels is not a cascade, and an album is not five events.
- The Telegram **bot token** joins the never-commit list. It is a `SecretStr` in settings like the api hash, and — unlike the api hash — it is the credential most likely to end up on a rented machine.

Out of scope, deliberately:

- **All metric scoring.** No baselines, no z-scores, no rate or velocity signals. That is the next change, and it plugs into this queue without modifying it — which is the test of whether this change drew the seam in the right place.
- **Replay and threshold tuning.** They belong with the scoring that needs them; there is nothing to tune in a count over families.
- **Anything conversational.** The bot answers `/status` and takes feedback. It is not a query interface over the graph, and the notebooks remain how questions get asked.
- **Deployment.** Where the bot runs, and whether it runs somewhere other than the collector, is an operational decision this change deliberately makes possible and does not make.
- **More than one recipient.** One operator, one chat id. Subscriptions and per-user preferences are a product this is not.

One dependency worth stating because it is easy to miss: **the alert pass is only as fresh as `itgraph derive`.** Edges are derived, not collected, and the cascade threshold counts them. A full derivation over the current corpus takes 11 seconds, measured — so the operational answer is a schedule rather than incrementality, and building incremental derivation now would be optimising something that costs eleven seconds.

## Capabilities

### New Capabilities

- `alert-delivery`: the queue and the bot. What an alert is, that one is raised once per post per kind, the caps and the digest that absorbs the overflow, quiet hours, feedback, and the guarantees about what the bot may not do — hold a session, write collection state, or reach anyone but the operator. Deliberately says nothing about what produces an alert, which is what lets the next change add a producer without touching it.
- `repost-cascades`: detecting that a post is travelling. Which reposts count (unaffiliated families, albums collapsed, self-excluded), the threshold and its window, that detection reads only derived edges, and that it is re-runnable over unchanged data.

### Modified Capabilities

None. Nothing already specified changes behaviour: the pass reads `edges` and the bot reads `alerts`, and reading is not a requirement change. The session lease that the watch loop introduced already covers the one interaction worth worrying about — the bot takes no lease because it opens no session, which is a property of `alert-delivery` and is specified there.

## Impact

- `src/itgraph/alerts/cascade.py` — new: which posts crossed the threshold, as a pure function over edge rows. No network, no session.
- `src/itgraph/alerts/run.py` — new: the pass — load, detect, store, report. The shape `affiliation/run.py` already has.
- `src/itgraph/db/alerts.py` — new: the queue's tables. Writes, the dedup constraint, the cap accounting, and the feedback rows.
- `src/itgraph/bot/app.py`, `bot/render.py`, `bot/handlers.py` — new subpackage: the aiogram application, how an alert reads as a message, and the two interactions it supports.
- `src/itgraph/db/models.py` — `Alert`, `AlertKind`, `AlertFeedback`.
- `src/itgraph/cli.py` — `itgraph alerts` and `itgraph bot`. Neither takes the session lease.
- `src/itgraph/config.py` — bot token, operator chat id, the family threshold and its window, daily cap, quiet hours. The quiet-hours settings are the loop's; whether they are shared or separate is a design question, and sharing them by default is the recommendation.
- One Alembic migration: two tables and one enum. Nothing existing changes shape.
- `pyproject.toml` — `[dependency-groups] bot = ["aiogram"]`, so the collector installs without it. A group rather than a second distribution: the bot imports `db/models.py` and `config.py`, so a separate package would depend on this one anyway, and splitting distributions is a move that belongs to an actual relocation rather than to the anticipation of one.
- `tests/` — `test_cascade.py`, `test_alerts_db.py`, `test_bot_render.py`, plus the CLI cases. No network in tests, aiogram mocked; the bot token in fixtures is spelled out in words like every other fake credential here.
- `src/itgraph/CLAUDE.md` — five rows in the module table, and a new subpackage line.
- `src/itgraph/README.md` — a section on running the bot, and on the fact that alerts are only as fresh as the last derivation.
- Root `CLAUDE.md` — the bot token joins the never-commit list.
- `docs/PLAN.md` — the alert-bot row of the plan is now two rows; the measured cascade rate is worth recording next to the claim that post-level virality is the realtime product, because it says which half of that claim carries the weight.
