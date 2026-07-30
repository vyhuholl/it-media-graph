## Why

A family of channels is a **set**. The current model stores it as a **star**: one canonical channel, every other member pointing at it. Nothing in the data justifies that shape, and imposing it makes real families unrecordable.

The case that exposed it is a group of job-vacancy channels the operator knows share an author. `itgraph affiliates` proposes the pairs it can see among them, and those pairs form an arbitrary graph — `@foranalysts`↔`@jobforjunior`, `@foranalysts`↔`@forpython`, `@jobforpr`↔`@jobforjunior`, `@forallsales`↔`@jobforjunior` — not a star around any one channel. Confirming them in the order the ranking offers them hits a wall twice:

- Confirm the first pair, and the second is refused — *"is itself in family X; name that channel as canonical instead"*. The operator must work out, before starting, which channel the hub is, and phrase every later confirmation around it.
- Once two sub-groups have formed independently, the pair that bridges them is refused outright — *"merging two families is a separate decision"* — with no command that performs the merge.

So which family comes out depends on the order the pairs were confirmed in, and some families cannot be assembled at all. That is not a threshold to tune; the representation is wrong.

The canonical channel was introduced by analogy with `linked_to`, where it is correct — a discussion chat genuinely belongs to a parent channel. Between an author's own channels there is no such asymmetry, and none of the three questions in `docs/PLAN.md` asks which of them is the main one. It is a distinction the project invented, has never read, and now pays for.

## What Changes

- **BREAKING: `channels.operator_id` is removed**, and with it the notion of a canonical channel. No member of a family is privileged over another.
- **A family becomes a set of channels of any size**, assembled from confirmed pairs. Confirming a pair between two channels already in the same family is a no-op; confirming one that bridges two families **merges them**, which is the natural reading of "these two share an author" and is what the current model refuses.
- **Confirmation stops asking which channel is canonical.** `itgraph family @a @b` records that the two share an author, and that is the whole statement. Order of confirmation no longer changes the result.
- **A whole group is confirmable in one command.** `itgraph family @a @b @c @d` records every pair among the channels named. The operator often knows the group outright — the vacancies case is five channels — while detection found only some of the pairs among them, and saying so should not take four commands.
- **The re-canonicalize form is removed**: `itgraph family @x --canonical @x` has nothing left to do. **BREAKING** for anyone with it in a script; it is a hand-run command a week old, so the blast radius is one person.
- **`--withdraw` keeps working and gains a defined meaning under sets:** it removes the confirmed pair, and the family splits if that pair was the only thing holding two parts together. Removing one *statement* must not silently dissolve statements the operator never withdrew.
- **The inventory still answers "which family is this channel in"** — `itgraph channels --family <ref>` keeps working and keeps returning every member. What disappears from the output is the `canonical`/`member` column, because there is nothing to put in it.
- **Existing data is carried over, not dropped.** 17 confirmed pairs across 14 families, 2 rejections. Every one is preserved: the pairs are already stored in `affiliation_candidates`, which is the shape the new model reads, so nothing has to be reconstructed by hand.

Out of scope:

- **Splitting a family by hand**, beyond withdrawing a specific pair. If the pairs say one family and the operator disagrees, the disagreement is with a pair, and withdrawing it is the honest way to say so.
- **Changing detection.** `itgraph affiliates`, the four signals, the thresholds and the evidence are untouched. This changes only what a confirmation records.
- **Naming a family.** A label — "the vacancies group" — is a plausible convenience and answers no question the analytics ask. Not now.

## Capabilities

### New Capabilities

None. This removes a distinction from an existing capability rather than adding one.

### Modified Capabilities

- `channel-affiliation`: **Family Shape** is rewritten — a family is a set with no canonical member, of any size, and the family key is no longer a pointer to a privileged channel. **Family Confirmation** loses the canonical argument, gains merge-on-bridge, and loses the re-canonicalize operation; `--withdraw` gains the split rule.
- `channel-inventory`: **Channel Record** loses the family pointer; **Inventory Listing** keeps the family filter and loses the canonical marker.

## Impact

- **Schema change with data movement**, unlike the additive revision that introduced it. `channels.operator_id` is dropped; whatever replaces it as the family key is populated from the confirmed pairs in the same revision, so the migration is reversible in the only direction that matters — the pairs survive either way, and they are the source of truth.
- `src/itgraph/db/models.py` — `operator_id` and its check constraint go; the family key arrives.
- `src/itgraph/db/channels.py` — `confirm_affiliation` loses its `canonical` argument and its two refusals; `recanonicalize_family` is deleted; `withdraw_affiliation` gains the split; `count_families` and the family filter change shape.
- `src/itgraph/db/affiliation.py` — `canonical_id` on a candidate records a decision that no longer exists.
- `src/itgraph/cli.py` — `--canonical` is removed from `itgraph family`, with it the one-channel promote form and the argument validation around it; the `canonical`/`member` column leaves `itgraph channels --family`.
- `src/itgraph/affiliation/detect.py` — the exclusion "two channels already sharing a family key" now reads a set rather than a pointer.
- Tests: `test_affiliation_family.py` is largely rewritten — most of its cases exist to pin the canonical rules. `test_cli.py`, `test_affiliation_db.py`, `test_db.py`.
- `README.md` — the "Как хранится семья" section describes the pointer and the depth-one rule, both of which go.
- **The depth-one invariant disappears with the pointer it protected**, and with it the one rule this project enforces in application code because a `CHECK` could not see it. That is a simplification worth naming: the shape that needed guarding is the shape being removed.
