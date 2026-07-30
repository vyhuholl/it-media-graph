"""Storing runs and candidates.

The load-bearing property here is that re-running detection refreshes the
measurement and never the decision — that is what lets a threshold be
re-tried as often as the operator likes.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.affiliation.detect import Candidate, Detection
from itgraph.affiliation.signals import Thresholds, Weights
from itgraph.db.affiliation import (
    count_candidates_by_decision,
    list_candidates,
    load_inventory,
    record_run,
    upsert_candidates,
)
from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import (
    AboutDirection,
    AffiliationDecision,
    DiscoverySource,
    EdgeKind,
)
from itgraph.db.session import Database

A = 1001
B = 1002
C = 1003

BOTH_KINDS = [EdgeKind.FORWARD, EdgeKind.MENTION]


async def seed(database: Database, *, in_scope: bool = True) -> None:
    """Three channels, seeds by default.

    The listing hides a pair in which neither channel is a seed, so a
    fixture of unreviewed candidates would be invisible to every
    assertion below for a reason that has nothing to do with what is
    being tested.
    """
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=tg_id,
                    username=f"example_{tg_id}",
                    title=f"Example {tg_id}",
                    is_chat=False,
                )
                for tg_id in (A, B, C)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        if in_scope:
            await session.execute(text("UPDATE channels SET status = 'seed'"))


def a_detection(refs_outside: int = 0) -> Detection:
    return Detection(
        candidates=[],
        channels_scored=3,
        with_description=1,
        refs_outside_inventory=refs_outside,
    )


async def store(
    database: Database,
    candidates: list[Candidate],
    thresholds: Thresholds | None = None,
) -> int:
    async with database.session() as session:
        run_id = await record_run(
            session,
            a_detection(),
            thresholds=thresholds or Thresholds(),
            weights=Weights(),
            edge_kinds=BOTH_KINDS,
        )
        await upsert_candidates(session, candidates, run_id=run_id)
        return run_id


async def test_a_run_records_its_parameters_and_coverage(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await record_run(
            session,
            a_detection(refs_outside=134),
            thresholds=Thresholds(max_share_min=0.55),
            weights=Weights(about=2.0),
            edge_kinds=[EdgeKind.FORWARD],
        )

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT max_share_min, weight_about, edge_kinds, "
                    "refs_outside_inventory FROM affiliation_runs"
                )
            )
        ).one()

    assert row == (0.55, 2.0, ["forward"], 134)


async def test_a_candidate_is_stored_with_its_evidence(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [
            Candidate(
                pair=(A, B),
                score=1.4,
                about_direction=AboutDirection.MUTUAL,
                shared_token="gonzo",
                shared_token_channels=2,
            )
        ],
    )

    async with database.session() as session:
        rows = await list_candidates(session)

    assert len(rows) == 1
    assert rows[0].channel_a == A
    assert rows[0].about_direction == "mutual"
    assert rows[0].shared_token == "gonzo"
    assert rows[0].username_a == f"example_{A}"


async def test_a_second_identical_run_writes_no_second_row(
    database: Database,
) -> None:
    await seed(database)
    candidate = Candidate(pair=(A, B), score=1.4, shared_token="gonzo")
    await store(database, [candidate])
    second_run = await store(database, [candidate])

    async with database.session() as session:
        rows = await list_candidates(session)
        run_id = (
            await session.execute(
                text("SELECT run_id FROM affiliation_candidates")
            )
        ).scalar_one()

    assert len(rows) == 1
    assert run_id == second_run


async def test_a_new_threshold_refreshes_the_measurement(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [
            Candidate(
                pair=(A, B), score=0.4, out_share=0.72, out_share_edges=25
            )
        ],
    )
    await store(
        database,
        [Candidate(pair=(A, B), score=1.9, shared_token="gonzo")],
    )

    async with database.session() as session:
        rows = await list_candidates(session)

    assert rows[0].score == 1.9
    assert rows[0].shared_token == "gonzo"
    # The share no longer fires under the new thresholds, so it must stop
    # claiming it did.
    assert rows[0].out_share is None


async def test_a_recorded_decision_survives_a_rerun(
    database: Database,
) -> None:
    await seed(database)
    await store(database, [Candidate(pair=(A, B), score=0.4)])

    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE affiliation_candidates SET decision = 'confirmed', "
                "canonical_id = :canonical, decided_at = :now, "
                "decision_note = 'same author'"
            ),
            {"canonical": A, "now": datetime.now(UTC)},
        )

    await store(database, [Candidate(pair=(A, B), score=1.9)])

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, canonical_id, decision_note, score "
                    "FROM affiliation_candidates"
                )
            )
        ).one()

    assert row == ("confirmed", A, "same author", 1.9)


async def test_a_decided_pair_leaves_the_review_list_but_stays_readable(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [Candidate(pair=(A, B), score=0.9), Candidate(pair=(A, C), score=0.5)],
    )

    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE affiliation_candidates SET decision = 'rejected', "
                "decided_at = :now WHERE channel_b = :b"
            ),
            {"now": datetime.now(UTC), "b": B},
        )

    async with database.session() as session:
        pending = await list_candidates(session)
        everything = await list_candidates(session, include_decided=True)
        counts = await count_candidates_by_decision(session)

    assert [row.channel_b for row in pending] == [C]
    assert len(everything) == 2
    assert counts[AffiliationDecision.REJECTED] == 1


async def test_candidates_come_back_ranked_and_bounded(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [Candidate(pair=(A, B), score=0.4), Candidate(pair=(A, C), score=1.8)],
    )

    async with database.session() as session:
        rows = await list_candidates(session, limit=1)

    assert [row.channel_b for row in rows] == [C]


async def test_a_pair_stored_the_wrong_way_round_is_refused(
    database: Database,
) -> None:
    """The check is what makes the primary key deduplicate an unordered
    pair, so it has to hold against a direct write too."""
    await seed(database)
    run_id = await store(database, [])

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await session.execute(
                text(
                    "INSERT INTO affiliation_candidates "
                    "(channel_a, channel_b, score, run_id) "
                    "VALUES (:b, :a, 1.0, :run)"
                ),
                {"a": A, "b": B, "run": run_id},
            )


async def test_the_inventory_load_builds_the_family_key(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await session.execute(
            text("UPDATE channels SET operator_id = :a WHERE tg_id = :b"),
            {"a": A, "b": B},
        )

    async with database.session() as session:
        inventory = await load_inventory(session, edge_kinds=BOTH_KINDS)

    # A member answers with the canonical channel, a solo channel with
    # itself — the same expression the analysis uses.
    assert inventory.family_of[B] == A
    assert inventory.family_of[A] == A
    assert inventory.family_of[C] == C
    assert inventory.known_channels == frozenset({A, B, C})


async def test_the_inventory_load_lowercases_usernames(
    database: Database,
) -> None:
    """So a handle parsed out of a description matches a stored one."""
    await seed(database)
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE channels SET username = 'MixedCase' WHERE tg_id = :a"
            ),
            {"a": A},
        )

    async with database.session() as session:
        inventory = await load_inventory(session, edge_kinds=BOTH_KINDS)

    assert inventory.usernames[A] == "mixedcase"


async def test_a_pair_with_no_seed_in_it_is_not_shown(
    database: Database,
) -> None:
    """Reviewing two channels neither of which is in scope answers a
    question nobody has asked yet."""
    await seed(database, in_scope=False)
    await store(database, [Candidate(pair=(A, B), score=1.4)])

    async with database.session() as session:
        rows = await list_candidates(session)

    assert rows == []


async def test_one_seed_is_enough_to_show_a_pair(database: Database) -> None:
    await seed(database, in_scope=False)
    await store(database, [Candidate(pair=(A, B), score=1.4)])

    async with database.session() as session:
        await session.execute(
            text("UPDATE channels SET status = 'seed' WHERE tg_id = :a"),
            {"a": A},
        )

    async with database.session() as session:
        rows = await list_candidates(session)

    assert [row.channel_b for row in rows] == [B]


async def test_the_hidden_pair_is_stored_and_reachable(
    database: Database,
) -> None:
    """Hidden from the reading, not dropped from the table: a channel
    accepted next week turns its pair into one worth reviewing without
    anything being recomputed."""
    await seed(database, in_scope=False)
    await store(database, [Candidate(pair=(A, B), score=1.4)])

    async with database.session() as session:
        rows = await list_candidates(session, seeds_only=False)

    assert [row.channel_b for row in rows] == [B]
