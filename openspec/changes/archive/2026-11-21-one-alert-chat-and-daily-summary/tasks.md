# Tasks

## 1. Settings and destinations: one chat, three topics

- [x] 1.1 Settings: add `telegram_alert_topic` (env `TELEGRAM_ALERT_TOPIC`),
      remove `youtube_alert_chat_id`, replace the YouTube-only validator with
      one all-or-none validator over the chat and the three topics, and update
      `.env.example`. Verify: settings tests for the four uneven combinations
      fail as the validator's message says.
- [x] 1.2 Destinations: `telegram_destination` carries the Telegram topic,
      `youtube_destinations` read the chat from `alert_chat_id`, and every
      destination names `ALERT_CHAT_ID` as its setting. Verify: destinations
      tests build all three from one chat and refuse a chat without its
      topics.
- [x] 1.3 `/status`: answer with both platforms' texts in one message, in the
      topic asked, dropping the chat-to-platform mapping. Verify: handler
      tests ask in the Telegram topic, the Russian one and General and get
      both platforms' state each time.

## 2. The day summary

- [x] 2.1 Day arithmetic: the day of a destination as
      `[quiet_to, next quiet_from)` in its zone, its label date, and the
      due test (due at `quiet_from`, once per day, no day when the window is
      empty). Verify: unit tests over zone boundaries, midnight-crossing
      windows and a bot restarting after the window's start.
- [x] 2.2 Selection: directly delivered alerts of the day window per
      destination (YouTube's per route), from `tg_alerts`/`yt_alerts` with
      evidence read as the queues read it. Verify: a fixture day with
      direct, held, folded and excluded alerts yields exactly the direct
      ones, sorted by publication time with the spike before the cascade of
      one post.
- [x] 2.3 Wording: the summary renderer in `bot/render.py` — the headline
      pieces shared with the alerts, the channel (YouTube: the video and its
      channel) with the post's clock time, the link, the dated header, parts
      through `message_parts`. Verify: rendering tests cover each kind, a
      post with two alerts, escaping, and a day longer than the message
      limit.
- [x] 2.4 Delivery: the pass beside `_maybe_digest` that renders, sends with
      `preview=False`, and records the marker only once every part has gone;
      silence on an empty day. Verify: delivery tests with a fake sender
      cover the first day, the marker blocking a restart, a failed part
      retried whole, and the cap untouched.

## 3. Schema, grants, restore

- [x] 3.1 Migration: the `bot_daily_summaries` table with its unique
      `(destination, day)` constraint and the bot's grants, following the
      rising-offers migration's pattern. Verify: the migration's upgrade and
      downgrade tests, and a grant test showing the bot can insert and close
      a marker and write nothing else of the table.
- [x] 3.2 Restore: `neutralise.sql` closes open markers, `readback.sql`
      fails a copy holding one, and the statement-removal test gains both.
      Verify: `make test` with each statement removed in turn fails as
      expected; the restore's trials deliver a synthetic alert to each of
      the three topics.

## 4. The role

- [x] 4.1 Inputs: `itgraph_telegram_alert_topic` required in
      `meta/argument_specs.yml`, `itgraph_youtube_alert_chat_id` removed,
      `tasks/inputs.yml` asserting the chat with all three topics, the bot's
      Secret carrying the chat id and the three topic ids unconditionally,
      and `deploy/ansible/inputs/{a,b,e2e}.yml` grown the Telegram topic.
      Verify: `make ansible-lint`, the role's input tests, and `make
      test-e2e` on the throwaway cluster.
- [x] 4.2 Docs: rewrite the runbook's «Чаты оповещений» for one chat and
      three topics, and the README/alerts notes that name two chats. Verify:
      no doc left describing the second chat, `make validate` green.

## 5. Operator: the infrastructure repository and Telegram

Done by the operator, outside this repository, after this change is released
and tagged; the order is design.md's («The infrastructure repository»).

- [x] 5.1 Create the topic «Telegram» in the alerts forum and take its id
      from a message link; then, in the infrastructure repository: the test
      vault's rename (`vault_itgraph_alert_chat_id` ← the forum chat,
      `vault_itgraph_telegram_alert_topic` ← the new topic, YouTube chat
      variable removed), the matching `group_vars/all/main.yml` wiring, and
      both `vault.yml.example` files with their comments. Commit, push, run
      `make itgraph vX.Y.Z` in the test environment, and confirm the three
      topics receive what design.md's steps 1–5 describe.
- [x] 5.2 Production: the same vault rename in the private inventory on the
      controller, then `make itgraph vX.Y.Z PROD=1`; once production's
      Telegram topic has received its first messages, delete the old
      «Telegram ITGraph Alerts» chat (design.md's steps 6–7).

## Workflow follow-up

- `/opsx:verify` once the code tasks are done, then `/opsx:archive` after
  the operator's steps 5.1–5.2 are confirmed in both environments.
