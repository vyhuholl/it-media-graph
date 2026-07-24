## Context

Derivation reads `raw_messages` and joins nothing. The fix is a predicate on which sources it streams — small enough that the only things worth writing down are the two judgement calls inside it and one interaction with a phase that does not exist yet.

## Decisions

### Statuses are listed, not excluded

The predicate names `candidate`, `seed` and `maybe` rather than saying "not `rejected`".

Both read the same today. They differ the next time `channel_status` gains a value: an allowlist silently drops it from the sources, which shows up as edges vanishing on the next rebuild, while a denylist silently admits it, which shows up as nothing at all. The noisy failure is the safer default for a table that grows the inventory.

### `candidate` is a source, and that is deliberate

Candidates have no collected history today, so the status contributes nothing in practice. It stays in the list because history and review are independent: a channel can be backfilled while its decision is pending, and treating that as out of scope would quietly discard collected data on a technicality.

`maybe` is there for the same reason — it is a deferred decision, not a negative one.

### Rejected channels stay valid targets

Only sources are filtered. An in-scope channel forwarding a rejected one is a fact about the source: it feeds its out-degree, the variety of what it reposts, and eventually the suspicion that it belongs to a crowd other than the one it was accepted into.

Dropping those edges would understate exactly the channels most worth looking at, and it would do so invisibly, since nothing downstream could tell a missing edge from an absent one.

### Chats are excluded now, for a problem that arrives later

No discussion chat has stored messages today, so this clause is inert. It is here because the comments phase will put user-written messages into storage, and a forward inside a comment would otherwise become an edge in the channel graph — a person's repost recorded as a publication by the chat.

That failure would be silent, plausible-looking and mixed in with real data. One clause now is cheaper than finding it in a clustering result later.

### The filter belongs in the cursor

The predicate joins `channels` in the streaming query rather than checking each payload in Python. Out-of-scope payloads are then never read, which keeps the guarantee "an out-of-scope source discovers nothing" structural: there is no code path where a reference from such a channel could reach the discovery step.

### Inventory rows are not retracted

A rebuild truncates `edges` and `pending_mentions`, both derived. It does not touch `channels`, so a candidate discovered through a source rejected afterwards remains, with its original `discovered_via` intact.

This follows from channels never being deleted, and it is honest: the reference did happen, at a time when the source was in scope. Reviewing it costs one triage decision; reconstructing a deleted record would cost a network round-trip.

`pending_mentions` needs no equivalent argument — being derived, it is emptied and rebuilt from in-scope sources only, so stale usernames disappear on their own.

## Alternatives considered

- **Filtering edges at analysis time.** Rejected: it handles the edges and not the inventory rows, which are the part that cannot be undone downstream.
- **Deleting channels discovered through a rejected source.** Rejected: contradicts the retention rule, and the provenance recorded is accurate.
- **Excluding `rejected` rather than listing the rest.** Rejected above, on failure-mode grounds.

## Deferred

- Retracting or re-scoring candidates whose only referrer was later rejected. A scoring concern, and the triage change is where scoring appears.
- Whether comment data lands in `raw_messages` at all. If it gets its own table, the chat clause becomes redundant rather than wrong.

## Testing

Fixtures need a rejected source with stored history alongside an in-scope one, plus a discussion chat with stored messages — the latter cannot occur yet, which is precisely why it needs a fixture rather than a manual check.

The rebuild behaviour deserves its own test, since it is the part users will read as a bug: derive, reject the source, derive again without `--rebuild` and assert the edges are still there, then rebuild and assert they are gone.