# Tasks

Tasks marked **(operator)** reach the server or publish something, and are the operator's. Every task that changes code ends on a green `make validate`. Nothing here names a host, an address, an id or the infrastructure repository, and no test holds a real payload: every fixture is synthetic.

## 1. The rule

- [x] 1.1 `tg/payload.py`: the rule of design.md, Decision 1, as a function of its own over plain JSON values, applied by `encode_payload`; the module's docstring states the exception to "verbatim". Verify: a test per scenario of `specs/personal-data`, "What Names A Person", idempotence among them; `tests/test_payload.py`'s existing tests pass; `git diff --stat` shows no change to `tg/watch.py`, `tg/backfill.py` or `tg/full_channel.py`.
- [x] 1.2 The three ways in. Verify: tests through the existing fakes that a polled post, a walked post and a channel's payload are stored naming nobody, and, by `caplog`, that no person's value reaches a log line.
- [x] 1.3 Nothing derived changes. Verify: a test deriving edges, references, readings and YouTube links from the same synthetic posts as received and as cleared, every derived table compared; it fails when the rule is made to clear a channel's peer too, then the rule is put back.

## 2. The migration

- [x] 2.1 The revision of design.md, Decision 3, revising `b7d2e4f19a30`: the function in `pg_temp`, the two prefiltered updates, `VACUUM FULL` in the autocommit block, a downgrade that does nothing. Verify on a database the test builds for itself, ending in `_test`: from the previous revision with seeded rows of every shape, each scenario of "What Was Stored Before Is Cleared Once"; a row naming nobody keeps its `xmin`; a downgrade changes no table.
- [x] 2.2 One table of cases through `tg/payload.py` and through the newest such revision's function. Verify: the test, and that it fails when either side drops one type of the rule.
- [x] 2.3 The implemented revision timed on the operator's machine at production's size, synthetic. Verify: the update's and the vacuum's times recorded in design.md, Decision 3.

## 3. The restore

The requirement is `adopt-infrastructure-requirements`' `database-backups` delta, "A Restored Copy Names Nobody", amended with this change.

- [x] 3.1 `neutralise.sql` without its payload statements; `readback.sql` looks for every type and field of design.md, Decision 1, anywhere in `raw_messages` and `raw_channels`. Verify: `tests/test_neutralise.py`, with a user's peer at a path no statement ever named failing the read-back, each remaining statement still needed, and the table of cases of 2.2 reading back clean once cleared.
- [x] 3.2 The end-to-end suite: a dump of the previous revision with every shape, made as design.md, Decision 4 says, restores and reads back clean; a dump at head naming a person is refused, and the database is left empty. Verify: `make test-e2e`, with both in its output.

## 4. Rules and docs

- [x] 4.1 Root `CLAUDE.md` ("The raw layer is immutable", "What the restore neutralises keeps up with the schema", the line on commenter ids), `README.md`, `src/itgraph/README.md` and the `edges` model's docstring, per design.md, Decision 5. Verify: a search for "verbatim", "stay in the raw layer" and «остаются в сыром слое» finds no sentence the code contradicts.
- [x] 4.2 `make validate` and `make test-e2e` green, and `openspec validate --strict` for this change and `adopt-infrastructure-requirements`. Verify: their output.

## 5. After production

- [x] 5.1 **(operator)** Once `adopt-infrastructure-requirements`' 14.3 has passed, the words added to `docs/privacy.html`, with the date of the run and the date the last backup holding people is gone. Verify: the words recorded here.

  The operator on 2026-11-07, before production: «Сначала поправь privacy.html и PLAN.md. Слова сам подбери какие лучше. Сегодня 07.11.2026.» So the words are the agent's, and both pages describe what the version of 2026-11-07 does rather than the date production ran it: it removes people before storing, deletes those stored when first installed, and the backups holding them go about four weeks later. Both pages go live when `master` is pushed.
- [x] 5.2 `docs/privacy.html` takes them and a new "Last updated" date; `docs/PLAN.md`'s legal note says what was removed and when. Verify: `git diff` shows those lines and nothing else.
- [ ] 5.3 **(operator)** A read-only count on production, `jsonb_path_exists` alone, streaming: no stored payload names a person; and the revision's duration in the test restore of 13.6. Verify: both recorded here.

## Workflow follow-up

- `/opsx:verify`, then `/opsx:archive` once 5.3 is done.
- The dumps that hold people are gone 28 days after production's run on the host, a day later off-site; nothing is to be done for it.
