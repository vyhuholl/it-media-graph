# Design

## Context

See `proposal.md`, Why, and its decision record. The requirements are in `specs/`. What shapes the approach, read on 2026-11-11:

- **The bot today.** One chat: `BotSender` sends to `alert_chat_id`, and the dispatcher filters messages and button presses to it. One process runs the delivery loop beside aiogram's polling, under `supervise`. Delivery polls every 60 s; `LISTEN` is not wired. Claim → commit → send → mark, `FOR UPDATE SKIP LOCKED`, `BATCH = 10`.
- **Telegram's alerts in production, the 28 full days to 2026-11-11, Moscow time** (aggregates only):

  | | |
  |---|---|
  | heads raised a day | mean 10.1, median 8, p90 21, max 28, min 1 |
  | sent directly | 9.6 a day; the rolling cap of 20 reached on a burst day |
  | into the digest | 13 heads in 28 days: 11 over the cap, 2 raised in quiet hours; 7 digests, median 2, max 7 |
  | by hour | 16–20 h hold 126 of 271; 02–07 h hold 3, because the collector sleeps then too |
  | send failures, undelivered | 0, 0 |
  | delay over an hour | 37 heads, on 5 days; not investigated |
  | verdicts | 1 of 813 heads since 2026-08-08, given before its alert was delivered: a test |

- **The YouTube inventory:** 500 seeds in English, 169 in Russian, 25 in other languages. At `docs/PLAN.md`'s upload rates (0.33 and 0.58 a channel a day, measured at 175 seeds) that is about 165 and 98 videos a day; at Telegram's share of about 1.7% of posts, about 3 and 1.7 alerts a day. Change 7's replay sets the real volume. The YouTube poll loop runs every 15 minutes around the clock, so unlike Telegram it will raise alerts at night.
- **Two defects in `bot/app.py`.**
  - `digest()` joins up to a hundred rendered alerts into one message. `sendMessage` takes "1-4096 characters after entities parsing", and a spike with its excerpt is 350–500 characters. `_maybe_digest` logs a failed send and returns without counting an attempt or moving `last_digest_at`, so it fails again every minute, and the held alerts stay undelivered until `ItgraphAlertsStuck` at 26 hours. Not yet happened: the largest digest held seven.
  - `claim_undelivered(limit=10)` takes the oldest outstanding alerts, and `_deliver_due` skips those raised in quiet hours, which belong to the digest. Ten of them, and every pass claims the same ten and sends nothing until the digest; with the first defect, never. Telegram's nights hold at most two.
- **Bot API** (Context7, `core.telegram.org/bots/api`; aiogram 3.30 as locked):
  - `sendMessage.message_thread_id`: "Unique identifier for the target message thread (topic) of a forum; for forum supergroups and private chats of bots with forum topic mode enabled only".
  - `createForumTopic` needs an administrator with `can_manage_topics`: "True, if the user is allowed to create, rename, close, and reopen forum topics; for supergroups only". `closeForumTopic` the same, "unless it is the creator of the topic". `deleteForumTopic` deletes a topic "along with all its messages" and needs `can_delete_messages`.
  - How to send into General, and what a send to a closed or deleted topic answers, the reference does not say.
  - `deleteMessage`: "A message can only be deleted if it was sent less than 48 hours ago." `editMessageText` and `editMessageReplyMarkup` limit to 48 hours only "business messages that were not sent by the bot".
  - aiogram's `Message.answer` "Automatically fills chat_id, message_thread_id": a reply lands in the topic it answers.
  - `getChat` returns `is_forum`.
- **YouTube API Services Developer Policies and the derived-metrics page**, read 2026-11-11, quoted in fragments as the pages allow:
  - III.E.4.b: "must not store statistics retrieved as Non-Authorized Data for more than 30 days"; "must also verify, every 30 days, that the video has not been deleted."
  - III.E.4.d: "may temporarily store limited amounts of Non-Authorized Data for as long as is necessary" … "but not longer than 30 calendar days" … "must either delete or refresh the stored data."
  - III.E.4.f: "must display the most updated API Data available in their user-facing presentations"; "may display historical API Data provided that it is presented accurately in context of time."
  - III.E.4.h: "must not … (ii) access or use API Data to create new or derived data or metrics"; for metrics not from YouTube shown beside API Data, "must include a clear and prominent disclosure there that such information, data and metrics are not from YouTube" … "are part of your own product".
  - III.F.2.a: "must make clear to the viewer that YouTube is the source of the relevant content" … "by displaying YouTube Brand Features in accordance with the requirements below"; and one "cannot provide one general set of attribution for all search results from various sources".
  - III.D.3: "use case changes, to use any allocated quota for the new use case, you must notify YouTube" … "by resubmitting an API Compliance Audit and receive approval for the application".
  - Derived metrics: "Policy III E.4.b, E.4.c, and E.4.d are amended to allow accepted API Clients to store metrics" … "from statistical endpoints for up to 36 calendar months."; "Derived metrics (such as sentiment analysis) based on retrieved data may also be stored for up to 36 calendar months."; "Other data (such as video titles, creator names, descriptions, and comment text)" … "must still follow the 30-day refresh and deletion policy in Section III(4)."; "You must distinguish these metrics from metrics sourced from API Data."
  - The definitions define neither "store" nor "display", and nothing in the policies speaks of messaging services. The branding guidelines speak of logos and icons alone, require a logo to link back to YouTube content, and say nothing of attribution in text.
- **What already binds the queue.** `youtube-data-retention`, "API Data Has One Home": "What the system works out from a video's data SHALL be stored with that video's payload and removed with it", and no title or statistic copied into another table. Root `CLAUDE.md`: a payload is "deleted when YouTube stops returning the object", and no API data is in a backup. `yt_raw_video_stats` cascades from `yt_raw_videos`; readings expire on their own clock, 30 days or, after acceptance, 36 months.
- **Dumps** leave out the rows of `yt_raw_*`, `yt_backfill_walks` and `yt_search_runs` (`EXCLUDED_DATA`); `files/readback.sql` reads the same tables back as empty, by name, and a test holds both lists equal.

## Goals / Non-Goals

**Goals:**

- Change 7 adds a producer and touches nothing specified here: the queue holds what a YouTube spike needs, folds included.
- Telegram's delivery unchanged in what it says; its defects gone; its buttons gone.
- Every route tried by the end-to-end suite and by the restore in a test environment, with synthetic alerts.

**Non-Goals:**

- Raising a YouTube alert outside a test, or scoring.
- Per-topic values for the cap, the quiet window and the digest hour (Decision 6).
- Narrowing the bot's existing table-level grant on `alerts`.
- A push path (`LISTEN`) for either queue.

## Decisions

### 1. Destinations and routes

A **destination** is a chat and a topic, or a chat alone. A **route** says which destination an alert goes to:

```
alerts (Telegram) ─────────────────────────────────▶ the Telegram chat, no topic
yt_alerts ── the channel's yt_channels.lang, read when sending
              en    ──▶ the YouTube chat, topic EN
              ru    ──▶ the YouTube chat, topic RU
              other ──▶ none: closed as `excluded`, never sent
```

- **Read when sending,** for direct alerts and for every digest entry: a relabelling corrects a mistake, and the alert follows it. What the alert was sent through is recorded on it as `route` (`en`, `ru`), for `/status`, the cap and the series.
- **A channel no longer a seed** keeps its language, and its alert is delivered: it was raised while the channel was in scope.
- **`excluded`** is a delivery state of its own, written by the bot in place of a send. It is counted in `/status`, so "never sent" stays distinguishable from "excluded by its label".
- **No fallback.** A topic that cannot be reached fails the send like any other failure: attempts grow, `/status` and `itgraph_alerts_failing{platform="youtube"}` report it. Sending elsewhere would send where nothing is configured and mix the languages.
- **A check at start.** The bot asks `getChat` for every configured chat and refuses to start, naming the setting, when one is unknown or when the YouTube chat is not a forum. It cannot learn whether a topic exists without sending to it.
- **No YouTube configured** — the setting empty — and the bot routes no YouTube alert. One raised then stays undelivered, which the series reports. A test environment configures its own.

*Alternatives.* Three plain chats: the same code, other inputs; rejected because the readers of both languages are the same people. A route frozen when the alert is raised: a relabelled channel would keep a wrong topic.

### 2. Topics are the operator's, and the bot is no administrator

The operator makes the forum chat and its two topics and adds the bot as a member. The ids reach the role as inputs: a message link copied in a topic reads `https://t.me/c/<id>/<topic>/<message>`, and the chat id is `-100<id>`. The runbook says so.

*Alternative.* The bot creating its topics at start. It would need `can_manage_topics`, and a leaked token could then rename, close or delete them. The bot's database role is bounded for the same reason, and topic ids would become state in the database rather than inputs.

### 3. The YouTube queue

```
yt_raw_videos ──(video_id)──▶ yt_raw_video_stats ──(video_id, observed_at) ON DELETE CASCADE──┐
                                                                                             ▼
yt_alerts
  id            bigint  pk
  kind          yt_alert_kind          views_spike | like_spike | comment_spike
  video_id      text    ┐ (video_id, scored_at) → yt_raw_video_stats(video_id, observed_at)
  scored_at     timestamptz ┘          the reading it was scored on: a pointer, not a copy
  band          int
  value         float                  the z
  expected      float  null            a comment spike's expectation, as Telegram's
  raised_at     timestamptz
  held_at       timestamptz null       Decision 7
  delivered_at  timestamptz null
  delivery      yt_alert_delivery null direct | digest | folded | excluded
  route         yt_alert_route null    en | ru, written when sent
  attempts      int, last_error text
  folded_into   bigint null → yt_alerts(id) ON DELETE CASCADE
  unique (kind, video_id, band)
  partial indexes: undelivered by raised_at; folded_into
```

- **It lives as long as the reading it was scored on.** That reading goes with its video's payload, so a video YouTube stops returning takes its alerts at once, and expiry stays one `DELETE`. Before acceptance a reading lives 30 days; after it, 36 months, with its video refreshed meanwhile.
- **It copies nothing YouTube said.** No title, channel name or count; the count that crossed is read through the pointer when it is needed. `expected` and `value` are itgraph's numbers. Both exist only from change 7, after acceptance, which keeps derived metrics 36 months.
- **Out of every dump,** by name in `EXCLUDED_DATA` and in the read-back, as `yt_search_runs` is: a dump that kept it would keep video ids, and its foreign key could not be restored without the readings. Not `yt_raw_` by name, since none of it is API data; the exclusion is explicit.
- **Its own types.** `yt_alert_delivery` carries `excluded`, which Telegram's alerts can never be; a shared `alert_delivery` would make it valid there — `YtChannelKind`'s reasoning. `yt_alert_kind` names YouTube's three signals.
- **The kinds are declared now — not covered by the requirements.** `AlertKind` added its spikes only when something could raise them, since "a value nothing can raise is a promise made in a type". Here the wording of each kind is this change's, and the restore's trial and the tests raise all three. Leaving them to change 7 would ship a renderer whose branches nothing exercises.
- **Notification:** change 7 raises through the same `itgraph_alerts` channel; nothing listens yet.

*Alternatives.* A table of its own with a 30-day clock: a video deleted upstream would stay named for up to 30 days, against root `CLAUDE.md`, and an accepted alert would be cut at 30 days instead of 36 months. The verdict kept apart without the video id, in dumps: moot once the buttons are gone, and it would have kept a derived metric past its video's data.

### 4. What a YouTube alert says

```
▶️ YouTube · EN
<b>Video title</b> — Channel title · 5 ч после публикации
https://www.youtube.com/watch?v=VIDEO_ID
Данные YouTube на 14:05 MSK: просмотры 12 400 · лайки 830 · комментарии 95
📈 Оценка itgraph (рассчитана itgraph, не метрика YouTube) на 10:15 MSK: просмотры — намного выше обычного для канала (z 4.2)
📶 Вместе с ним выше нормы ещё 2 видео канала — …      (only when folds exist)
```

- **YouTube is named on each alert, and on each entry of a digest** (III.F.2.a: no "general set of attribution"), by its name in text, as a link to the video. The link preview stays on, so Telegram shows YouTube's own card. A logo is not possible in a text message.
- **Counts:** the newest stored reading at the moment of sending — direct or in a digest — with its time (III.E.4.f, "most updated"; "in context of time"). A counter YouTube did not report is left out, never written as zero.
- **The score** with its own moment, `scored_at`, and the disclosure III.E.4.h and the derived-metrics page ask for. The magnitude in words, by `_magnitude` as Telegram's; a comment spike states its count, read through `scored_at`, and its expectation.
- **The age** from the broadcast's actual start when it has one, else publication — `derive/yt_videos.facts_of`, the schedule's rule.
- **The channel's title** comes from its payload; the site lists it among an alert's contents from this change. A payload older than 30 days is treated as absent — expiry may run late, and the bot cannot delete — and the line goes without it. A video payload older than that is not rendered: the alert is skipped as gone, as a vanished Telegram post is.
- **Russian in both topics — not covered by the requirements:** the readers are Telegram's.
- **Telegram's wording is unchanged.** Every rendered alert is cut to fit one message — a YouTube title is at most 100 characters, so this holds by construction and a test pins it.

### 5. A sent message is display

The operator's decision. III.E.4.f allows historical data "presented accurately in context of time", which is why the counts and the score each carry their moment (Decision 4). Nothing is deleted or edited later. `docs/index.html` says today "Data read from YouTube is refreshed or deleted within 30 days", and is reworded to say what itgraph stores; `docs/privacy.html` adds that alert messages stay in the chat as sent.

### 6. Each destination keeps its own account

The cap counts the destination's direct sends over the past 24 hours, from its queue's rows (`route` for YouTube). Quiet hours hold what the destination's window holds. The digest goes out at the destination's hour in its zone, once a day each. A burst on Telegram no longer spends YouTube's cap, nor English Russian's.

**The values are set per platform — not covered by the requirements.** Telegram keeps `alert_daily_cap`, `alert_quiet_from_hour`/`_to_hour`, `alert_digest_hour` and `watch_timezone`. YouTube's two topics take `youtube_alert_daily_cap` (10), `youtube_alert_quiet_from_hour`/`_to_hour` (2, 7), `youtube_alert_digest_hour` (9) and `youtube_alert_timezone` (`Europe/Moscow`), each topic counting and sending on its own. A value per topic would be settings nobody sets while the readers are one group in one zone; the settings validator refuses a digest hour inside either quiet window, as today.

At Moscow's quiet hours, a large share of English alerts may be raised at night and arrive in the 09:00 digest. YouTube's views accrue over days, so a few hours cost little.

### 7. Held alerts, and the digest in parts

- **`held_at`** on both queues, written by the bot: the first time a claimed alert is found raised in its destination's quiet window, or over its destination's cap, it is marked held. Direct sending claims `delivered_at IS NULL AND held_at IS NULL AND folded_into IS NULL`; the digest claims the held ones of its destination. Held alerts no longer stand in the queue's head, so the second defect cannot happen. An alert once held belongs to the digest even when the cap frees up, which is what "held for the digest" says to a reader.
- **The digest takes every held alert of its destination**, not a hundred, and sends it in parts. Each part is at most 4,096 characters with entries kept whole, the first carrying the count of the whole: «📋 За прошедший период — 23 оповещения (1/3)». Each part's alerts are marked delivered as `digest` when it is sent; a failed part counts a failed attempt on each alert in it and is tried again on the next pass. `last_digest_at` moves only when every part went out. This fixes the first defect.
- **`/status`** says how many wait for the digest and how many for a send. The series count both as undelivered.
- **Restored copies:** `neutralise.sql` already marks every undelivered Telegram alert delivered, held ones included.

*Alternative.* Filtering quiet-raised alerts in the claim's SQL by the hour in the destination's zone. It works for Telegram, and for YouTube needs the route — a join through the video to the channel's language — inside a `FOR UPDATE SKIP LOCKED`, and it would still let cap-held alerts block the head.

### 8. Feedback removed

- **One revision drops `alert_feedback` and `alert_verdict`.** Its downgrade recreates both empty and the bot's grant on the table. The full dump every migration takes first holds the one row.
- **Gone:** `verdict_keyboard`, the `reply_markup` on sends, `record_verdict`, `AlertFeedback`, `AlertVerdict` and their tests.
- **The docstrings that gave buttons as the reason for keeping `value`** say what it is kept for now: the number that crossed, which a replay compares against.
- **Old buttons.** 812 messages carry them. A press matching `^(useful|boring):\d+$`, from a configured chat, is answered «отзывы больше не собираются» and the keyboard is removed from that message, which the API allows at any age for the bot's own messages. Unanswered, a client shows the press loading.

### 9. Chats and commands

- **Messages and presses are filtered to the configured chats**, in any topic, General included: `F.chat.id.in_(…)`, applied to the dispatcher as today, so a later handler inherits it.
- **`/status` answers about the platform of its chat**, in the topic it was asked in. Telegram's report is unchanged, plus held and excluded counts. YouTube's: alerts raised, folded and excluded, and per route undelivered — held and not — and stuck (an excluded alert has no route); the last poll pass that completed; the quiet window; and, while `DERIVED_METRICS_ACCEPTED_ON` is empty, «оценок YouTube нет до одобрения».

### 10. Grants

- **The bot's role:**
  - `SELECT` on `yt_channels`, `yt_raw_channels`, `yt_raw_videos`, `yt_raw_video_stats`, `yt_watch_passes`, `yt_alerts`;
  - `UPDATE` on `yt_alerts` (`held_at`, `delivered_at`, `delivery`, `route`, `attempts`, `last_error`) alone — no `INSERT`, as nothing it does creates an alert;
  - `alert_feedback`'s grants go with the table.
- **The exporter's role:** `SELECT` on `yt_alerts` (`raised_at`, `held_at`, `delivered_at`, `delivery`, `attempts`, `folded_into`) — not which video, nor the score.
- **The reader** reads the new table through its default privileges, with no migration of its own.

### 11. The series

`itgraph_alerts_oldest_undelivered_seconds` and `itgraph_alerts_failing` gain `platform`, `telegram` or `youtube`, constants. The infrastructure repository's rules on them keep matching and fire per platform; their messages should name it. No new series: an excluded alert is the label working, not a fault.

### 12. Backups and the restore

- `EXCLUDED_DATA` and `files/readback.sql` gain `yt_alerts`; the existing test holds them equal.
- The trial of `tasks/trials.yml` raises, besides Telegram's synthetic alert, one YouTube alert per configured topic, where the environment configures YouTube:
  1. picks a restored seed of that language;
  2. inserts a synthetic video payload, its reading and the alert — after the read-back, so the copy was proven empty first;
  3. waits for the delivery and its `route`;
  4. deletes the synthetic video, which takes the reading and the alert with it.

  Inside the destination's quiet window it reports "could not be proven", as Telegram's does.

### 13. Inputs and the bot's Secret

| Input | Type | Required |
|---|---|---|
| `itgraph_youtube_alert_chat_id` | int; 0 is none | no, default 0 |
| `itgraph_youtube_alert_topic_en`, `itgraph_youtube_alert_topic_ru` | int; 0 is none | no; both non-zero exactly when the chat is |

`tasks/inputs.yml` refuses a topic without the chat and a chat without both topics. When set, the bot's Secret carries `YOUTUBE_ALERT_CHAT_ID`, `YOUTUBE_ALERT_TOPIC_EN` and `YOUTUBE_ALERT_TOPIC_RU`, and its checksum restarts the bot alone. A test environment may set them: they reach its own chat. `inputs/a.yml`, `b.yml` and `e2e.yml` get synthetic values unlike each other's. `config.py` refuses the same incoherence at load.

### 14. The end-to-end suite

- **The Bot API stand-in** numbers its messages, answers `getChat` with `is_forum` per configured chat, answers `editMessageReplyMarkup`, and refuses one topic id as a deleted topic would. It also serves updates queued by the suite — one `/status` in a topic, one press on an old button — instead of always an empty `getUpdates`.
- **`e2e.yml`** configures the YouTube chat and its topics. The suite seeds a Telegram alert and YouTube alerts on `en`, `ru` and `other` seeds, with synthetic videos and readings, and runs with quiet hours empty.
- **Checks:**
  - each `sendMessage`'s `chat_id` and `message_thread_id` — Telegram's with none;
  - the `other` alert `excluded` with nothing sent;
  - a twelve-alert digest arriving in parts, each under 4,096 characters;
  - the deleted topic's alert failing with `itgraph_alerts_failing{platform="youtube"}` at 1;
  - `/status` answered in its topic;
  - the old button's keyboard removed;
  - the restore's trial on the YouTube routes.

### 15. What the infrastructure repository is asked

Handed to the operator once this design is accepted. Nothing in it names that repository or holds a value.

1. **Inputs per environment:** `itgraph_youtube_alert_chat_id`, `_topic_en`, `_topic_ru`. Production's are kept with its other inputs on the controller; the test environment's name its own forum chat and topics, reached by its own test bot.
2. **Rules:** `ItgraphAlertsFailing` and `ItgraphAlertsStuck` name `platform` in their message; the expressions stay.
3. **Dashboard «itgraph»:** undelivered alerts per platform.

### 16. Found in implementation

- **A channel with no language to route by is excluded too — not covered by the requirements.** A label cleared by a later review, or a video YouTube now attributes to a channel outside the inventory, has neither `en` nor `ru` to send by. It is closed as `excluded`, as `other` is. Left outstanding, it would wait for ever and read as delivery being stuck.
- **What never failed is claimed before what did.** Both queues order a claim by whether the alert has failed, then by when it was raised. A topic Telegram keeps refusing, or a message it will never take, then stands behind fresh alerts rather than in front of them: the same failure as the held alerts of Decision 7, by another road.
- **Everything taken from a post, a channel or a video is escaped,** Telegram's wording included. Messages are HTML: a `<` in a post's excerpt, or `&` in a channel's or video's title, would be refused on every retry. Nothing so far tripped it in Telegram's 813 alerts; YouTube's titles would have.
- **"Never attempted" leaves held alerts out,** and `/status`'s warning that delivery is not running looks only at alerts not held: a quiet night's held alerts are waiting by design.
- **The bot's modules:** `bot/destinations.py` (where, and each destination's account), `bot/queues.py` (the two queues behind one interface), `bot/status.py` (`/status` per platform), `db/yt_alerts.py` (YouTube's queue and the evidence its message reads).
- **Observed, not changed:** the exporter's engine does not ping its pooled connections, so the first scrape after the database is dropped and recreated — a restore — answers 503 once, and the next succeeds. The end-to-end suite now retries its scrape, as the scraper does.

## Risks / Trade-offs

- **[YouTube reads a chat message as storage]** The policies define neither word, and the site will say messages stay. → Should YouTube ask, deleting the two topics removes every message in them whatever their age, by `deleteForumTopic`, given the right; nothing kept would find single messages to edit. Accepted by the operator.
- **[A topic closed or deleted by hand]** → The send fails loudly, with no fallback (Decision 1). How the API words it is probed in test (tasks.md, 12.3).
- **[No delivery to try in production before change 7]** → Every route is tried in the end-to-end suite and in each test restore. Production proves the inputs and the check at start.
- **[A leaked bot token reads YouTube's API data]** The bot's role gains `SELECT` on payloads to render them. → It can write only delivery columns. What it reads lives at most 30 days, and is what the chat receives anyway.
- **[A night's English alerts arrive in one morning digest]** → In parts, so it arrives; the timing is the value of the quiet window. Change 7 can move the window with its measured volume.
- **[Dropping `alert_feedback`]** removes a table from production. → It holds one test row, the migration's dump keeps it, and the downgrade recreates the table.
- **[Kinds declared ahead of their producer]** → Raised by the trial and the suite. If change 7 measures likes as useless, it removes a value — a type recreated under an empty table.

## Migration Plan

**Irreversible steps are in bold.**

1. **Agent:** the tasks; `make validate`, `make test-e2e`, `openspec validate --strict` green.
2. **Operator:** push `master`, tag a release. **A published release cannot be changed.**
3. **Operator, test environment:**
   - a forum chat with two topics and the test bot as a member; the inputs in the test inventory;
   - the role at the release; a restore — the trial delivers to Telegram's chat and to both topics;
   - the probes of tasks.md, 12.3.
4. **Operator:** the request of Decision 15.
5. **Operator, production:**
   - the forum chat, its two topics and the bot; the inputs on the controller;
   - promote the pin and run the role. The migration takes its full dump, then **drops `alert_feedback`**;
   - `/status` in both chats; a press on an old button.
6. **Operator:** read a week of Telegram's delivery for the held and the parts.

**Rollback:**

- Revert the pin. The run then fails at the migration: the older image's Alembic does not know the newer revisions.
- Going back for good means `alembic downgrade` on production after a dump. It drops `yt_alerts`, empty before change 7, and recreates `alert_feedback` empty, under the guard's override (root `CLAUDE.md`).
- Nothing sent is affected either way.

## Open Questions

- The API's answer to a send into General without a topic id, into a closed topic, and into a deleted one: probed in test, recorded in tasks.md. Only the wording of the failure depends on it.
