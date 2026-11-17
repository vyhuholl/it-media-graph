# Tasks

Tasks marked **(operator)** reach a server, Telegram or the test environment, and are the operator's. The agent sends no request to Telegram, runs no collector, `resolve` or CronJob, and points nothing at the production database.

Rules for every task:

- Every group ends on a green `make validate` (hooks through `prek`).
- Migrations are verified on a scratch database whose name ends in `_test`.
- The Telegram client is the suite's fake. No test makes a request, and fake ids and usernames are spelled as obviously fake.
- No username or id of a resolution is written to the journal, and no pending username is printed — checked by a test wherever either is produced.
- `itgraph resolve`'s and `itgraph add`'s existing tests stay green; they change only where a journal row is now expected.

## 1. Schema

- [x] 1.1 One revision (design.md, Decisions 3, 4 and 8):
  - `first_referenced_at` and `last_referenced_at`, nullable, on `tg_pending_mention_sources`;
  - enum types `tg_resolution_kind` (`id`, `username`) and `tg_resolution_outcome` (`channel`, `not_channel`, `failed`);
  - the table `tg_resolutions` (`id`, `requested_at`, `command` of `tg_collection_command`, `kind`, `outcome`), indexed on `requested_at`;
  - the exporter's `SELECT (requested_at, command)` on it.

  Its downgrade removes all of it. Verify on a `_test` database: upgrade, downgrade, upgrade; autogenerate reports nothing; `tests/test_schema_names.py` green.

## 2. Pending sources remember when

- [x] 2.1 `derive/edges.py` carries each mention's publication time to `db/edges.py`. There the source's upsert keeps the least first and the greatest last time, updating a row only when a time would move (design.md, Decision 3). Verify in the derivation tests, with the scenarios of the modified "Pending Mentions Record Their Sources": a source keeps its first and last mention; a later mention moves the last only; re-derivation writes nothing; a rebuild empties and refills them; sources go with a resolved name.
- [x] 2.2 Docs: `src/itgraph/README.md`'s `derive` section, and the `db/edges.py` row of `src/itgraph/CLAUDE.md`.

## 3. The journal of resolutions

- [x] 3.1 `db/rising.py` gains `record_resolution(session, command, kind, outcome)` and `resolutions_since(session, moment)`, the counts by command. `tg/resolve.py`'s `resolve_channel` and `resolve_pending`, and `tg/manual.py`'s lookup, write a row with their answer, in the transaction that records it; a rate limit is `failed`. Verify:
  - `itgraph resolve` and `itgraph add` each journal under their own command, by id or by username, with the outcome — channel, not a channel, failed;
  - a name resolving to a user leaves a row with neither name nor id;
  - those commands' existing tests stay green.
- [x] 3.2 Docs: `src/itgraph/README.md`, `resolve` and `add`: the journal, and that their resolutions count against the loop's cap. The `tg/resolve.py`, `tg/manual.py` and `db/rising.py` rows of `src/itgraph/CLAUDE.md`.

## 4. The ranking

- [x] 4.1 `config.py` gains `rising_window_days` (14) and `rising_min_families` (2), at least 1 each, and `resolution_cap` (10) and `rising_resolve_gap_minutes` (20), the cap at least 0. Verify in `tests/test_config.py`.
- [x] 4.2 `derive/rising.py`, pure: rows and a moment in; the ranking, the candidates awaiting an identity in rank order, and the counts left out by reason, out (design.md, Decisions 1 and 2). Verify with unit tests, a scenario each, of "What Is Rising", "How Rising Candidates Rank" and "What Is Left Out". A source without times counts as no evidence in the window.
- [x] 4.3 `db/rising.py` loads the rows in one `REPEATABLE READ` transaction: seeds that are not chats with their families; their edges to `candidate` and `maybe` channels; their pending sources with times; each target's identity state. Verify on a `_test` database: a seed's edge and a candidate's edge; a confirmed family; a chat seed left out; "Nothing is stored" (no row changes).

## 5. `itgraph rising`

- [x] 5.1 `cli.py`'s `rising` and its rendering (design.md, Decision 7):
  - the ranked channels with username, title, families in the window and before, the first reference in the window;
  - the count awaiting an identity;
  - those left out, by reason;
  - the journal's last 24 hours by command against the cap.

  No client, no lease. Verify with the scenarios of "The Rising Report", including one asserting that no pending username and no id of a channel awaiting resolution appears in the output, and one run while a test holds the session lease.
- [x] 5.2 Docs: `src/itgraph/README.md`, a section on rising candidates — what rises, how it ranks, what is left out, the command, running it on the host without `--session`. `docs/PLAN.md`, "Expanding the seed set". The `cli.py`, `derive/rising.py` and `db/rising.py` rows of `src/itgraph/CLAUDE.md`.

## 6. The poll loop resolves the head

- [x] 6.1 `tg/backfill.py`'s `waiting_out_floods` gains `halt_after`, defaulting to `flood_abort_threshold`. Verify: every existing caller unchanged (their tests green); with `halt_after=0` a wait is recorded and raised at once, not slept.
- [x] 6.2 `tg/rising.py`: one turn at resolution (design.md, Decisions 5 and 6). It returns at once, asking nothing:
  - inside the gap since the journal's newest `watch` row;
  - at the cap;
  - while a recorded wait on `ResolveUsernameRequest` or `GetChannelsRequest` has not ended;
  - or with nothing awaiting.

  Otherwise it takes the head awaiting an identity, paces as the resolution pass does, resolves it through `resolve_channel` or `resolve_pending` with a `watch` recorder and `halt_after=0`, and journals it under `watch`. A `FloodWaitTooLong` ends the turn and changes nothing else. `tg/watch.py` calls it when no poll is due and no walk had a page. Verify in `tests/test_watch.py` against the fake client:
  - the scenarios of "The Poll Loop Resolves The Head Of The Ranking", "Resolutions Are Capped Across Every Command" and "A Rate Limit On Resolution Stops Resolution Alone";
  - the new scenarios of "Polls Spend No Quota-Bearing Request";
  - polls continuing at once after a day's wait on resolution, and no resolution after a restart inside it.
- [x] 6.3 Docs:
  - `src/itgraph/README.md`, the loop's section: what it resolves, the gap and the cap, how a wait is handled, and the cycle's line.
  - The README's and `docs/PLAN.md`'s rule on who spends `contacts.resolveUsername`: the resolution pass, `add`, and the loop within the cap; a `ResolveUsernameRequest` under `watch` is a regression past the cap only.
  - The `tg/watch.py`, `tg/rising.py` and `tg/backfill.py` rows of `src/itgraph/CLAUDE.md`.

## 7. The series

- [x] 7.1 `metrics/series.py` serves `itgraph_telegram_resolutions_24h{command}` for `resolve`, `add` and `watch`, each 0 when it made none. Verify:
  - the scenario "Resolutions by command";
  - the series list on an empty database;
  - through the exporter's role: it reads `requested_at` and `command`, and is refused `kind` and `outcome`.
- [x] 7.2 Docs: the README's table of series, `deploy/runbook.md` — what the series means, and what the loop's count above the cap means — and the `metrics/series.py` row of `src/itgraph/CLAUDE.md`.

## 8. Integration

- [x] 8.1 `openspec validate tg-rising-candidates --strict`, `make validate` and `make test-e2e`, the last for the revision under the restore and the exporter's role. Verify: all green, and the number of collected tests is not lower than before group 1. Then a sweep: no `print` of a pending username anywhere in `src/`, and no username or id column in `tg_resolutions`.

## 9. Rollout

A release of its own.

- [ ] 9.1 **(operator)** Deploy the release to the test environment. Verify: the revision applies; after the next `itgraph-detect`, `scripts/on-host.sh rising` prints a ranking of the restored copy; the series reads 0 for every command.
- [ ] 9.2 **(operator)** Deploy to production. Verify, over the first day:
  - `itgraph rising` shows the head;
  - the series shows the loop's resolutions, never more than ten in 24 hours;
  - `tg_flood_events` holds nothing new, or, if a wait came, polls went on through it;
  - resolved candidates appear in the ranking with their usernames.
- [ ] 9.3 **(operator)** Ask the infrastructure repository for the alert on `itgraph_telegram_resolutions_24h{command="watch"} > 10` and a panel.

## Workflow follow-up

- `/opsx:apply`, then `/opsx:verify`. Archive once 9.2 is read.
- Next: change 3, review in the bot — the daily selection at 11:00, the message and its buttons — with an explore.
