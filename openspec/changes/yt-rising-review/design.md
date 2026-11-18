# Design

## Context

See proposal.md for why. What shapes the approach:

- **The rankings exist and are never stored** (`yt-rising-candidates`): `derive/yt_rising.py` ranks; `db/yt_rising.py` loads the rows in one `REPEATABLE READ` transaction. A selection reads the same rows and stores only its choices.
- **Telegram's rising review is the blueprint.** `tg_rising_offer_days` / `tg_rising_offers`, `derive/offers.py`'s pure choice, the bot's claim-send-mark loop, the `rv:` callback, the detection job's application, the restore's closing and the three series all carry over; what differs is the topic routing, the language step and the record the selection creates.
- **The bot already delivers into the YouTube chat's topics** — the weekly lists go to the topic of their language, outside the caps and quiet hours, and a topic that refuses fails the send. The card rides the same path.
- **A day-one card cannot count on a payload.** The selection creates the candidate record without API data (as the queues' `--add` does), and the refresh fetches the payload at its next run. So the card links by handle when a payload carries one and by id otherwise, and forces the link preview: Telegram renders the channel's page — its name and face — which is the recognition the card needs. `defaultLanguage` is known for one payload in twenty, so the unknown-language path is the normal one, not the edge.
- **`itgraph yt mark` is the review's semantics** — a seed needs a kind and a language, and the language is exactly what the operator wanted to decide by hand when nothing tells it.

## Goals / Non-Goals

**Goals:**
- A reviewer sees a few rising channels a day in the topics they already read, and reviews each with at most three presses, as `itgraph yt mark` would.
- The bot gains no privilege over either inventory.
- A restored copy offers nothing from production and applies nothing of production's.
- The selection runs where no key is: a test environment tries the whole flow on its own copy.

**Non-Goals:**
- Notes beside a decision, changing a decision in the bot, reminders, offering a channel twice.
- Detecting a channel's language from its content.
- Anything the rankings own: `yt-rising-candidates`.

## Decisions

### 1. Two tables, Telegram's plus a place and a language

`yt_rising_offer_days`, one row per day the selection ran, an empty day included: `day` (the Moscow date, primary key), `computed_at`, `size_chart`, `size_links` — the two sizes it was computed with.

`yt_rising_offers`, one row per channel offered:

- `id`; `day` → the days; `channel_id` → `yt_channels`, **unique**: a channel is offered once, ever;
- `ranking` (`link` or `chart`), `region` and `position` for a chart offer, `families`, `families_before`, `first_in_window` for a link offer — nullable each, whichever the ranking fills;
- `discovered_via` — the source the record was created with;
- `topic` (`en` or `ru`) — where the card goes, decided at selection so the bot never re-derives it;
- `offered_at`; `delivered_at`, `attempts`, `last_error`;
- `decision` (`channel_status`), `kind` (`yt_channel_kind`), `language` (`yt_language`), `reject_reason` (`yt_reject_reason`), `decided_at`;
- `applied_at`, `applied`.

Checks, partial indexes and the unique channel key mirror Telegram's tables exactly; a decision is `seed`, `maybe` or `rejected`; a seed has a kind and a language, a rejection a reason, and nothing else has either.

### 2. The selection is pure, the command stores and creates records

`derive/yt_offers.py` holds `choose_offers`, the pure choice, and the two passes around it — the selection and applying decisions — beside Telegram's. The choice takes the link ranking, the chart ranking, the set of channels ever offered and the two sizes, and returns the offers in order: the link ranking's head first, then the chart's, each skipping channels the link side took and channels offered before, each size counting only its own.

`itgraph yt offer`:
1. loads the rankings' rows as `itgraph yt rising` does, as of now;
2. chooses the day's offers;
3. in one transaction: the day's row, the offers, and — for each offered channel no record holds — a `candidate` record, `discovered_via` set to `chart` for a chart offer and to the source of a link offer's strongest evidence (most families from that source; a tie `telegram`, `mention`, `featured`), `first_seen_at` now;
4. prints counts only.

A channel that gained a record between the ranking and the transaction is skipped by the same uniqueness that stops a re-offer. A second run on the day meets the day's primary key and says the day has its selection. `--preview` prints the day's offers by id and writes nothing; the CronJob never passes it.

The CronJob `itgraph-yt-offer` runs daily at 11:00 Moscow (`itgraph_yt_offer_schedule`), in every environment, holding the configuration and the database's URL and nothing else — the selection makes no request, so the test environment runs it on its restored copy into its own chat, as it runs Telegram's.

### 3. The card, and the topic it goes to

The bot's delivery pass gains a step for the YouTube destination, mirroring Telegram's offers and the weekly lists: claim the undelivered offers (oldest day, then rank order), read each one's channel, close it unsent if the channel is no longer a candidate or a maybe, otherwise render and send to the offer's stored topic, outside that topic's quiet hours, uncapped, never digested. A refused topic fails the attempt and nothing goes anywhere else. Order everywhere: claim, commit, send, mark.

The card, in HTML, everything escaped, the preview forced to the first link:

```
Link offer                                 (channel linked by handle or id)
📈 Растёт: <title or channel id>
3 семьи за 14 дней, 1 раньше · впервые в окне 10 ноября
Язык: не определён                          (only when nothing tells it; «язык: de» when it does and is neither)

Chart offer
📈 Чарт RU, позиция 4: <title or channel id>
🎬 <video id>                               (linked to the video; its preview is the card's face)
```

The title is read from the stored payload at sending and left out when there is none; the evidence numbers are the offer's own columns; the charting video is the channel's first video in the day's reading, its id read from the stored reading at sending.

### 4. A press is data the bot parses

Callback data is `yv:<offer id>:<press>`, at most 64 bytes. A press is one of `seed`, `reject` — show the second step; `back`; `maybe` — decide maybe; `kind:<value>`, `reason:<value>`, `lang:<value>` — decide.

The pure parts live beside Telegram's in `bot/review.py` (or a YouTube peer if the shared shapes pull apart): parsing, the keyboards, the card, the decision line. A deciding press writes only where `decision IS NULL AND delivered_at IS NOT NULL`, then redraws the card with the decision — «✅ В сиды · медиа · EN», «🤔 Может быть», «🚫 Отклонён · гаджеты» — and no buttons; a press on a decided card is answered «уже размечено». Presses are accepted only in the YouTube chat; the Telegram chat's `rv:` handler refuses YouTube-chat presses today, and the `yv:` handler refuses the Telegram chat the same way. The retired `^(useful|boring):` and the `rv:` scheme do not overlap `yv:`.

The seed flow: «🌱 В сиды» → the five kinds → the three languages, shown only while the offer's language is unknown or other than `en` and `ru` — a payload's `defaultLanguage` of `en` or `ru` fills it silently, and the decision line says so. Nothing about who pressed is stored or logged.

### 5. The labels

One to three words, a leading emoji, no trailing punctuation; a test holds every label to the rule, as Telegram's does.

| Press | Label | | Press | Label |
|---|---|---|---|---|
| seed | 🌱 В сиды | | not_it | 🙅 Не про IT |
| maybe | 🤔 Может быть | | adjacent | ↔️ Смежное |
| reject | 🚫 Отклонить | | crypto | 🪙 Крипта |
| back | ↩️ Назад | | infobiz | 💸 Инфобиз |
| personal | 👤 Личный | | ads | 📢 Реклама |
| company | 🏢 Компания | | content_farm | 🏭 Контент-ферма |
| media | 📺 Медиа | | consumer_tech | 📱 Гаджеты |
| event | 🎤 Событие | | en | 🇬🇧 EN |
| education | 🎓 Обучение | | ru | 🇷🇺 RU |
| | | | other | 🌐 Другой |

The shared reasons keep Telegram's labels, so the two chats read alike; the kinds, the languages and `consumer_tech` are YouTube's own.

### 6. The detection job applies both platforms' decisions

`itgraph apply-reviews` takes the decided, unapplied offers of both tables, oldest decision first, one transaction each: lock the channel; if it is still a candidate or a maybe, apply through the same code `itgraph yt mark` uses (setting `reviewed_at`, which orders the backfill walk of a new seed); otherwise record it not applied; either way set `applied_at`. A YouTube seed decided in the bot is walked like one marked from the terminal.

### 7. Roles

- **The bot:** `SELECT` on both tables; `UPDATE (delivered_at, attempts, last_error, decision, kind, language, reject_reason, decided_at)` on `yt_rising_offers`. Nothing on `yt_channels` beyond the `SELECT` it has. Role tests hold both halves, Telegram's pattern.
- **The exporter:** `SELECT (computed_at)` on the days; `SELECT (offered_at, delivered_at, decided_at, applied_at)` on the offers.
- **The reader:** both tables through its default privileges.

### 8. The restore

`files/neutralise.sql` gains two statements beside Telegram's:

```sql
UPDATE yt_rising_offers SET delivered_at = now() WHERE delivered_at IS NULL;
UPDATE yt_rising_offers SET applied_at = now(), applied = false
 WHERE decided_at IS NOT NULL AND applied_at IS NULL;
```

`files/readback.sql` gains a check for each, and the suite's test removes each statement in turn and expects the read-back to fail. A copy's days and applied decisions are history like any other; a test environment then computes and sends its own selection, from its own copy, into its own chat.

### 9. The series

- `itgraph_youtube_rising_offers_computed_timestamp_seconds` — the newest day's `computed_at`; 0 when none.
- `itgraph_youtube_rising_offers_oldest_undelivered_seconds` — now minus the oldest undelivered `offered_at`; 0 when none.
- `itgraph_youtube_rising_decisions_oldest_unapplied_seconds` — now minus the oldest unapplied `decided_at`; 0 when none.

The infrastructure repository is asked for the same three alerts Telegram's selection has.

### 10. Settings

`youtube_rising_chart_offers_per_region` (3, at least 1) and `youtube_rising_link_offers_per_day` (3, at least 1); the hour is the CronJob's schedule, `itgraph_yt_offer_schedule` (`0 11 * * *`, in `itgraph_timezone`).

## Risks / Trade-offs

- **[A day-one card has no title]** → it links the channel by id with the preview forced, which Telegram renders with the page's own name; the payload arrives with the next 05:00 refresh, and only the day's own offers are ever without one. The alternative — the selection fetching payloads — would put a key in the test environment's job or make the selection a requester, and the operator chose the keyless shape.
- **[The bot can rewrite a decision before it is applied]** → the handler writes only where `decision IS NULL`, Telegram's accepted pattern; at most nine channels a day, and only ones still unreviewed at application.
- **[A channel offered while its language is unknown lands in the English topic]** → the card says the language is unknown and the buttons ask it; the operator chose this over guessing, and over skipping such channels.
- **[Two selections at 11:00]** → Telegram's and YouTube's jobs run at the same minute as independent CronJobs; neither reads what the other writes.
- **[The test environment's chart offers]** → no key there, so no chart readings in the copy and the test's selections are link offers alone; the chart card and its routing are covered by the suite against fixtures.

## Migration Plan

1. Release after `yt-rising-candidates`, then deploy to the test environment. The revision creates the tables, the enum value and the grants; the CronJob exists beside Telegram's; `itgraph-detect`'s reviews container applies both. `scripts/on-host.sh yt offer --preview` prints what the copy would offer; a run of the CronJob sends the cards to the test chat, and pressing them changes the copy's inventory.
2. Deploy to production. The next 11:00 sends at most nine cards across the two topics. Pressed seeds are walked within the hour.
3. Ask the infrastructure repository for the three alerts and a panel.

Rollback: revert the pin. The revision's downgrade drops both tables and the enum value; a decision already applied is a review like any other.
