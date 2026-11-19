# Proposal

## Why

The operator reads two alert chats, and nothing that arrives in them is news
by the next morning: a viral post was reported once, as it happened, and the
only recap the bot sends is the morning digest of what quiet hours held —
which is a record of *delays*, not of the day. Two chats also split one
reading flow across two surfaces. This change closes the day in each topic
with a summary of everything that went viral in it, and merges the two chats
into one forum with a topic per audience — Telegram's beside YouTube's
English and Russian ones.

## What Changes

- **One chat for everything.** The YouTube forum chat becomes the only
  alert chat: a new topic of the operator's making receives everything
  Telegram's chat used to receive — its alerts, its weekly list and its
  rising offers. The separate Telegram chat is retired and deleted. The
  settings lose `youtube_alert_chat_id`; `alert_chat_id` names the one
  forum, and a new `telegram_alert_topic` joins `youtube_alert_topic_en`
  and `youtube_alert_topic_ru`, the four set together or not at all.
  **BREAKING** for the role's inputs: `itgraph_youtube_alert_chat_id`
  leaves, `itgraph_telegram_alert_topic` arrives, and the infrastructure
  repository's vault and wiring move with them (the operator's task; see
  design.md).
- **A daily summary per topic.** As soon as each destination's quiet window
  begins (`alert_quiet_from_hour`, `youtube_alert_quiet_from_hour` — 02:00
  today, the hour the collector's `itgraph_quiet_from_hour` also names),
  the bot sends to that destination a summary of the day that has just
  ended — the period from the previous `quiet_to_hour` to this
  `quiet_from_hour`. It lists every alert **delivered directly** in that
  window, one entry per alert, sorted by the post's publication time:
  the metric line an alert already uses, the channel (for YouTube, the
  video and its channel) with the post's clock time, and the link. No link
  preview is shown. A day with no directly delivered alert sends nothing.
  Held alerts are not repeated in it — the morning digest remains their
  summary — and neither are rising offers or weekly lists, which are not
  alerts. The summary counts towards no cap and is held by no quiet
  window; it is the one message the window's start itself sends.
- **`/status` answers for both platforms.** With one chat, the command can
  no longer infer the platform from the chat; asked anywhere in the chat,
  it answers with both platforms' state in one message, in the topic it
  was asked in.
- **A marker table for the summaries.** Which day's summary a destination
  has already received is kept in the database, not in the bot's memory,
  so a restart does not resend yesterday's summary. The bot's database
  role is granted exactly this table's delivery columns, and a restore
  neutralises the markers, as it closes everything else that would act on
  a restored copy.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `alert-delivery`: Telegram's destination becomes a topic of the one
  forum chat; new requirements for the daily summary — what it covers
  (directly delivered alerts of the day window, per destination), when it
  is due (at the quiet window's start, once per day, restart-safe), and
  what it says; `/status` answers for both platforms in the one chat.
- `youtube-alerts`: the topics requirement becomes "the one chat and its
  three topics"; the daily summary, per topic, on the same terms as
  Telegram's; each summary entry names the video and its channel.
- `collection-deploy`: the role's inputs change (`itgraph_telegram_alert_topic`
  in, `itgraph_youtube_alert_chat_id` out; the chat and all three topics
  together), and the bot's pod holds the chat id and three topic ids.
- `telegram-weekly-list`: the list goes to the alert chat's Telegram topic,
  not to a chat of Telegram's own.
- `telegram-rising-review`: offers go to the alert chat's Telegram topic.

## Impact

- `src/itgraph/config.py` — settings: `telegram_alert_topic` in,
  `youtube_alert_chat_id` out, the all-or-none validator, `.env.example`.
- `src/itgraph/bot/destinations.py`, `handlers.py`, `app.py`,
  `render.py`, a new daily-summary module beside them — destinations,
  `/status`, the summary pass and its wording.
- `src/itgraph/db/` — a migration for the summary marker table and the
  bot's grants on it; `neutralise.sql` and `readback.sql` gain their
  statements.
- `deploy/ansible/itgraph/` — defaults, vars, `meta/argument_specs.yml`,
  `tasks/inputs.yml`; the synthetic inputs under `deploy/ansible/inputs/`;
  the runbook's «Чаты оповещений».
- The infrastructure repository (operator's task, outside this one):
  vault variables of both environments, their wiring in
  `group_vars/all/main.yml`, both `vault.yml.example` files; the Telegram
  topic is created and the old chat deleted by hand.
- Tests: destinations, the summary's due/window/render logic, the
  migration's grants, the role's input validation, the restore's
  read-back.
