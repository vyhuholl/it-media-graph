# Tasks

Tasks marked **(operator)** reach a server, a chat or the test environment, and are the operator's: the agent sends no request to the Bot API or YouTube and starts no bot. Every task that changes code ends on a green `make validate`; every task that changes the role also on `make test-e2e`. Migrations are verified on a scratch database whose name ends in `_test`, never on the working one. Fixtures are synthetic: no real chat, topic, video or channel id, title or count, and fake ids spelled as obviously fake.

## 1. Feedback removed

- [x] 1.1 Revision A: drop `alert_feedback` and the type `alert_verdict` with the bot's grant on the table; add `alerts.held_at`, nullable. The downgrade recreates both empty with the grant, and drops the column. Verify on a `_test` database: upgrade, downgrade, upgrade; Telegram alerts read back unchanged; autogenerate reports nothing.
- [x] 1.2 Remove `verdict_keyboard`, the `reply_markup` on sends, `record_verdict`, `AlertFeedback`, `AlertVerdict` and their tests; reword the docstrings that gave buttons as the reason for keeping `value` (design.md, Decision 8). Verify: `git grep` finds none of the five names outside migrations and the archive; a test that no send of either platform, alone or in a digest, carries a keyboard.
- [x] 1.3 The handler for old buttons: a press matching `^(useful|boring):\d+$` from a configured chat is answered «отзывы больше не собираются» and its message's keyboard removed; nothing is written. Verify: tests per scenario of `specs/alert-delivery`, "Buttons Under Old Alerts Are Retired", against a fake bot, and that a press from another chat does nothing.
- [x] 1.4 Docs of this group: `src/itgraph/README.md`'s paragraphs on the buttons and verdicts; `notebooks/channel_report.py`'s comments on `alert_feedback`; `docs/privacy.html`'s sentence on verdicts. Verify: `git grep -i verdict` and `git grep alert_feedback` find only the archive and migrations.

## 2. The two delivery defects

- [x] 2.1 The held marker on Telegram's queue: a claimed alert raised in the quiet window, or over the cap, is marked `held_at`; direct sending claims only alerts neither held nor folded; the digest claims the held ones (design.md, Decision 7). Verify: tests of "A Held Alert Does Not Delay Others" — twelve held overnight and one at 08:00 sent at once; a cap-held alert not sent directly when the period frees up; Telegram's existing delivery tests unchanged.
- [x] 2.2 The digest in parts: every held alert, not a hundred; parts of at most 4,096 characters with entries whole, the first stating the count of the whole; each part's alerts marked when it is sent; a failed part counts an attempt on each of its alerts; `last_digest_at` moves only when every part went out. Every rendered alert is bounded under 4,096. Verify: tests of "A Summary Arrives Whatever Its Length", including a thirty-alert digest whose second part fails, and a rendered alert at the longest title and excerpt.
- [x] 2.3 Telegram's `/status`: held and waiting counted apart. Verify: a test of "The Bot Reports Its Own State", "Quiet is distinguishable from broken"; `src/itgraph/README.md`'s delivery section describes held alerts and digests in parts.

## 3. Destinations

- [x] 3.1 `config.py`: `youtube_alert_chat_id`, `youtube_alert_topic_en`, `youtube_alert_topic_ru`, `youtube_alert_daily_cap` (10), `youtube_alert_quiet_from_hour`/`_to_hour` (2, 7), `youtube_alert_digest_hour` (9), `youtube_alert_timezone` (`Europe/Moscow`). The chat and both topics are all set or none; a digest hour inside YouTube's quiet window is refused; a blank value is unset. Verify: tests of "YouTube Delivery Needs Its Chat And Both Topics", "A chat without its topics", and of the digest refusal.
- [x] 3.2 Destinations in the delivery loop: Telegram's chat, and YouTube's two topics when configured, each with its own cap, quiet window and digest, counted from its queue's rows (design.md, Decisions 1 and 6). Verify: tests of "Each Destination Keeps Its Own Account" and of the modified "Nothing Is Silently Withheld" and "Quiet Hours Hold Rather Than Drop"; Telegram's messages and timing unchanged.
- [x] 3.3 The check at start: `getChat` for every configured chat; refuse to start, naming the setting, when a chat is unknown or the YouTube chat is not a forum. Verify: tests of "The Bot Starts Only With Chats It Can Reach" against a fake bot.
- [x] 3.4 The dispatcher filtered to the configured chats, in any topic; `/status` answers in the topic it was asked in. Verify: tests of "The Bot Reaches Only The Operator" — a stranger ignored, General of the YouTube chat answered.

## 4. The YouTube queue

- [x] 4.1 Revision B: types `yt_alert_kind`, `yt_alert_delivery`, `yt_alert_route`; table `yt_alerts` as design.md, Decision 3, with the foreign key to the reading `ON DELETE CASCADE`, the self-reference for folds, the unique constraint and the two partial indexes. Grants: the bot's role `SELECT` on `yt_channels`, `yt_raw_channels`, `yt_raw_videos`, `yt_raw_video_stats`, `yt_watch_passes`, `yt_alerts` and `UPDATE` on `yt_alerts`' delivery columns; the exporter's `SELECT` on `yt_alerts`' moments and delivery state. The downgrade revokes and drops. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; grant tests under `SET LOCAL ROLE` — the bot cannot insert an alert or change its video, reading, kind or score, the exporter cannot select the video, the reading or the score.

## 5. YouTube routing and wording

- [x] 5.1 Routing when sending, by the channel's current language; `other` closed as `excluded`; a channel no longer a seed delivered; a digest entry routed when the digest is sent; the route recorded. Verify: tests per scenario of "Alerts Are Routed By The Channel's Language When Sent" and "Channels In Other Languages Get No Alert".
- [x] 5.2 `bot/render.py`: the YouTube alert and digest entry of design.md, Decision 4, pure. Verify: tests per scenario of "A YouTube Alert Names Its Source And Dates What It Shows" — attribution and link on every entry, the counts' time and the score's time, a counter left out rather than zero, a broadcast's age, a comment's count and expectation, the folds' line — and Telegram's wording unchanged.
- [x] 5.3 No YouTube configured: nothing sent and the alert counted undelivered; a topic refused by Telegram: an attempt counted, nothing sent elsewhere. Verify: tests of "YouTube Delivery Needs Its Chat And Both Topics", "Nothing configured", and "A Topic That Cannot Be Reached Fails The Send".
- [x] Verify: a test of "The Bot Reports Its Own State", "The YouTube chat" and "Asked in a topic".
- [x] 5.5 Docs of this group:
  - The wording is proposed, and the operator words both pages before archiving.
  - `docs/PLAN.md`'s "### Alerts" with what was decided and the aggregates of design.md, Context; the row of change 6, and for change 7 that `other` is not scored.
  - `src/itgraph/CLAUDE.md`'s rows for the modules touched and added; `src/itgraph/README.md`'s bot section.

  Verify: every claim on the pages holds in the code and the specs, and every figure in PLAN is one in design.md.

## 6. Metrics

- [x] 6.1 `itgraph_alerts_oldest_undelivered_seconds` and `itgraph_alerts_failing` with `platform`, `telegram` or `youtube`, held alerts counted, excluded ones not.

## 7. Backups and the restore

- [x] Verify: the existing test holding the two lists equal; a test that a full dump holds the table's definition and no row; a restore of a dump carrying an alert fails naming it, in the role's tests.
- [x] 7.2 `tasks/trials.yml`: one synthetic YouTube alert per configured topic on a synthetic video of a restored seed of that language, after the read-back; waits for its delivery and route; removes the synthetic video at the end, the alert and the reading going with it; says "could not be proven" inside a quiet window, and that YouTube delivery is not configured where it is not. Verify: the role's tests against a stub `kubectl` for both configurations; `deploy/runbook.md`'s restore section.

## 8. The role's inputs

- [x] 8.1 `itgraph_youtube_alert_chat_id`, `itgraph_youtube_alert_topic_en`, `itgraph_youtube_alert_topic_ru` in `meta/argument_specs.yml`, default 0; `tasks/inputs.yml` refuses a chat without both topics and a topic without the chat; the bot's Secret carries the three keys only when given; `inputs/a.yml`, `b.yml` and `e2e.yml` hold synthetic values unlike each other's. Verify: `tests/test_deploy_role.py` — the refusals before any change, the bot's pod with and without them, the collector without any chat id, no value of one set in another's objects, a changed topic restarting the bot alone; `make ansible-lint`.
- [x] 8.2 `deploy/runbook.md`: making the forum chat and its two topics, adding the bot as a member and no administrator, reading the ids from a message link, naming them to the role, and what the bot does when a topic is gone. Verify: the runbook names no real id, host or chat.

## 9. The end-to-end suite

- [x] 9.1 `deploy/ansible/e2e_telegram.py`: numbered messages; `getChat` answering `is_forum` per configured chat; `editMessageReplyMarkup`; one topic id refused as a deleted topic; updates queued by the suite served by `getUpdates`. Verify: run alone, its answers match the Bot API's shapes used by aiogram.
- [x] 9.2 `deploy/ansible/e2e.sh`: the checks of design.md, Decision 14 — every route's `chat_id` and `message_thread_id`, Telegram's without a topic; the `other` alert excluded with nothing sent; a twelve-alert digest in parts; the refused topic's alert failing with the series at 1; `/status` answered in its topic; the old button's keyboard removed; the restore's trial on both topics. Verify: `make test-e2e` green.

## 10. Verification

- [x] 10.1 `make validate`, `make test-e2e`, `openspec validate route-alerts-by-platform --strict`. Verify: their output recorded here.

  2026-11-11: `make validate` — ruff and mypy clean, 2269 passed, ansible-lint 0 failures; `make test-e2e` — 64 of 64, the restore's trial delivering to the Telegram chat and to both YouTube topics, the routes section passing; `openspec validate --strict` valid. Both revisions verified on a `_test` database: upgrade, downgrade to `d629dc2e11d0`, upgrade, `alembic check` clean.

  The first two e2e runs failed on the suite's own checks, not on the code: the stand-in records refused sends as well, which read as "sent elsewhere"; and the first scrape after the suite drops and recreates the database meets a connection the drop ended, which the exporter answers 503 once, as it would before this change. Both checks were corrected: sends other than to the refused topic, and a scrape tried up to three times, as the scraper retries.

  After `/opsx:verify` (2026-11-11), whose answer was «Поправь.»:
  - the YouTube `/status` scenario and 5.4 say what an excluded alert allows — raised, folded and excluded overall, and per route undelivered, held and failing;
  - tests added for one alert per kind, video and band, for an alert's reading having to exist (both on the migrated schema), for a comment alert's count coming from the reading it was scored on, for the failing count per route, for `itgraph bot` refusing an unreachable chat before delivering, and for YouTube in a restore inside quiet hours;
  - `Platform` moved to `db/models.py`, so the exporter no longer imports the bot's package;
  - the design's message sketch and the privacy page's heading corrected.

  `make validate`: 2273 passed, ansible-lint clean; `openspec validate --strict` valid. The role is unchanged since the e2e run above.

## 11. Before the release

- [x] 11.1 The request of design.md, Decision 15, handed to the operator as it stands. Verify: it names no repository, host or value.

## 12. The operator's

- [x] 12.1 **(operator)** Push `master`, wait for the image, tag a release. **A published release cannot be changed.**
- [x] 12.2 **(operator)** Test environment:
  - a forum chat with an English and a Russian topic, the test bot a member and no administrator, the three inputs in the test inventory;
  - the role at the release, then a restore.

  Verify: the report shows Telegram's synthetic alert and one in each topic delivered, in the right places; `/status` in both chats.
- [x] 12.3 **(operator)** In the test chat, record here what the Bot API answers to a send into General without a topic id, into a closed topic, and into a deleted one. Verify: the three answers recorded; if a send into a closed topic succeeds, design.md says so.
- [x] 12.4 **(operator)** The request of 11.1 on the infrastructure repository's side. Verify: the rules fire per platform on a test of their own, and the dashboard shows both.
- [x] 12.5 **(operator)** Production:
  - the forum chat, its two topics and the bot as a member; the inputs on the controller;
  - promote the pin and run the role. Its migration takes the full dump and then **drops `alert_feedback`**.

  Verify: the bot starts; `/status` answers in both chats; a press on an old button removes its keyboard.
- [x] 12.6 **(operator)** A week after 12.5: Telegram's held alerts and digest parts read in `/status` and the series. Verify: recorded here.

## Workflow follow-up

- `/opsx:verify`, then `/opsx:archive` once 12.6 is done.
