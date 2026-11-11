# Proposal

## Why

`docs/PLAN.md` orders YouTube's alerts into "a Telegram chat separate from the Telegram one", and the bot today knows one chat. The explore of 2026-11-11 also found two defects in the delivery loop that YouTube's round-the-clock polling would turn from latent into regular, and buttons nobody presses.

## What Changes

- **A queue for YouTube alerts**, `yt_alerts`, apart from Telegram's `alerts`, which references `raw_messages`.
- **Destinations.** The bot sends to a destination — a chat, and a topic or none. Telegram's alerts keep their chat. YouTube's go to one forum chat with a topic for English and one for Russian, chosen by the channel's language at the moment of sending; channels labelled `other` get no alerts, and an alert whose channel became `other` while it waited is closed as excluded, counted, never sent. A channel no longer in scope still has its alert delivered.
- **Each destination keeps its own account:** its daily cap, quiet window and digest. A burst on one platform no longer spends another's cap.
- **A YouTube alert's wording:** YouTube named as the source on every alert and every digest entry, the video's title and channel, a link to it on YouTube, the counts YouTube reports with the time they were read, how old the video is, and itgraph's score, labelled as calculated by itgraph and not a YouTube metric, with the moment it was calculated. Russian, as Telegram's.
- **A sent message is display, not storage** (the operator's decision): the bot leaves its messages as sent, and the site says so.
- **Fixed: a digest longer than Telegram's 4,096 characters** failed forever and kept every held alert undelivered. It is sent in parts, every held alert in one of them, and a failed part counts as a failed attempt for each alert in it.
- **Fixed: ten alerts held overnight blocked every direct alert** until the morning digest. Held alerts are marked as held and are no longer what the direct path claims.
- **BREAKING: the feedback buttons are removed**, with the `alert_feedback` table and the `alert_verdict` type. In three months of production one verdict was given, a test. A press on a button under an old message is answered once and its keyboard removed.
- **The bot answers in the chats it is configured for**, in any topic of them; `/status` reports on the platform of the chat it is asked in.
- **Operations:** the bot's database role reads what a YouTube alert renders from and updates only delivery columns; the exporter's series of undelivered and failing alerts gain a `platform` label; the role takes the YouTube chat and its two topics as inputs, the restore delivers a synthetic alert to every destination of the test environment, and the end-to-end suite checks every route.
- **Docs:** `docs/index.html` and `docs/privacy.html` say where alerts go, what they contain, what happens to the messages and that the bot answers in several chats; `docs/PLAN.md`'s "### Alerts" records what was decided; the runbook says how a chat and its topics are made and named to the role.

**Out of scope:**

- A signal of YouTube videos linked by Telegram families: a change of its own, after its rate is measured.
- Deleting or editing sent messages.
- Why direct delivery took over an hour on five days of the last 28 (37 alerts): observed, not investigated.

## Capabilities

### New Capabilities

- `youtube-alerts`: the YouTube alert queue — what an alert names, how long it lives, how it is routed by language, and what its message says.

### Modified Capabilities

- `alert-delivery`: destinations instead of one recipient; the cap, quiet hours and digest kept per destination; held alerts marked, and no longer blocking direct ones; the digest sent in parts; feedback removed; the bot's commands answered in its chats, its writes confined to the delivery columns of both queues.
- `collection-monitoring`: the alert series carry the platform; the exporter cannot read which video an alert is about.
- `collection-deploy`: the bot's pod holds the chat and topic ids of its destinations.
- `database-backups`: the restore's trial delivers an alert to every destination of the environment.

## Impact

- **Code:** `bot/` (destinations, routing, accounting per destination, the digest in parts, the held marker, YouTube's wording, chat filter, `/status`, the old buttons, a check of the chats at start), `db/alerts.py` and a new `db/yt_alerts.py`, `db/models.py`, `config.py`, `metrics/series.py`, `db/backup.py`, `notebooks/channel_report.py`'s comments. No new dependency.
- **Schema:** two Alembic revisions. One adds `alerts.held_at` and drops `alert_feedback` and `alert_verdict`; one adds `yt_alerts` and its types, and the grants of the bot's and the exporter's roles.
- **Role:** three optional inputs, the bot's Secret, the input checks, the synthetic input sets, `files/readback.sql`, the restore's trial, `e2e.sh` and its Bot API stand-in.
- **Infrastructure repository:** the inputs in each environment's inventory, a forum chat with two topics per environment, the alert rules' messages per platform, the dashboard.
- **Production:** no YouTube alert arrives until change 7. Telegram's alerts arrive without buttons, and on a day of many alerts the night's held ones no longer delay the day's.

## Decision record

Brought as thirteen questions in the explore session of 2026-11-11, on the Telegram alerts' aggregates of the last 28 days. The operator's answers:

> 1. A, читатели одни и те же. other исключаем вообще. Маршрут берётся по языку в момент отправки. Eсли канал перестал быть сидом, оповещение доставляем.
> 6. Считать показом, ничего не делать.
>
> По остальным вопросам — делай всё как ты рекомендуешь. Чини оба бага доставки в этом change. А, и кстати, про кнопки отзыва, раз уж ты их упомянул. Они не нужны, их, по-хорошему, надо бы убрать. Давай их уберём тоже в этом change.

Then, to four follow-up questions with recommendations — what "excluded" covers, an alert whose channel turns `other` in the queue, the feedback table, the buttons already sent: «Всё как рекомендуешь. Создавай.»

Confirmed as of 2026-11-11:

1. Two chats: Telegram's as it is, and a forum chat for YouTube with an English and a Russian topic; the same people read both topics.
2. Channels labelled `other` get no alerts: their videos are not to be scored or queued (recorded for change 7); collection, the history walk and the mention graph keep reading them.
3. The destination is the channel's language at the moment of sending. An alert whose channel turned `other` is closed as excluded, counted and not sent. An alert whose channel stopped being a seed is delivered.
4. Topics are made by the operator and named to the role; the bot is no administrator; a topic it cannot reach fails the send, with no fallback.
5. `/status` answers about the platform of its chat.
6. The cap, quiet window and digest are kept per destination, by default Telegram's, the YouTube topics' cap 10 a day.
8. A message in the chat is display: nothing is deleted or edited later.
12. Both delivery defects fixed here.
13. The feedback buttons, the table and its type removed; a press on an old button is answered and its keyboard taken off.

**Not covered by the requirements** — chosen in `design.md`, not by the operator:

- The YouTube alert kinds are declared now — views, likes, comments, the three signals `docs/PLAN.md` names — rather than with change 7, against `AlertKind`'s precedent (design.md, Decision 3).
- The values of the cap, the quiet window and the digest hour are set per platform, and both YouTube topics take YouTube's; each destination still counts and sends on its own (design.md, Decision 6).
- Messages are Russian in both topics, since their readers are the same people as Telegram's (design.md, Decision 4).
- How the Bot API answers a send to General without a topic id, to a closed topic and to a deleted one is undocumented, and is probed by the operator in the test environment (tasks.md, 12.3).
- A channel with no language to route by — its label cleared, or a video attributed to a channel outside the inventory — is excluded as `other` is (design.md, Decision 16).
