## 1. Prove the column is redundant before touching it

- [x] 1.1 Run the redundancy check against the working database by hand: count `operator_id` values with no confirming pair behind them. It was 0 at design time (17 pairs, 17 pointers, 14 families, largest family 3), and seeing it again before a destructive revision is cheaper than reading a failed migration
- [x] 1.2 Full dump, verified with `pg_restore --list`. This is the first revision in the project that drops a column holding hand-made decisions, so the backup is the thing standing between a mistake and re-doing the review by hand

## 2. The family becomes a view over the confirmed pairs

- [x] 2.1 Alembic revision: create the `channel_families` view — a recursive CTE over `affiliation_candidates WHERE decision = 'confirmed'`, taken in both directions, keyed by `LEAST(MIN(reached), channel_id)`. **`UNION`, never `UNION ALL`**: the pairs among one author's channels contain cycles by construction — `A–B`, `B–C`, `A–C` is exactly the shape that broke the old model — and `UNION ALL` would not terminate
- [x] 2.2 Same revision, before any drop: fail loudly if any `operator_id` has no confirming pair behind it. The redundancy is true of this database and guaranteed by nothing in the schema; a migration that quietly discards a fact it assumed was duplicated is what the backup rule exists for
- [x] 2.3 Same revision: drop `channels.operator_id`, its foreign key and the `operator_is_another_channel` check
- [x] 2.4 Same revision: drop `affiliation_candidates.canonical_id` and the `canonical_only_when_confirmed` check
- [x] 2.5 Downgrade: recreate both columns, repopulate `operator_id` by picking an arbitrary channel per family. Say in the docstring that the round trip is lossy — *which* channel was canonical is the fact being destroyed, and pretending otherwise would be worse than the loss
- [x] 2.6 `db/models.py`: drop `operator_id` and its check from `Channel`; drop `canonical_id` and its check from `AffiliationCandidate`. A view is not a model — nothing maps to `channel_families`, it is read by explicit select
- [x] 2.7 Verify up and down on a scratch database whose name ends in `_test`, reading `alembic upgrade --sql` first. Check the view against a cycle (`A–B`, `B–C`, `A–C`) and against a chain of four, since termination is the one thing the CTE can get wrong
- [x] 2.8 Test in `test_db.py`: the view returns one family for a cycle of three, one for a chain of four, nothing for a channel with no confirmed pair, and the same key whichever member is asked about

## 3. Reading the family

- [x] 3.1 `db/affiliation.py`: `family_keys(session) -> dict[int, int]` reading the view. The family of a channel absent from it is the channel itself — `COALESCE(family_key, tg_id)` expressed once, in the one place callers get it from
- [x] 3.2 `db/affiliation.py`: `load_inventory` builds `family_of` from the view instead of from `operator_id`. Detection's exclusion of two channels already in one family then works on sets with no change to `detect.py`
- [x] 3.3 `db/channels.py`: the family filter in `list_channels` joins the view rather than reading a column; `count_families` counts distinct keys among channels the view holds, so a channel in no family is counted in neither number
- [x] 3.4 Tests in `test_affiliation_db.py`: the inventory load reports one family for a group assembled from a non-star set of pairs; a solo channel is its own family

## 4. Confirmation is a statement about channels, not a hierarchy

- [x] 4.1 `db/channels.py`: `confirm_affiliation` takes two **or more** channel references and no `canonical`. It records every pair among them — for four channels, six pairs. Not a chain: withdrawing the middle link of a chain would split a family the operator asserted as whole, and which pairs existed would depend on the order the channels were typed
- [x] 4.2 Same function: delete the canonical-is-one-of-the-two check, the depth-one enforcement, and the different-families refusal. **The merge needs no code** — the pair is recorded and the components join. The comment explaining why a `CHECK` could not see another row goes with the rule it justified
- [x] 4.3 Same function: refuse a statement naming the same channel twice, and one naming a channel the inventory does not hold — nothing written in either case, including pairs among the channels that were valid
- [x] 4.4 `db/channels.py`: delete `recanonicalize_family` and `FamilyLink`. Return what the operator needs to see instead: how many pairs were recorded and how large the family now is, so a merge that surprises them is visible in the output rather than only in the table
- [x] 4.5 `db/channels.py`: `withdraw_affiliation` stops clearing any pointer — it sets the pair back to pending and nothing else. The split falls out of the view; there is no branch to write and none to test beyond the outcome
- [x] 4.6 `db/channels.py`: `_identity` survives (the error messages still name channels), `_record_decision` loses its `canonical_id` argument
- [x] 4.7 Tests in `test_affiliation_family.py` — largely a rewrite, since most existing cases pin the canonical rules. New: a group of four confirmed from non-star pairs ends in one family; the same pairs in any order give the same family; a bridging pair merges two families; a pair inside one family succeeds and changes nothing; withdrawing one pair of a group keeps the family whole; withdrawing the only connecting pair splits it; a repeated channel and an unknown channel each write nothing
- [x] 4.8 Test in the same file: confirming still leaves every edge between the channels in place — unchanged by this change, and the reason the family exists at all

## 5. The commands

- [x] 5.1 `cli.py`: `itgraph family` takes two or more channels, drops `--canonical` and the one-channel promote form with its argument validation. `--reject` and `--withdraw` stay on exactly two channels: a rejection is a statement about a pair, and there is no sensible reading of rejecting a group
- [x] 5.2 Same command: report what happened — pairs recorded, and the size of the resulting family. A merge is silent in the data model and must not be silent on screen
- [x] 5.3 `cli.py`: `itgraph channels --family` keeps working from any member; the `canonical`/`member` column goes, because there is nothing to put in it
- [x] 5.4 Tests in `test_cli.py`: the vacancies case end to end — several channels, pairs that form no star, confirmed in the order detection proposed them, all landing in one family; a group confirmed in one command; `--canonical` is no longer accepted; the listing no longer marks a member

## 6. Close out

- [x] 6.1 `make validate` green — lint, mypy, pytest. No loosened config, no `# type: ignore` added to make an error disappear
- [x] 6.2 `README.md`: the "Как хранится семья" section describes the pointer, the `COALESCE(operator_id, tg_id)` key and the depth-one rule. All three change. Say what replaces them and how a notebook joins the view to exclude intra-family edges — that is the whole point of the feature and it is currently undocumented
- [x] 6.3 `src/itgraph/CLAUDE.md`: `db/channels.py` is no longer "the only writer of `operator_id`"; `db/affiliation.py` gains the view
- [x] 6.4 `openspec validate drop-canonical-channel --strict` green
- [x] 6.5 `alembic upgrade head` on the working database, after the dump from 1.2
- [x] 6.6 Confirm the families survived: 14 families and 17 pairs must read the same through the view as they did through `operator_id`. Then assemble the vacancies group — the case this change exists for — and check it comes out as one family
