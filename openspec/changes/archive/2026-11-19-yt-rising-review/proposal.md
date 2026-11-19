# Proposal

## Why

The fifth of the changes that bring rising channels into the inventory (the explore of 2026-11-16; `yt-rising-candidates` is the fourth). That change works out YouTube's two rankings, but a person still has to run `itgraph yt rising` and review each channel by hand — and the queues it replaces were drained by the last review round, so whatever starts rising next reaches nobody until the operator asks. This change brings the rankings to the operator: a few channels a day in the YouTube chat's topics, each reviewed with buttons as `itgraph yt mark` reviews it. A seed marked that way is walked by the backfill loop (`walk-new-seeds`) and joins the graph at the next derivation.

Measured on the production database on 2026-11-18 (aggregates only):

- **Language almost never comes from the API:** 56 of 1,127 stored payloads carry `defaultLanguage` (31 `en`, 20 `ru`). The card's language routing mostly cannot know, so the operator's fallback is the norm: the English topic, the language marked unknown, and language buttons at the decision.
- **The whole pipeline's shape exists twice over:** Telegram's rising review (offer days, cards, buttons, unattended application, restore, series) and YouTube's weekly lists (topics by language, uncapped delivery outside quiet hours) together cover every part but the YouTube card itself.

## What Changes

- **A daily selection, from both rankings.** At 11:00 Moscow a scheduled pass picks, from `yt-rising-candidates`' rankings, up to `youtube_rising_chart_offers_per_region` (3) chart channels of each region and up to `youtube_rising_link_offers_per_day` (3) link channels, none offered before. A channel that sits in both rankings is taken by the link one. The rest wait for the next day's selection, which re-ranks. A day with nothing to offer sends nothing and is still recorded.
- **Each offered channel becomes a candidate record, and nothing is asked of YouTube.** The selection creates the record without API data, as the queues' `--add` creates records; the refresh fetches its payload at its next run. The selection makes no request, holds no key, and runs in every environment.
- **A card per channel, in the topic of its language.** Chart offers: `US` to the English topic, `RU` to the Russian. Link offers: the topic of the payload's `defaultLanguage` when it is `en` or `ru`; otherwise the English topic, the language marked unknown on the card. The card links the channel — by its handle when a stored payload carries one, else by its id — names its evidence (families and dates for a link offer, region and position for a chart offer, the charting video linked), and forces the link preview, which shows what Telegram renders of the page: the recognition the card itself needs no payload for. Everything from a channel or a video is escaped.
- **Review with buttons, as `itgraph yt mark` reviews.** Three buttons: «🌱 В сиды», «🤔 Может быть», «🚫 Отклонить». Seed takes the five kinds, then — when the offer's language is unknown — the language buttons «🇬🇧 EN», «🇷🇺 RU», «🌐 Другой»; reject takes the seven reasons; each second step returns with «↩️ Назад». A seed of a known language needs no language step. A decision is final in the bot; a mistake is fixed with `itgraph yt mark`.
- **The bot records a decision and changes nothing else,** under its own database role; the detection job applies it to the inventory within fifteen minutes, unless the channel was reviewed some other way in the meantime.
- **Each channel is offered once.** No reminder; nothing pressed leaves the channel a candidate.
- **Nobody is recorded:** who pressed is neither stored nor logged.
- **A restored copy offers nothing from production:** undelivered offers are closed on restore, and so are decisions not yet applied.
- **Series** for the shared monitoring: when the selection last ran, how long the oldest offer has waited for delivery, how long the oldest decision has waited to be applied.

**Out of scope:**

- Notes beside a decision, changing a decision from the bot, offering a channel twice, reminders.
- The rankings, the chart reading and the lookups: `yt-rising-candidates`.
- Deciding a language for a channel from its content: the buttons are the mechanism, by the operator's decision.
- Skipping channels of other languages: they are offered, in the English topic with the language marked — the operator decides what they are.

## Capabilities

### New Capabilities

- `youtube-rising-review`: the daily selection from the two rankings, the records it creates and their discovery source, the card the bot sends for each channel and the topic it goes to, the buttons that review it, and how a decision reaches the inventory.

### Modified Capabilities

- `youtube-inventory`: the discovery source set gains `chart`; the rising flow records `chart` for a chart offer and the strongest of `telegram`, `mention`, `featured` for a link offer.
- `alert-delivery`: the bot's role may also record a YouTube offer's delivery and its decision — and still nothing of the inventory or of which channel an offer is.
- `database-backups`: a restored copy closes YouTube's undelivered offers and unapplied decisions, and the read-back fails while one remains.
- `collection-deploy`: the selection is scheduled in every environment, and the detection job applies the decisions of both platforms' offers.
- `collection-monitoring`: three series of YouTube's selection and its decisions, read from their moments alone.

## Impact

- **Code:**
  - `db/yt_offers.py` (the tables: storing a day, claiming, marking, recording a decision, applying it) and `derive/yt_offers.py` (the pure choice and the passes around it);
  - `bot/review.py` or its YouTube peer (pure: the card, the buttons, what a press means), `bot/app.py` and `bot/handlers.py` (sending to the topic, the `yv:` callback);
  - `cli.py` (`itgraph yt offer`; `apply-reviews` extended to both platforms); `config.py`; `metrics/series.py`.
  - No new dependency.
- **Schema:** one Alembic revision.
  - `yt_rising_offer_days` and `yt_rising_offers`, mirroring Telegram's with a region and position for chart offers and the topic a card goes to;
  - `chart` added to the discovery source enum;
  - the bot's `SELECT` on both tables and `UPDATE` on delivery and decision columns only; the exporter's `SELECT` on moments only.
- **Role:** a CronJob `itgraph-yt-offer`, daily at 11:00, in every environment, reaching the database alone; the detection job's reviews container now applies both platforms' decisions under the same command.
- **Docs:** `src/itgraph/README.md` (the review), `src/itgraph/CLAUDE.md`, `deploy/runbook.md` (the series), `docs/PLAN.md` ("Expanding the seed set").
- **Infrastructure repository:** alerts on a selection older than a day, an offer undelivered for an hour, a decision unapplied for an hour, and a panel.
- **Release:** a release of its own, after `yt-rising-candidates`, test environment first — the selection runs there on the restored copy, into that environment's own chat, as Telegram's does.

## Decision record

Brought from the explore of 2026-11-16 and the operator's answers of 2026-11-18: two rankings, reviewed alike; for the chart, three a day of `regionCode=US` to the English topic and three of `regionCode=RU` to the Russian; for the links, three a day, routed by the channel's language when it can be told from the payload — the language, not the country — with other and unknown languages going to the English topic marked as such, and language buttons at the decision when the language is unknown; the lookups embedded under a limit (that one is `yt-rising-candidates`'); consumer-tech noise accepted, no "took off" filter.

Confirmed:

1. The daily sizes are three chart channels a region and three link channels, one batch a day at 11:00 Moscow, the overflow on the next day; outside the topic caps and digests, as the weekly lists are.
2. The card shows the channel by its link and its evidence; the language is marked unknown when nothing tells it.
3. A seed decided with buttons takes a kind, and a language when the offer's is unknown; a rejection takes one of the seven reasons; maybe is one press.
4. A decision is final in the bot; the detection job applies it within fifteen minutes; the bot gains no privilege over the inventory.

**Not covered by the requirements.** These were chosen here, not by the operator:

- A channel in both rankings is offered by the link one, the chart's three going to channels the link ranking did not take (design.md).
- The selection creates candidate records without API data and makes no request, so it runs where no key is; payloads arrive with the next refresh, and a day-one card links by id (design.md).
- The discovery source of a link offer is the source of its strongest evidence, ties broken `telegram`, `mention`, `featured` (design.md).
- The language step is asked only when the offer's language is unknown or neither `en` nor `ru` (design.md).
- The card's link preview is the channel's own page, as Telegram renders it (design.md).
- The button labels (design.md).
