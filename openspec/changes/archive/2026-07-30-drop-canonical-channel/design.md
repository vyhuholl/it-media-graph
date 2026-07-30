## Context

`channels.operator_id` points at the canonical channel of a family, and every member points at the same one. That shape came from `linked_to`, where it is right: a discussion chat genuinely belongs to a parent. Between an author's own channels there is no such asymmetry, and the star it forces cannot hold what detection actually finds.

The pairs `itgraph affiliates` proposes among one author's channels form an arbitrary graph. Confirming them in the order they are ranked hits two refusals — *"is itself in family X"* when a second pair reaches a channel already placed, and *"merging two families is a separate decision"* when a pair bridges two groups assembled independently. The result depends on the order of confirmation, and some groups cannot be assembled at all.

The column is also, as it turns out, **entirely redundant**. Measured against the working database: 17 confirmed pairs, 17 channels carrying `operator_id`, and **0** `operator_id` values with no confirming pair behind them. Every fact the column holds is already in `affiliation_candidates`. Largest family today: 3 channels, 14 families.

So this is not a migration of data from one representation to another. It is the deletion of a derived column that was never the source of truth, and the promotion of the pairs — which always were — to being read directly.

## Goals / Non-Goals

**Goals**

- A family is a set of any size, assembled from whatever pairs were found, in whatever order they are confirmed.
- Confirming a pair is a complete statement on its own. No argument about which channel leads.
- Family membership cannot disagree with the confirmed pairs, by construction rather than by discipline.
- Analysis keeps a cheap way to ask "do these two edge endpoints share a family".

**Non-Goals**

- Changing detection. The signals, thresholds, evidence and ranking are untouched.
- Splitting a family other than by withdrawing a specific pair.
- Naming or annotating a family.
- Preserving which channel *was* canonical. That fact is being deliberately destroyed; it answers no question.

## Decisions

### 1. Family membership is derived from the confirmed pairs, and stored nowhere

No replacement column. A family is the connected component of the graph whose vertices are channels and whose edges are the confirmed pairs in `affiliation_candidates`.

This is the project's own principle applied to a fact that was quietly violating it. `docs/PLAN.md`: *anything derivable can be deferred for free*; the raw layer is immutable and everything on top is a re-runnable transformation. `operator_id` was a stored summary of the pairs, maintained by hand, able to drift from them — and the reason the invariant needed enforcing in application code at all.

*Alternative considered — a materialized `family_id`, recomputed in full on every confirmation.* Gives a plain column to join on, cannot drift if the recompute is total. Rejected because it stores what a query already answers, and because "recompute in full on every change" is the same computation as the view below, only with a write and a chance to be stale between them.

*Alternative considered — incremental union-find maintaining a `family_id`.* Rejected outright: merging is easy incrementally, splitting on withdrawal is not, and the whole reason this change exists is that clever incremental family bookkeeping got the shape wrong once already.

**The split rule costs no code.** Withdrawing a pair removes an edge; the components recompute. A withdrawal that leaves the two channels connected by another chain changes nothing, and one that does not splits the family — both fall out of the derivation rather than being implemented and tested as branches.

### 2. The derivation is a database view

```sql
CREATE VIEW channel_families AS
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM affiliation_candidates WHERE decision = 'confirmed'
    UNION            -- not UNION ALL: A–B, B–C, A–C is a cycle
    SELECT channel_b, channel_a
      FROM affiliation_candidates WHERE decision = 'confirmed'
),
reach AS (
    SELECT channel_id, reached FROM linked
    UNION
    SELECT r.channel_id, l.reached
      FROM reach r JOIN linked l ON l.channel_id = r.reached
)
SELECT channel_id, LEAST(MIN(reached), channel_id) AS family_key
  FROM reach GROUP BY channel_id;
```

One row per channel that is in a family of more than one — roughly 30 rows today, and the recursion walks only the affiliated subgraph rather than all 2093 channels. The family of any channel is then

```sql
COALESCE(f.family_key, c.tg_id)
```

which is the same shape the analysis already uses, with a left join in place of a column read. An edge is intra-family exactly when its two endpoints' keys are equal — unchanged.

`UNION`, never `UNION ALL`: the pairs among one author's channels contain cycles by construction (A–B, B–C, A–C is exactly the case that broke the old model), and `UNION ALL` would not terminate.

*Why a view rather than a Python function.* The analysis this whole feature exists to serve runs over `edges` in SQL and pandas, and a join is what that wants. A Python union-find would be about twenty lines and would then have to be duplicated in, or imported into, every notebook — and the CLI and detection would still need it. One definition in one place, readable from either side.

*Cost accepted:* this is the project's first view. Far less risk than the trigger considered and rejected last time — read-only, no write path, no surprise on insert — and it is created and dropped by an ordinary revision.

### 3. `family_key` is a label, not a canonical channel

The key is the smallest channel id in the set. It is a stable, deterministic name for the component and **nothing else** — no command treats that channel differently, and it is deliberately never printed. `itgraph channels --family <ref>` takes any member and lists the members.

Naming the risk because it is the obvious one: a key that looks like a channel id will be read as "the main channel" by the next person to open the table, which is exactly what this change removes. Mitigated by never surfacing it, and by the view's name and comment saying what it is. *Alternative — a surrogate id from a `families` table.* Rejected: it would need its own identity preserved across recomputes, which is bookkeeping this design exists to avoid, and the family is addressed by a member anyway.

### 4. Confirmation becomes a statement about two channels and nothing else

`confirm_affiliation(session, a, b, note=...)` — no `canonical`. It records the pair and returns. Gone with the argument:

- the check that the canonical channel is one of the two,
- the depth-one enforcement, and the comment explaining why a `CHECK` could not do it,
- the refusal when the two sides are in different families — **that case is now the merge**, and it needs no code either: the pair is recorded and the components join,
- `recanonicalize_family` entirely.

Confirming a pair already inside one family records the pair and changes no membership. It succeeds, because "these two share an author" is true and worth recording even when it is already implied.

`canonical_id` leaves `affiliation_candidates`, along with the check constraint tying it to `decision = 'confirmed'`. It records an answer to a question no longer asked.

### 5. A whole group is confirmable in one command, and it stores every pair

`itgraph family @a @b @c @d` — two channels or twenty, the statement is the same: these share an author.

**It records every pair among them, not a chain.** Four channels give six pairs, five give ten. A chain (`a–b`, `b–c`, `c–d`) would be three rows and the wrong claim: withdrawing the middle one would split a family the operator asserted as whole, and which pairs exist would depend on the order the channels were typed in. Every pair is what "all of these are one family" actually means, and it makes the family robust to any single withdrawal — which is the correct outcome, since removing one piece of evidence should not undo an assertion made about the set.

Growth is quadratic in the size of one command, not in the inventory: a group of twenty — far beyond anything plausible — is 190 rows in a table holding hundreds.

A pair among them that detection already proposed keeps its `origin` of `signal`; only pairs the operator introduced are marked as theirs. That already falls out of the existing upsert, which touches the decision columns and not `origin`.

### 6. The migration drops a column and proves it first

The revision drops `channels.operator_id`, its foreign key and its check; drops `affiliation_candidates.canonical_id` and its constraint; creates the view.

**It verifies before it drops.** The claim that `operator_id` is redundant is true of this database today (0 orphans, measured) and is not guaranteed by anything in the schema. The revision counts `operator_id` values with no confirming pair behind them and **fails loudly** rather than dropping data if the count is not zero. A migration that quietly discards a fact it assumed was duplicated is exactly the kind of thing the backup rule exists for, and the assumption is cheap to check.

**The downgrade is honestly lossy.** It recreates the column and repopulates it by picking, per family, an arbitrary canonical channel — because *which* channel was canonical is the fact being destroyed. The docstring says so rather than pretending the round-trip is clean.

## Risks / Trade-offs

- **The analysis query gains a join it did not need.** → It is a left join to a ~30-row view, and it replaces a column that could be wrong. The expression on either side of it is unchanged.
- **A merge is now silent where it used to be refused.** Confirming a bridging pair joins two families with no confirmation step. → That is the intended behaviour, and the refusal it replaces was blocking a legitimate operation with no alternative. The command reports the resulting family size, so a merge that surprises the operator is visible in the output rather than only in the table.
- **`family_key` will be mistaken for a canonical channel by someone reading the view.** → Never printed, named `family_key` rather than anything channel-shaped, and documented where it is defined.
- **The recursive CTE is the first non-trivial SQL in the project.** → It is bounded by the confirmed pairs, not by the inventory: tens of rows now, and it would take thousands of hand-confirmed pairs to matter.
- **Dropping a column is irreversible in the way that matters.** → Full dump first, per the backup rule; the revision is exercised up *and* down on a scratch database before the working one; and the pre-drop verification means the only thing lost is the canonical designation itself.

## Migration Plan

1. Full dump, verified with `pg_restore --list` — the standard rule, and this is the first revision in the project that drops a column holding hand-made decisions.
2. Verify the revision up and down on a scratch database whose name ends in `_test`, reading `alembic upgrade --sql` first.
3. Run the redundancy check against the working database before upgrading it; it is the same query the revision runs, and seeing it return zero by hand is cheaper than reading a failed migration.
4. `alembic upgrade head`.
5. Confirm the families survived: the 14 families and 17 pairs must read the same through the view as they did through `operator_id`.

Rollback is `alembic downgrade`, which restores the column with an arbitrary canonical per family. The pairs — the thing that matters — are untouched by both directions.

## Open Questions

1. ~~**Should `itgraph family` accept more than two channels at once?**~~ **Settled: yes.** See decision 5.
2. ~~**Should a rejected pair prevent a merge that other pairs imply?**~~ **Settled: no, the component wins.** A rejection means "this pair is not evidence", not "these two are not family". If A–B and B–C are confirmed and A–C rejected, all three are one family and the rejection keeps doing its only job — stopping A–C being proposed again. The alternative would make a rejection an assertion of *non*-affiliation strong enough to veto two positive statements, which is more than the operator was asked when they rejected it.
3. **Does anything want the family key to be stable across recomputation?** Nothing currently does — families are addressed by a member. If a later export or a saved notebook wants to name a family, the smallest-id label will move when a smaller channel joins.
