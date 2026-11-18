# Tasks

Tasks marked **(operator)** reach a server, Telegram or the test environment, and are the operator's. The agent sends no request, runs no CronJob, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`).
- Migrations are verified on a scratch database whose name ends in `_test`.
- No test makes a request; fake channel ids and handles are spelled as obviously fake.
- No log record or report names who pressed a button — checked by a test wherever a press is handled.

## 1. Schema

- [ ] 1.1 One revision (design.md, Decision 1):
  - `yt_rising_offer_days` (`day`, `computed_at`, `size_chart`, `size_links`);
  - `yt_rising_offers` with Telegram's checks, unique `channel_id`, the partial indexes, and the columns a YouTube offer adds: `ranking`, `region`, `position`, `families`, `families_before`, `first_in_window`, `discovered_via`, `topic`, `language`;
  - `chart` added to the discovery source enum;
  - the bot's `SELECT` on both tables and `UPDATE` on delivery and decision columns only; the exporter's `SELECT` on moments only.

  Its downgrade drops both tables and the enum value. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; `tests/test_schema_names.py` green.

## 2. The selection

- [ ] 2.1 `config.py` gains `youtube_rising_chart_offers_per_region` and `youtube_rising_link_offers_per_day` (3 each, at least 1). Verify in `tests/test_config.py`.
- [ ] 2.2 `derive/yt_offers.py`, pure `choose_offers` (design.md, Decision 2). Verify with unit tests, a scenario each, of "A Daily Selection From Both Rankings": the head of both rankings; the link ranking wins a channel both hold; the overflow waits; a channel offered once, ever.
- [ ] 2.3 `db/yt_offers.py` stores the day, the offers and the candidate records in one transaction; `cli.py` gains `yt offer` with `--preview`. Verify: the record's status, discovery source (`chart`; strongest of the others, ties in the fixed order) and `first_seen_at`; a channel that gained a record another way is not offered; a second run says the day has its selection; an empty day is recorded; nothing is requested and no key is needed.
- [ ] 2.4 The report and logs name ids and counts only. Verify: `yt offer` prints counts; `--preview` prints ids; debug logs carry no handle, title or link.

## 3. The card and its delivery

- [ ] 3.1 The bot's delivery pass gains the YouTube offers' step: claim, close what is no longer a candidate or a maybe, render, send to the offer's stored topic, mark (design.md, Decision 3). Verify: the topic of each kind of offer — `US` to English, `RU` to Russian, a link offer of a known language to its topic, an unknown one to English marked; outside the topic's quiet hours and uncapped; a refused topic fails the attempt and reaches no other topic or chat; a failed send is retried and a sent one is not.
- [ ] 3.2 The card: channel linked by handle when a payload carries one, else by id; title shown when a payload holds one; the evidence line of each ranking; the charting video linked on a chart offer, read from the stored reading at sending; everything escaped; the preview forced. Verify with fixtures titled with angle brackets and an ampersand, and a payload-less offer.

## 4. The buttons

- [ ] 4.1 `bot/review.py` (or its YouTube peer) gains the presses, the keyboards and the decision line (design.md, Decisions 4 and 5). Verify: seed of a known language takes two presses and of an unknown one three; maybe one; reject then a reason; «↩️ Назад» at each step; every label meets the rule — one to three words, a leading emoji, no trailing punctuation.
- [ ] 4.2 `bot/handlers.py` binds `yv:` in the YouTube chat alone. Verify: a deciding press writes only where the decision is empty and the card delivered, and redraws the card decided; a press on a decided offer changes nothing and is answered; the Telegram chat's `rv:` still refuses the YouTube chat, and `yv:` refuses the Telegram chat; an unknown offer id is answered and writes nothing; nothing stored or logged identifies who pressed.

## 5. Applying decisions

- [ ] 5.1 `itgraph apply-reviews` takes both platforms' decided, unapplied offers, oldest first, one transaction each, applying through the code `itgraph yt mark` uses. Verify: a seed of kind and language applied and `reviewed_at` set so the backfill walks it; a channel reviewed in the meantime left alone, the decision recorded not applied; each applied once; Telegram's application unchanged and its tests green.

## 6. Series

- [ ] 6.1 `metrics/series.py` gains the three YouTube rising series. Verify: served from a scrape with rows present and 0 with none; the exporter's role tests — a day's date, an offer's channel, ranking, numbers or decision refused, moments alone granted.

## 7. The restore

- [ ] 7.1 `files/neutralise.sql` and `files/readback.sql` gain the YouTube offers (design.md, Decision 8). Verify: the existing per-statement test removes each new statement in turn and the read-back fails; a restore of a dump holding a computed, undelivered YouTube offer marks it delivered and sends nothing.

## 8. Deploy

- [ ] 8.1 The CronJob `itgraph-yt-offer` among the always jobs in `deploy/ansible/itgraph/vars/main.yml`, scheduled by `itgraph_yt_offer_schedule` (`0 11 * * *` in `defaults/main.yml`), its pod holding the configuration and the database's URL alone. Verify: `make ansible-lint`; the e2e render asserts the job exists where collection is off too, with no YouTube secret; the detection job's reviews container applies both platforms under the same command; `make test-e2e` on the throwaway cluster.

## 9. Docs and release

- [ ] 9.1 `src/itgraph/README.md`: the selection, the card, the review and the application. `src/itgraph/CLAUDE.md`: the new modules. `deploy/runbook.md`: the three series and the alerts. `docs/PLAN.md`: "Expanding the seed set".
- [ ] 9.2 (operator) A release of its own, with `yt-rising-candidates`, test environment first; after the production deploy, watch the first 11:00 selection, the cards in their topics, and the first pressed seed's walk.
