# Proposal

## Why

The third of the changes that bring rising channels into the inventory (the explore of 2026-11-16). `tg-rising-candidates` ranks Telegram's rising channels and resolves the ranking's head unattended, but a person still has to run `itgraph rising` and then `itgraph mark` for each one — and nobody has reviewed a candidate since August. This change brings the ranking to the operator: a few channels a day in the Telegram alert chat, each reviewed with buttons as `itgraph mark` reviews it. A seed marked that way is walked by the poll loop (`walk-new-seeds`) and joins the graph at the next derivation.

Measured on the production database on 2026-11-17, the day `tg-rising-candidates` was released (aggregates only):

- **Rising now:** 9 channels with a username — the ones that can be shown — and 6 more known by id alone, waiting for the loop to resolve them.
- **The flow:** about 12–16 new rising candidates a week at the accepted threshold (measured in `tg-rising-candidates`), about 2 a day. Five a day clears today's backlog in two days and keeps up after that.
- **The loop's first resolution** happened within the hour of the release, and no rate limit has been met since.

## What Changes

- **A daily selection.** At 11:00 Moscow time a scheduled pass picks, from the ranking, the top `rising_offers_per_day` (5) channels with a username that were never offered before. The rest wait for the next day's selection, which re-ranks. A day with nothing to offer sends nothing, and the pass still records that it ran.
- **A card per channel in the Telegram alert chat**, in rank order: `@username` with its link, the title, the families that referred to it in the window and before it, when the window's first reference was, and links to up to three seed posts that referred to it, one per family. Sent by the bot outside quiet hours, outside the alert cap and the digest, as the weekly list is.
- **Review with buttons, as `itgraph mark` reviews.** Three buttons: «🌱 В сиды», «🤔 Может быть», «🚫 Отклонить». Seed and reject take a second step: a channel's kind (seven, as `--kind`) or a reason (seven, as `--reason`), with «↩️ Назад». A decision is final in the bot. The message then shows it and loses its buttons, and a mistake is fixed with `itgraph mark`.
- **The bot records a decision and changes nothing else.** It writes the decision into the offer only, under its own database role — no new privilege on the inventory. The detection job applies it to `tg_channels` within 15 minutes, unless the channel was reviewed some other way in the meantime.
- **Each channel is offered once.** No reminder, and nothing pressed means the channel stays a candidate.
- **Nobody is recorded:** who pressed a button is neither stored nor logged.
- **A restored copy offers nothing from production.** Undelivered offers are closed on restore, and so are decisions not yet applied.
- **Series** for the shared monitoring: when the day's selection last ran, how long the oldest offer has waited for delivery, and how long the oldest decision has waited to be applied.

**Out of scope:**

- YouTube's rising candidates and their review: change 4.
- Notes beside a decision (`--note`): typed text in a group chat is another kind of input, and a note is what the restore clears.
- Changing a decision from the bot.
- Offering a channel a second time, or reminding about one nobody reviewed.

## Capabilities

### New Capabilities

- `telegram-rising-review`: the daily selection of rising channels, the card the bot sends for each, the buttons that review it, and how a decision reaches the inventory.

### Modified Capabilities

- `alert-delivery`: the bot's role may also record an offer's delivery and its decision, and still nothing of the inventory or of which channel an offer is.
- `database-backups`: a restored copy closes undelivered offers and unapplied decisions, and the read-back fails while one remains.
- `collection-deploy`: the daily selection is scheduled in every environment, and the detection job applies decisions.
- `collection-monitoring`: three series of the selection and its decisions, read from their moments alone.

## Impact

- **Code:**
  - `db/offers.py` (the selection's tables: storing a day, claiming, marking, recording a decision, applying it);
  - `derive/offers.py` (pure `choose_offers`: which channels a day offers; and the passes around it — the selection, and applying decisions — as `derive/edges.py` holds the derivation pass);
  - `bot/review.py` (pure: the card, the buttons and what a press means);
  - `bot/app.py` and `bot/handlers.py` (sending, and the callback);
  - `cli.py` (`itgraph offer`, `itgraph apply-reviews`); `config.py`; `metrics/series.py`.
- **Schema:** one Alembic revision.
  - `tg_rising_offer_days` (a day and when it was computed) and `tg_rising_offers` (the day's channels, their delivery and their decision).
  - The bot's `SELECT` on both and `UPDATE` on delivery and decision columns only.
  - The exporter's `SELECT` on moments only.
- **Role:**
  - a CronJob `itgraph-offer` daily at 11:00, in every environment, reaching the database alone;
  - a third container in `itgraph-detect` running `itgraph apply-reviews`;
  - `files/neutralise.sql` and `files/readback.sql` gain the offers.
- **Docs:** `src/itgraph/README.md` (the review), `src/itgraph/CLAUDE.md`, `deploy/runbook.md` (the series), `docs/PLAN.md` ("Expanding the seed set").
- **Infrastructure repository:** alerts on a selection older than a day, an offer undelivered for an hour, a decision unapplied for an hour, and a panel.
- **Release:** a release of its own, test environment first.

## Decision record

Answers in the explore of 2026-11-16, accepted then: one batch a day at 11:00 Moscow time, at most five per chat, the overflow on the next day; outside the alert cap and the digest; an empty day silent, with a series to show the pass ran; each candidate shown once, no reminders. TG candidates go to the Telegram alert chat, which only the operator and their manager read. Button text follows `CLAUDE.md`: one to three words with a leading emoji, no trailing punctuation.

The operator chose the recommended answer to each of four questions on 2026-11-17:

1. The bot writes a decision into the offer only; `itgraph-detect` applies it to the inventory every 15 minutes if the channel is still a candidate or a maybe. The bot gets no privilege on `tg_channels`.
2. Seed and reject take a second step: seven kinds or seven reasons, with «↩️ Назад»; maybe is one press.
3. A decision is final in the bot; a mistake is fixed with `itgraph mark`.
4. The card: `@username` and title, the families in the window and before it, the first reference in the window, and links to up to three seed posts that referred to it, one per family.

**Not covered by the requirements.** These were chosen here, not by the operator:

- A release of its own, as for the last three changes.
- A decision on a channel reviewed some other way before it was applied is not applied, and is recorded as such.
- An offer whose channel was reviewed before the bot sent it is not sent, and neither is one whose channel lost its public username: a card names and links the channel by it.
- The restore closes decisions not yet applied, as well as undelivered offers.
- Who pressed a button is not recorded.
- The card's link preview is the channel's own, which Telegram renders with the channel's description and avatar.
- The button labels for the kinds and reasons (design.md, Decision 5).
