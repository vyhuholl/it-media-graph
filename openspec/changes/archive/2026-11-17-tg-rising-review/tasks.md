# Tasks

Tasks marked **(operator)** reach a server, Telegram or the test environment, and are the operator's. The agent sends no message through a real bot, runs no CronJob and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`).
- Migrations are verified on a scratch database whose name ends in `_test`.
- The Bot API is the suite's fake. Fake ids, usernames and titles are spelled as obviously fake.
- Nothing about who pressed a button is stored or logged — checked by a test wherever a press is handled.
- Every button label holds to `CLAUDE.md`'s rule, by a test.

## 1. Schema

- [x] 1.1 One revision (design.md, Decisions 1, 7):
  - `tg_rising_offer_days` (`day`, `computed_at`, `size`);
  - `tg_rising_offers`, with the unique `channel_id`, the checks and the two partial indexes;
  - the existing enum types `channel_status`, `tg_channel_kind`, `tg_reject_reason`, each referenced with `create_type=False`;
  - the bot's `SELECT` on both tables, and `UPDATE` on the delivery and decision columns;
  - the exporter's `SELECT (computed_at)` on the days, and `SELECT (offered_at, delivered_at, decided_at, applied_at)` on the offers.

  Its downgrade removes all of it. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; `tests/test_schema_names.py` green. Verify the checks with tests: a seed without a kind and a rejection without a reason are refused, and so is a second offer of one channel.
- [x] 1.2 Role tests on a database the migrations built, as `tests/test_resolutions_role.py` does:
  - the bot records a delivery and a decision;
  - the bot is refused inserting an offer or a day, changing an offer's channel, rank or numbers, setting `applied_at` or `applied`, and updating `tg_channels`;
  - the exporter reads the four moments and `computed_at`;
  - the exporter is refused the channel, the rank, the decision, the kind, the reason and the day.

## 2. The daily selection

- [x] 2.1 `config.py` gains `rising_offers_per_day` (5, at least 1). Verify in `tests/test_config.py`.
- [x] 2.2 `derive/offers.py`, pure: the ranking's `ranked`, the channels ever offered and the size in; the day's offers in rank order out. Verify with unit tests, a scenario each, of "A Daily Selection Of Rising Channels" and "Each Channel Is Offered Once".
- [x] 2.3 `db/offers.py` stores a day with its offers in one transaction; a second store of the day meets the key in a savepoint and says so. `itgraph offer` loads, ranks, stores and prints counts; `--preview` prints the offers with usernames and writes nothing. Verify against a `_test` database:
  - the scenarios of "A Daily Selection Of Rising Channels", including the empty day;
  - "Selecting Reads Only Collected Data", run while a test holds the session lease;
  - that without `--preview` the output holds no username.
- [x] 2.4 Docs: `src/itgraph/README.md`, a section on the review — the selection, the command and `--preview`. The `derive/offers.py`, `db/offers.py` and `cli.py` rows of `src/itgraph/CLAUDE.md`.

## 3. The card and its buttons

- [x] 3.1 `bot/review.py`, pure (design.md, Decisions 3–5): the card's text from an offer, its channel and its posts; the three keyboards as rows of label and data; parsing callback data; the line stating a decision. Verify with unit tests:
  - a card with three families and three posts;
  - a seed without a username named by its title, unlinked;
  - a title with `<`, `>` and `&` shown as written;
  - every label one to three words with a leading emoji and no trailing punctuation;
  - every callback datum at most 64 bytes, and parsed back to the press that made it;
  - data that is not `rv:` refused;
  - the decision lines for a seed of each kind, a maybe and a rejection for each reason.

## 4. Sending the offers

- [x] 4.1 `db/offers.py` claims undelivered offers, oldest day then rank, and marks them delivered or failed; the posts for a card are read as design.md, Decision 3 says. `bot/app.py` sends them for the Telegram destination, outside quiet hours, with the channel's own link previewed; `BotSender.send` takes an optional keyboard. Verify in the bot's tests against the fake sender:
  - the scenarios of "Offers Are Delivered Outside Quiet Hours, Uncapped" and "An Offer Reviewed Elsewhere Is Not Sent";
  - "An Offer Is A Card";
  - alerts and lists sent as before, with no keyboard.

## 5. Pressing a button

- [x] 5.1 `db/offers.py` records a decision only where none is and the offer was delivered, and says whether it did. `bot/handlers.py` binds the presses in the Telegram chat, redraws, records, edits and answers (design.md, Decision 4). Verify in `tests/test_bot_handlers.py`:
  - the scenarios of "A Card Is Reviewed As `itgraph mark` Reviews" and "A Decision Is Final In The Bot", including two presses at once;
  - an unknown offer;
  - a press in the YouTube chat refused;
  - the retired feedback buttons still retired;
  - "Nobody Who Pressed Is Recorded" — no row and no log line carries the presser's id or name.
- [x] 5.2 Docs: the README's section — the buttons, the second step, a decision as final, and fixing one with `itgraph mark`. The `bot/review.py`, `bot/app.py` and `bot/handlers.py` rows of `src/itgraph/CLAUDE.md`.

## 6. Applying decisions

- [x] 6.1 `db/offers.py` and `itgraph apply-reviews` apply each decided offer once, oldest first, a transaction each, through `mark_channel` under the channel's row lock (design.md, Decision 6); counts only in the output. Verify against a `_test` database:
  - the scenarios of "A Decision Reaches The Inventory Within Fifteen Minutes";
  - a seed decided in the bot is the next seed the poll loop walks;
  - a maybe sets no kind.
- [x] 6.2 Docs: the README's section — when a decision lands, and what happens to one on a channel reviewed meanwhile. `docs/PLAN.md`, "Expanding the seed set".

## 7. The role

- [x] 7.1 The role (design.md, Decisions 2, 6, 8):
  - a CronJob `itgraph-offer`, in every environment, `itgraph_offer_schedule` `0 11 * * *`, starting deadline 13 hours, reaching the database alone and holding the configuration and the database's URL;
  - a third container of `itgraph-detect` running `apply-reviews`, with the same configuration and secret as its siblings;
  - `files/neutralise.sql` and `files/readback.sql`, a statement and a check each for undelivered offers and unapplied decisions.

  Verify in `tests/test_deploy_role.py`:
  - the scenarios of "Telegram's Rising Selection Runs In Every Environment" and "The Detection Job Applies Decisions";
  - the schedule table;
  - no job passes `--preview`.

  Verify in `tests/test_neutralise.py` that removing either new statement fails the read-back.
- [x] 7.2 Docs: `deploy/runbook.md` — the new CronJob, running `offer --preview` on the host, and what to do when the cards did not come or a decision did not land. The README's restore section.

## 8. The series

- [x] 8.1 `metrics/series.py` serves the three series (design.md, Decision 9). Verify:
  - the scenario "The rising selection and its decisions";
  - the series list on an empty database, each 0;
  - the role test's count of series;
  - "The role cannot read what was offered or decided".
- [x] 8.2 Docs: the README's table of series, and `deploy/runbook.md` — what each series means and which alert reads it. The `metrics/series.py` row of `src/itgraph/CLAUDE.md`.

## 9. Integration

- [x] 9.1 `openspec validate tg-rising-review --strict`, `make validate` and `make test-e2e`. The last covers the revision under the restore, the new CronJob and the detection job's third container. Verify: all green, and the number of collected tests not lower than before group 1. Then a sweep:
  - `from_user` read nowhere in `src/`;
  - the bot's role refused `UPDATE` on `tg_channels` in the e2e copy;
  - no job's output prints a username.

## 10. Rollout

A release of its own.

- [x] 10.1 **(operator)** Deploy the release to the test environment. Verify:
  - the revision applies;
  - `scripts/on-host.sh offer --preview` prints the copy's offers;
  - a run of `itgraph-offer` sends the cards to the test chat;
  - a seed and a rejection pressed there are applied by the next `itgraph-detect`;
  - the three series read sensibly.
- [x] 10.2 **(operator)** Deploy to production. Verify, over the first two days:
  - at most five cards at 11:00 in the Telegram alert chat;
  - presses land within 15 minutes;
  - a pressed seed is walked by the poll loop;
  - nothing is offered twice.
- [x] 10.3 **(operator)** Ask the infrastructure repository for the three alerts — a selection older than 25 hours, an offer undelivered for an hour outside quiet hours, a decision unapplied for an hour — and a panel.

## Workflow follow-up

- `/opsx:apply`, then `/opsx:verify`. Archive once 10.2 is read.
- Next: change 4, YouTube's rising candidates, with an explore.
