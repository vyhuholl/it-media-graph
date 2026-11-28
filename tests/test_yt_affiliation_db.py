"""Storing YouTube runs and candidates.

``test_affiliation_db.py``'s load-bearing property, carried over: a
re-run refreshes the measurement and never the decision, and a pair
hidden for want of a seed is stored anyway so accepting the channel
next week needs no recomputation. What is YouTube's own here is the
read-time join — the titles and handles live in the payloads and in no
copied column, so a gone payload must degrade the row to ids and
statuses rather than break the reading.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from ytfakes import channel_id, channel_resource

from itgraph.db.models import (
    AffiliationDecision,
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtLang,
)
from itgraph.db.session import Database
from itgraph.db.yt_affiliation import (
    list_candidates,
    load_inventory,
    record_run,
    upsert_candidates,
)
from itgraph.db.yt_channels import (
    confirm_affiliation,
    count_shown_pending,
    create_channels,
    reject_affiliation,
    reject_all,
    store_payloads,
)
from itgraph.yt.affiliation.detect import Candidate, Detection
from itgraph.yt.affiliation.run import run_detection
from itgraph.yt.affiliation.signals import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    Thresholds,
    Weights,
    YtRefDirection,
    ordered,
)

A = channel_id(1)
B = channel_id(2)
C = channel_id(3)
D = channel_id(4)

# Two ids whose two orders disagree: code points rank the uppercase B
# of UCB… before the lowercase a of UCa…, a linguistic collation — the
# working database's en_US.utf8 — ranks a before B, the other way
# round. About a fifth of all pairs of real ids disagree like this,
# which is why the pair-order comparison and the family key are
# collated to bytes.
DIVERGENT_LOWER = "UCa" + "x" * 20 + "1"
DIVERGENT_UPPER = "UCB" + "x" * 20 + "2"


def payload(number: int, *, handle: str | None = None) -> dict[str, object]:
    return channel_resource(number, handle=handle or f"example{number}")


async def seed(database: Database, *, in_scope: bool = True) -> None:
    """Three channels, seeds by default, with the payloads detection reads.

    The listing hides a pair in which neither channel is a seed, for the
    same reason as Telegram's fixture: an unreviewed candidate list
    would be invisible to the assertions below for a reason that has
    nothing to do with what is being tested.
    """
    async with database.session() as session:
        await create_channels(session, [A, B, C])
        await store_payloads(
            session,
            {
                channel: payload(number)
                for number, channel in enumerate((A, B, C), 1)
            },
            fetched_at=datetime.now(UTC),
        )
        if in_scope:
            # A seed is labelled, the inventory's check insists on it.
            for channel in (A, B, C):
                record = await session.get(YtChannel, channel)
                assert record is not None
                record.status = ChannelStatus.SEED
                record.kind = YtChannelKind.PERSONAL
                record.lang = YtLang.EN


def a_detection(refs_outside: int = 0) -> Detection:
    return Detection(
        candidates=[],
        channels_scored=3,
        with_payload=3,
        with_description=3,
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
            a_detection(refs_outside=37),
            thresholds=Thresholds(max_corroborated_channels=45),
            weights=Weights(group=1.5),
        )

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT min_token_length, max_token_channels, "
                    "max_corroborated_channels, weight_reference, "
                    "weight_group, weight_token, channels_scored, "
                    "with_payload, with_description, refs_outside_inventory "
                    "FROM yt_affiliation_runs"
                )
            )
        ).one()

    assert row == (4, 3, 45, 1.0, 1.5, 0.6, 3, 3, 3, 37)


async def test_a_run_leaves_the_payloads_it_read_alone(
    database: Database,
) -> None:
    """What YouTube said is the one thing detection may never touch:
    every input was collected by a refresh, and a refresh is the only
    writer the raw layer has."""
    await seed(database)
    async with database.session() as session:
        before = (
            await session.execute(
                text(
                    "SELECT channel_id, payload FROM yt_raw_channels ORDER BY 1"
                )
            )
        ).all()

    await run_detection(
        database, thresholds=DEFAULT_THRESHOLDS, weights=DEFAULT_WEIGHTS
    )

    async with database.session() as session:
        after = (
            await session.execute(
                text(
                    "SELECT channel_id, payload FROM yt_raw_channels ORDER BY 1"
                )
            )
        ).all()
        # The run did work over those payloads, so the comparison below
        # is of a run that read something, not of one that looked at
        # an empty table.
        proposed = await session.scalar(
            text("SELECT count(*) FROM yt_affiliation_candidates")
        )

    assert after == before
    assert proposed == 3


async def test_a_candidate_is_stored_with_its_evidence(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [
            Candidate(
                pair=ordered(A, B),
                score=1.4,
                ref_direction=YtRefDirection.MUTUAL,
                shared_token="gonzo",
                shared_token_channels=2,
            )
        ],
    )

    async with database.session() as session:
        rows = await list_candidates(session)

    assert len(rows) == 1
    assert rows[0].channel_a == A
    assert rows[0].ref_direction == "mutual"
    assert rows[0].shared_token == "gonzo"
    # Title and handle joined from the payload at read time, stored nowhere.
    assert rows[0].title_a == "Fake Channel 1"
    assert rows[0].handle_a == "example1"
    assert rows[0].status_a.value == "seed"


async def test_a_candidate_whose_payload_is_gone_prints_ids(
    database: Database,
) -> None:
    """The record and the review stay; only what YouTube said leaves."""
    await seed(database)
    await store(database, [Candidate(pair=ordered(A, B), score=1.4)])

    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_raw_channels"))
        rows = await list_candidates(session)

    assert len(rows) == 1
    assert rows[0].title_a is None
    assert rows[0].handle_a is None
    assert rows[0].status_a.value == "seed"


async def test_a_second_identical_run_writes_no_second_row(
    database: Database,
) -> None:
    await seed(database)
    candidate = Candidate(pair=ordered(A, B), score=1.4, shared_token="gonzo")
    await store(database, [candidate])
    second_run = await store(database, [candidate])

    async with database.session() as session:
        rows = await list_candidates(session)
        run_id = (
            await session.execute(
                text("SELECT run_id FROM yt_affiliation_candidates")
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
                pair=ordered(A, B),
                score=1.0,
                corroborated_token="google",
                corroborated_token_channels=28,
            )
        ],
    )
    await store(
        database,
        [Candidate(pair=ordered(A, B), score=1.9, shared_token="gonzo")],
    )

    async with database.session() as session:
        rows = await list_candidates(session)

    assert rows[0].score == 1.9
    assert rows[0].shared_token == "gonzo"
    # The group no longer fires under the new thresholds, so it must stop
    # claiming it did.
    assert rows[0].corroborated_token is None
    assert rows[0].corroborated_token_channels is None


async def test_a_recorded_decision_survives_a_rerun(
    database: Database,
) -> None:
    await seed(database)
    await store(database, [Candidate(pair=ordered(A, B), score=0.4)])

    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE yt_affiliation_candidates SET decision = 'confirmed', "
                "decided_at = :now, decision_note = 'same owner'"
            ),
            {"now": datetime.now(UTC)},
        )

    await store(database, [Candidate(pair=ordered(A, B), score=1.9)])

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, decision_note, score "
                    "FROM yt_affiliation_candidates"
                )
            )
        ).one()

    assert row == ("confirmed", "same owner", 1.9)


async def test_a_decided_pair_leaves_the_review_list_but_stays_readable(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [
            Candidate(pair=ordered(A, B), score=0.9),
            Candidate(pair=ordered(A, C), score=0.5),
        ],
    )

    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE yt_affiliation_candidates SET decision = 'rejected', "
                "decided_at = :now WHERE channel_b = :b"
            ),
            {"now": datetime.now(UTC), "b": B},
        )

    async with database.session() as session:
        pending = await list_candidates(session)
        everything = await list_candidates(session, include_decided=True)

    assert [row.channel_b for row in pending] == [C]
    assert len(everything) == 2


async def test_reject_all_clears_the_list_and_only_the_list(
    database: Database,
) -> None:
    """The bulk statement answers the list the operator reads: shown
    pending pairs are rejected with when and why, a pair decided one by
    one keeps that decision, and a hidden pair — no seed on either side —
    stays pending for a channel accepted later."""
    await seed(database, in_scope=False)
    async with database.session() as session:
        await create_channels(session, [D])
        # A and B into scope, C and D left outside it.
        for channel in (A, B):
            record = await session.get(YtChannel, channel)
            assert record is not None
            record.status = ChannelStatus.SEED
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN
    await store(
        database,
        [
            Candidate(pair=ordered(A, B), score=0.9),
            Candidate(pair=ordered(A, C), score=0.7),
            Candidate(pair=ordered(B, C), score=0.6),
            Candidate(pair=ordered(C, D), score=0.5),
        ],
    )
    async with database.session() as session:
        await reject_affiliation(session, A, B, note="one by one")

    async with database.session() as session:
        shown = await count_shown_pending(session)
        rejected = await reject_all(session)

    assert (shown, rejected) == (2, 2)
    async with database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT channel_a, channel_b, decision::text, "
                    "decided_at IS NOT NULL, decision_note "
                    "FROM yt_affiliation_candidates "
                    "ORDER BY channel_a, channel_b"
                )
            )
        ).all()

    assert rows == [
        (A, B, "rejected", True, "one by one"),
        (A, C, "rejected", True, "--reject-all"),
        (B, C, "rejected", True, "--reject-all"),
        (C, D, "pending", False, None),
    ]


async def test_reject_all_with_nothing_shown_writes_nothing(
    database: Database,
) -> None:
    await seed(database, in_scope=False)
    await store(database, [Candidate(pair=ordered(A, B), score=1.4)])

    async with database.session() as session:
        assert await count_shown_pending(session) == 0
        assert await reject_all(session) == 0

    async with database.session() as session:
        rows = await list_candidates(
            session, seeds_only=False, include_decided=True
        )

    assert [row.decision for row in rows] == [AffiliationDecision.PENDING]


async def test_reject_all_note_is_the_operators_when_given(
    database: Database,
) -> None:
    await seed(database)
    await store(database, [Candidate(pair=ordered(A, B), score=0.9)])

    async with database.session() as session:
        await reject_all(session, note="first run, all noise")

    async with database.session() as session:
        note = await session.scalar(
            text("SELECT decision_note FROM yt_affiliation_candidates")
        )

    assert note == "first run, all noise"


async def test_candidates_come_back_ranked_and_bounded(
    database: Database,
) -> None:
    await seed(database)
    await store(
        database,
        [
            Candidate(pair=ordered(A, B), score=0.4),
            Candidate(pair=ordered(A, C), score=1.8),
        ],
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
                    "INSERT INTO yt_affiliation_candidates "
                    "(channel_a, channel_b, score, run_id) "
                    "VALUES (:b, :a, 1.0, :run)"
                ),
                {"a": A, "b": B, "run": run_id},
            )


async def test_a_pair_the_locale_orders_the_other_way_is_stored(
    database: Database,
) -> None:
    """The stored order is Python's code-point order, whatever the
    database's collation makes of it.

    `ordered()` compares code points, so it stores UCB… before UCa… —
    and under a linguistic collation that pair reads as the wrong way
    round. The first production run of `yt affiliates` died on exactly
    this: one bulk insert of hundreds of pairs, one disagreement with
    the en_US.utf8 database, the whole run rolled back. On a database
    whose locale is a linguistic collation — production's, and the
    compose and CI instances match it — this test fails without the
    collation; on a byte-order locale it passes either way, which is
    what the introspection below is for.
    """
    async with database.session() as session:
        await create_channels(session, [DIVERGENT_LOWER, DIVERGENT_UPPER])

    await store(
        database,
        [Candidate(pair=ordered(DIVERGENT_UPPER, DIVERGENT_LOWER), score=1.0)],
    )

    async with database.session() as session:
        rows = await list_candidates(session, seeds_only=False)

    assert [(row.channel_a, row.channel_b) for row in rows] == [
        (DIVERGENT_UPPER, DIVERGENT_LOWER)
    ]


async def test_the_pair_order_comparisons_are_collated_to_bytes(
    database: Database,
) -> None:
    """The constraint and the family key say their collation out loud.

    A behaviour test only bites where the database locale is a
    linguistic collation; this one reads what was built, and bites on
    every locale. The view is named too because a view keeps the
    collation it was created with — recreating it under a different
    default says nothing to the reader that relies on its keys being
    the code-point-smallest id, the order every Python reader sorts
    ids by.
    """
    async with database.session() as session:
        constraint = await session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'yt_affiliation_candidates'::regclass "
                "AND conname = "
                "'ck_yt_affiliation_candidates_pair_is_ordered'"
            )
        )
        view = await session.scalar(
            text("SELECT pg_get_viewdef('yt_channel_families'::regclass)")
        )

    assert constraint is not None and 'COLLATE "C"' in constraint
    assert view is not None and 'COLLATE "C"' in view


async def test_the_inventory_load_builds_the_family_key(
    database: Database,
) -> None:
    """Detection compares two keys, so every channel needs one — the
    unaffiliated ones answering with themselves."""
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B])

    async with database.session() as session:
        inventory = await load_inventory(session)

    assert inventory.family_of[A] == inventory.family_of[B]
    assert inventory.family_of[C] == C
    assert inventory.known_channels == frozenset({A, B, C})
    assert inventory.titles[A] == "Fake Channel 1"
    assert inventory.descriptions[B] == "An invented channel for tests."
    assert inventory.handles["example1"] == frozenset({A})


async def test_the_inventory_load_lowercases_handles(
    database: Database,
) -> None:
    """So a handle parsed out of a description matches a stored one,
    whatever case the customUrl carries."""
    await seed(database)
    async with database.session() as session:
        await store_payloads(
            session,
            {A: channel_resource(1, handle="MixedCase")},
            fetched_at=datetime.now(UTC),
        )

    async with database.session() as session:
        inventory = await load_inventory(session)

    assert inventory.handles["mixedcase"] == frozenset({A})


async def test_a_pair_with_no_seed_in_it_is_not_shown(
    database: Database,
) -> None:
    """Reviewing two channels neither of which is in scope answers a
    question nobody has asked yet."""
    await seed(database, in_scope=False)
    await store(database, [Candidate(pair=ordered(A, B), score=1.4)])

    async with database.session() as session:
        rows = await list_candidates(session)

    assert rows == []


async def test_one_seed_is_enough_to_show_a_pair(database: Database) -> None:
    await seed(database, in_scope=False)
    await store(database, [Candidate(pair=ordered(A, B), score=1.4)])

    async with database.session() as session:
        record = await session.get(YtChannel, A)
        assert record is not None
        record.status = ChannelStatus.SEED
        record.kind = YtChannelKind.PERSONAL
        record.lang = YtLang.EN

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
    await store(database, [Candidate(pair=ordered(A, B), score=1.4)])

    async with database.session() as session:
        rows = await list_candidates(session, seeds_only=False)

    assert [row.channel_b for row in rows] == [B]
