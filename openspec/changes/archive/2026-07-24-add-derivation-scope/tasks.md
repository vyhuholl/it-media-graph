## 1. Source selection

- [x] 1.1 Add the scope predicate to the streaming query that feeds derivation: source channels must have status `candidate`, `seed` or `maybe`, and must not be discussion chats. List the statuses explicitly rather than excluding `rejected`.
- [x] 1.2 Keep the predicate in the cursor, not in the loop over payloads. An out-of-scope payload must never be read, so that no code path can carry one of its references to the discovery step.
- [x] 1.3 Leave target handling untouched: an edge to a rejected channel is still written, and a rejected channel is still a valid endpoint.
- [x] 1.4 Report the number of channels read as sources, so a predicate that silently matches nothing is visible in the run output.

## 2. Tests

- [x] 2.1 Extend the fixtures with a rejected channel holding stored history, and with a discussion chat holding stored messages. Neither case can be produced by hand today, which is why both need fixtures.
- [x] 2.2 Cover the spec scenarios: sources selected by status, chats never sources, an out-of-scope source discovering neither channels nor pending mentions, and rejected channels still valid as targets.
- [x] 2.3 Test the rebuild behaviour end to end, since this is the part that reads as a bug: derive, reject the source, derive again without `--rebuild` and assert its edges are still present, then rebuild and assert they are gone.
- [x] 2.4 Assert that a rebuild leaves `channels` untouched — a candidate discovered through a source rejected afterwards keeps its row and its original discovery source.

## 3. Wrap-up

- [x] 3.1 Run `make validate` and fix everything it reports. Do not close the change while validate is red.
- [x] 3.2 Update `README.md` **in Russian**: which statuses count as derivation sources, that rejecting a channel does not retroactively remove its edges until `derive --rebuild`, and that channels it introduced earlier stay in the inventory.