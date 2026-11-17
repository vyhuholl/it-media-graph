# Design

## Context

See proposal.md for why. What shapes the approach:

- **The ranking exists and is never stored.** `derive/rising.py` ranks; `db/rising.py` loads its rows in one `REPEATABLE READ` transaction. Its `ranked` list is exactly what can be shown: channels in `candidate` or `maybe` with a username, not chats. Names and channels known by id alone are counted, never shown.
- **The bot's database role writes only delivery state.** That role is what bounds the damage when the bot's token leaks. It already reads `tg_channels`, `tg_edges`, `tg_raw_messages` and `tg_channel_families`, which is all a card needs.
- **The weekly list is the nearest precedent.** A CronJob computes and stores it, holding ids and itgraph's numbers and no text. The bot reads the text at sending, sends outside quiet hours, outside the cap and the digest, and marks what it sent. A restored copy marks everything undelivered as delivered.
- **The bot already takes callbacks.** `bot/handlers.py` answers presses on the retired feedback buttons. Every handler is filtered to the configured chats.
- **A seed is walked without anyone asking.** Since `walk-new-seeds`, the poll loop walks every seed whose walk is not complete, earliest `reviewed_at` first. So a decision that sets `status = seed` through `mark_channel` is all a new seed needs.
- **`itgraph-detect` runs every 15 minutes in every environment,** under the main role, holding the database's URL and nothing else.

## Goals / Non-Goals

**Goals:**
- A reviewer sees a few rising channels a day, in the chat they already read, and reviews each with at most two presses, as `itgraph mark` would.
- The bot gains no privilege over the inventory.
- A restored copy offers nothing from production and applies nothing of production's.

**Non-Goals:**
- YouTube's candidates (change 4).
- A note beside a decision, changing a decision in the bot, reminders, offering a channel twice.
- Showing the bot's offers in `itgraph rising` or `/status`.

## Decisions

### 1. Two tables: a day, and its offers

`tg_rising_offer_days`, one row per day the selection ran, an empty day included:
- `day` (the Moscow date, primary key);
- `computed_at`;
- `size`, the `rising_offers_per_day` it was computed with.

The series reads `computed_at`, so a day with nothing to offer still shows the pass ran.

`tg_rising_offers`, one row per channel offered:
- `id`;
- `day` → `tg_rising_offer_days`;
- `channel_id` → `tg_channels`, **unique**: a channel is offered once, ever;
- `rank` within the day;
- `families`, `families_before`, `first_in_window`, as the ranking gave them;
- `offered_at`;
- `delivered_at`, `attempts`, `last_error` — the bot's delivery;
- `decision` (`channel_status`), `kind` (`tg_channel_kind`), `reject_reason` (`tg_reject_reason`), `decided_at` — the bot's decision;
- `applied_at`, `applied` — what the detection pass did with it.

Checks:
- a decision is `seed`, `maybe` or `rejected`;
- a seed has a kind and a rejection a reason, and nothing else has either;
- `decided_at` is set exactly when there is a decision;
- `applied_at` only after `decided_at`, and `applied` with it.

Partial indexes: undelivered offers, and decided offers not yet applied — what the bot and the detection pass ask on every tick.

No username and no title are stored: the bot reads them at sending, as it reads a weekly entry's. A channel renamed between 11:00 and the send is shown by its new name.

Rejected: one table with the day as a column. An empty day would leave no row, and the series could not tell "nothing rose" from "the pass did not run".

### 2. The selection is pure, the command stores it

`derive/offers.py` holds the pure choice and the two passes around it — the selection, and applying decisions (Decision 6) — as `derive/edges.py` holds the derivation pass. Its `choose_offers` takes the ranking's `ranked` list, the set of channels ever offered and the size, and returns the first `size` channels not offered before, in rank order. `itgraph offer`:
- loads the ranking's rows as `itgraph rising` does;
- ranks as of now;
- stores the day and its offers in one transaction;
- prints counts only — how many rose, how many were offered, how many are left for later.

A second run on the same day meets the day's primary key inside a savepoint and says the day has its selection, as `itgraph weekly` does. `--preview` prints the day's offers with their usernames and writes nothing. It is for an operator at a terminal; the CronJob never passes it, because a job's output is kept in the cluster's logs.

The day is the date in `watch_timezone` at computing. The CronJob runs at 11:00 Moscow time with a starting deadline of 13 hours, so a run missed while the host was down starts when it is back, the same day.

### 3. The bot sends a card per offer, as it sends a weekly list

A step of the bot's delivery pass, for the Telegram destination:
1. claim the undelivered offers, oldest day first, then by rank;
2. read each one's channel;
3. if the channel is no longer `candidate` or `maybe`, or has lost its username, close the offer as delivered without sending it — a card names and links the channel by its username;
4. otherwise render the card and send it with its buttons;
5. mark it delivered.

As everywhere in the bot, the order is claim, commit, send, mark, so the residual failure is a duplicate card, not a lost one. Offers go out only outside the alert chat's quiet hours, are not counted against the cap and are never held for the digest. A failed send counts an attempt and is tried on the next poll.

The card, in HTML with everything from a channel or a post escaped:

```
📈 Растёт: @fake_channel                         (linked to t.me/fake_channel)
Fake Channel
3 семьи за 14 дней, 1 раньше · впервые в окне 10 ноября
Ссылались: @seed_a, @seed_b, @seed_c             (each linked to a post)
```

The posts are read at sending:
- the newest post in the window of each referring family, from `tg_edges` whose source is a seed that is not a chat;
- one per family, the families with the newest reference first, up to three;
- each linked as `t.me/<seed>/<msg_id>`. A seed without a username is named by its title, unlinked.

The link preview is forced to the channel's own link. Telegram renders it with the channel's avatar and description, which is what a reviewer needs and what the project never stores.

`BotSender.send` gains an optional keyboard. The alerts and the lists pass none, as now.

### 4. A press is data the bot parses; the bot decides nothing it was not asked

Callback data is `rv:<offer id>:<press>` — at most 64 bytes, as the Bot API requires. A press is one of:
- `seed`, `reject` — show the second step;
- `back` — show the three again;
- `maybe` — decide maybe;
- `kind:<value>`, `reason:<value>` — decide a seed of that kind, or a rejection for that reason.

The pure parts live in `bot/review.py`: parsing the data, the keyboards as rows of label and data, the card's text and the line that states a decision. `bot/handlers.py` binds them to aiogram, as it binds `/status`.

A press is accepted only in the Telegram destination's chat. The dispatcher's filter already drops every chat not configured, and the handler also refuses the YouTube chat. `seed`, `reject` and `back` only redraw the buttons and write nothing. A deciding press writes:

```sql
UPDATE tg_rising_offers SET decision = …, kind = …, reject_reason = …, decided_at = now()
 WHERE id = :id AND decision IS NULL AND delivered_at IS NOT NULL
```

If it updated a row, the card is edited: the decision line is appended — «✅ В сиды · медиа», «🤔 Может быть», «🚫 Отклонён · крипта» — and the buttons are removed. If not, the press is answered «уже размечено» and the card is redrawn with the decision that won. So two people pressing at once leave one decision. An unknown offer id is answered «нет такого предложения».

Nothing about who pressed is stored or logged. The handler reads `callback_query.data` and the message, never `from_user`. The retired feedback buttons' pattern, `^(useful|boring):\d+$`, does not overlap `rv:`.

### 5. The labels

Per `CLAUDE.md`: one to three words, a leading emoji, no trailing punctuation. A test holds every label to the rule.

| Press | Label |
|---|---|
| seed | 🌱 В сиды |
| maybe | 🤔 Может быть |
| reject | 🚫 Отклонить |
| back | ↩️ Назад |
| `personal` | 👤 Личный |
| `aggregator` | 📰 Агрегатор |
| `company` | 🏢 Компания |
| `vacancies` | 💼 Вакансии |
| `media` | 📺 Медиа |
| `community` | 👥 Сообщество |
| `event` | 📅 Событие |
| `not_it` | 🙅 Не про IT |
| `adjacent` | ↔️ Смежное |
| `crypto` | 🪙 Крипта |
| `infobiz` | 💸 Инфобиз |
| `ads` | 📢 Реклама |
| `content_farm` | 🏭 Контент-ферма |
| `other_scene` | 🌐 Другая тусовка |

The second step lays the seven out two per row and puts «↩️ Назад» on a row of its own.

### 6. The detection job applies decisions

`itgraph apply-reviews` takes the decided offers not yet applied, oldest decision first, one transaction each:
1. lock the channel's row (`SELECT … FOR UPDATE`);
2. if it is still `candidate` or `maybe`, apply the decision through `mark_channel`, as `itgraph mark` would, and record it applied;
3. otherwise record it not applied;
4. either way, set `applied_at`.

It runs as a third container of `itgraph-detect`, beside `alerts` (`derive && alerts`) and `score`. Neither a failed derivation nor a failed scoring holds it back, and it fails the job as they do. It prints counts only.

`mark_channel` sets `reviewed_at`, which orders the poll loop's walk, so a seed decided in the bot is walked from its decision. A decision of maybe sets no kind, as `mark --maybe` sets none.

Rejected: applying in the bot, which needs the inventory privilege the operator declined; applying in the poll loop, which runs only where collection does, so the test environment could never try the flow.

### 7. Roles

- **The bot:** `SELECT` on both tables; `UPDATE (delivered_at, attempts, last_error, decision, kind, reject_reason, decided_at)` on `tg_rising_offers`. Nothing on `tg_channels` beyond the `SELECT` it has. A role test holds both halves: it may record a delivery and a decision; it may not insert an offer or a day, change an offer's channel, rank or numbers, mark one applied, or update `tg_channels`.
- **The exporter:** `SELECT (computed_at)` on the days; `SELECT (offered_at, delivered_at, decided_at, applied_at)` on the offers. Neither which channel, nor what was decided, nor the day itself.
- **The reader:** both tables through its default privileges.

### 8. The restore

`files/neutralise.sql` gains two statements:

```sql
UPDATE tg_rising_offers SET delivered_at = now() WHERE delivered_at IS NULL;
UPDATE tg_rising_offers SET applied_at = now(), applied = false
 WHERE decided_at IS NOT NULL AND applied_at IS NULL;
```

`files/readback.sql` gains a check for each — undelivered rising offers, unapplied rising decisions — and the suite's test removes each statement in turn and expects the read-back to fail. A copy's days and its applied decisions are history like any other. A test environment then computes and sends its own selection, from its own copy, into its own chat, as it does its weekly list.

### 9. The series

- `itgraph_telegram_rising_offers_computed_timestamp_seconds` — the newest day's `computed_at`; 0 when none.
- `itgraph_telegram_rising_offers_oldest_undelivered_seconds` — now minus the oldest undelivered `offered_at`; 0 when none.
- `itgraph_telegram_rising_decisions_oldest_unapplied_seconds` — now minus the oldest unapplied `decided_at`; 0 when none.

The infrastructure repository is asked for three alerts:
- a selection older than 25 hours;
- an offer undelivered for an hour outside quiet hours;
- a decision unapplied for an hour.

Requirement 9 of the infrastructure file asks this of the new CronJob, and of the step the detection job gains.

### 10. Settings

`rising_offers_per_day` (5, at least 1). The hour is the CronJob's schedule, `itgraph_offer_schedule` (`0 11 * * *`, in `itgraph_timezone`), as every other job's is.

## Risks / Trade-offs

- **[The bot can rewrite a decision before it is applied]** → a column grant cannot say "only when empty". The handler writes only where `decision IS NULL`. A leaked bot role could change the decision on an offer, or decide one never sent, at most five channels a day and only channels still a candidate or a maybe at application. The alternative is a trigger or a function, and the operator chose the plain grant pattern.
- **[A decision takes up to 15 minutes to land]** → the card says the decision at once. The seed is walked from the next detection pass.
- **[The test environment cannot show the loop's resolution]** → `tg_pending_mentions` is cleared on restore and nothing collects there, so the copy offers channels only. The cards, the presses and their application are tried there in full.
- **[A press on a card from another environment]** → offer ids are per database. An id the database does not hold is answered «нет такого предложения», and nothing is written.
- **[Telegram renders no preview]** → the card still names and links the channel. The preview is a convenience, and its absence is not a failure.

## Migration Plan

1. Release, then deploy to the test environment. The revision creates the tables and the grants, the CronJob exists, and `itgraph-detect` has its third container. `scripts/on-host.sh offer --preview` prints what the copy would offer. A run of the CronJob sends the cards to the test chat. Pressing them, then waiting for the next detection pass, changes the copy's inventory.
2. Deploy to production. The next 11:00 sends at most five cards to the Telegram alert chat. Pressed seeds are walked within the hour.
3. Ask the infrastructure repository for the three alerts and a panel.

Rollback: revert the pin. The revision's downgrade drops both tables. A decision already applied is a review like any other.
