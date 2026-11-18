"""Recording that YouTube channels share an owner.

``test_affiliation_family.py``'s cases, carried over to the YouTube
tables: a family is a set with no main channel, the connected components
of the confirmed pairs rather than anything stored, and a bridging
confirmation merges two families exactly as a withdrawal can split one.
What is YouTube's own here is the token rejection — one statement
declining every pending pair a corroborated token proposed — and the
rule that its rejections stay pair-scoped, so a channel that later
starts carrying the token is a new question.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from ytfakes import channel_id, channel_resource

from itgraph.db.models import YtChannel
from itgraph.db.session import Database
from itgraph.db.yt_affiliation import family_keys, family_of, list_candidates
from itgraph.db.yt_channels import (
    AmbiguousHandleError,
    YtChannelNotFoundError,
    confirm_affiliation,
    create_channels,
    reject_affiliation,
    reject_token,
    store_payloads,
    withdraw_affiliation,
)
from itgraph.yt.affiliation.run import run_detection
from itgraph.yt.affiliation.signals import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS

A = channel_id(1)
B = channel_id(2)
C = channel_id(3)
D = channel_id(4)
E = channel_id(5)


async def seed(database: Database) -> None:
    """Five channels whose titles share no token, so a test that overwrites
    some of them starts from a quiet candidate list."""
    titles = (
        "Alpha One",
        "Bravo Two",
        "Charlie Three",
        "Delta Four",
        "Echo Five",
    )
    async with database.session() as session:
        await create_channels(session, [A, B, C, D, E])
        await store_payloads(
            session,
            {
                channel: channel_resource(
                    number, handle=f"example{number}", title=title
                )
                for number, (channel, title) in enumerate(
                    zip((A, B, C, D, E), titles, strict=True), 1
                )
            },
            fetched_at=datetime.now(UTC),
        )


async def families(database: Database) -> dict[str, str]:
    """The family of every seeded channel, solo channels included."""
    async with database.session() as session:
        keys = await family_keys(session)
    return {channel: family_of(keys, channel) for channel in (A, B, C, D, E)}


async def confirmed_pairs(database: Database) -> set[tuple[str, str]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT channel_a, channel_b FROM yt_affiliation_candidates "
                "WHERE decision = 'confirmed' ORDER BY 1, 2"
            )
        )
        return {(a, b) for a, b in rows.all()}


async def decided_rows(database: Database) -> list[tuple[str, str, str]]:
    """(pair, decision, note) for every row carrying a decision."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT channel_a, channel_b, decision::text, decision_note "
                "FROM yt_affiliation_candidates "
                "WHERE decision <> 'pending' ORDER BY 1, 2"
            )
        )
        return [(a, b, decision) for a, b, decision, _ in rows.all()]


# --- the group, and the shape of a family ------------------------------


async def test_a_group_is_confirmable_in_one_statement(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        group = await confirm_affiliation(session, [A, B, C, D])

    # Every pair, not a chain: four channels are six pairs.
    assert group.pairs == 6
    assert group.channels == 4
    assert await confirmed_pairs(database) == {
        (A, B),
        (A, C),
        (A, D),
        (B, C),
        (B, D),
        (C, D),
    }


async def test_a_group_whose_pairs_form_no_star_is_one_family(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [A, C])
        await confirm_affiliation(session, [D, B])

    assert await families(database) == {A: A, B: A, C: A, D: A, E: E}


async def test_confirmation_order_does_not_change_the_family(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [D, B])
        await confirm_affiliation(session, [A, C])
        await confirm_affiliation(session, [A, B])

    assert await families(database) == {A: A, B: A, C: A, D: A, E: E}


async def test_a_bridging_pair_merges_two_families(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [C, D])

    assert len(set((await families(database)).values())) == 3

    async with database.session() as session:
        group = await confirm_affiliation(session, [B, C])

    # The named group is a pair; the family it made is four channels.
    assert group.channels == 4
    assert await families(database) == {A: A, B: A, C: A, D: A, E: E}


async def test_a_channel_in_no_family_is_its_own_family_of_one(
    database: Database,
) -> None:
    await seed(database)

    result = await families(database)

    assert result == {A: A, B: B, C: C, D: D, E: E}


async def test_a_repeated_channel_is_refused(database: Database) -> None:
    await seed(database)

    with pytest.raises(ValueError, match="more than once"):
        async with database.session() as session:
            await confirm_affiliation(session, [A, B, A])


async def test_fewer_than_two_channels_is_refused(
    database: Database,
) -> None:
    await seed(database)

    with pytest.raises(ValueError, match="at least two"):
        async with database.session() as session:
            await confirm_affiliation(session, [A])


async def test_channels_are_found_by_handle_and_id(
    database: Database,
) -> None:
    """The confirmation line the report prints names handles, so a
    handle has to confirm the same pair the id does."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, ["@example2", A])

    assert await confirmed_pairs(database) == {(A, B)}


async def test_an_ambiguous_handle_fails_naming_both(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        # Two stored payloads carrying one handle: one is stale, and
        # neither is a safe reading of the name.
        await store_payloads(
            session,
            {C: channel_resource(3, handle="Example2")},
            fetched_at=datetime.now(UTC),
        )

    with pytest.raises(AmbiguousHandleError, match=f"{B}.*{C}|{C}.*{B}"):
        async with database.session() as session:
            await confirm_affiliation(session, ["@example2", A])

    assert await confirmed_pairs(database) == set()


async def test_an_unknown_channel_writes_nothing(database: Database) -> None:
    """Including no pair among the channels that were valid."""
    await seed(database)

    with pytest.raises(YtChannelNotFoundError):
        async with database.session() as session:
            await confirm_affiliation(session, [A, B, channel_id(99)])

    assert await confirmed_pairs(database) == set()


# --- withdrawal, and the split that falls out of the derivation --------


async def test_withdrawing_the_only_connection_splits_the_family(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [B, C])

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    result = await families(database)
    assert result[A] == A
    assert result[B] == result[C] == B


async def test_withdrawing_a_pair_leaves_the_others_confirmed(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B, C, D])

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    # Six pairs minus one: the group was stored whole, so one withdrawal
    # cannot take the others with it.
    assert await confirmed_pairs(database) == {
        (A, C),
        (A, D),
        (B, C),
        (B, D),
        (C, D),
    }


async def test_a_withdrawn_pair_returns_to_review(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B], note="same owner")

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, decided_at, decision_note "
                    "FROM yt_affiliation_candidates"
                )
            )
        ).one()

    assert row == ("pending", None, None)


# --- rejection --------------------------------------------------------


async def test_rejecting_records_the_pair_and_no_family(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        await reject_affiliation(session, A, B, note="different owners")

    assert await families(database) == {A: A, B: B, C: C, D: D, E: E}
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, decision_note, decided_at "
                    "FROM yt_affiliation_candidates"
                )
            )
        ).one()
    assert row[0] == "rejected"
    assert row[1] == "different owners"
    assert row[2] is not None


async def test_a_rejected_pair_does_not_veto_a_family(
    database: Database,
) -> None:
    """A rejection says "this pair is not evidence", not "these two are
    not family". If other confirmations connect them anyway, they are
    one family and the rejection keeps stopping the pair being proposed."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [B, C])
        await reject_affiliation(session, A, C)

    result = await families(database)
    assert result[A] == result[B] == result[C]


async def test_rejecting_a_channel_with_itself_is_refused(
    database: Database,
) -> None:
    await seed(database)

    with pytest.raises(ValueError, match="itself"):
        async with database.session() as session:
            await reject_affiliation(session, A, A)


# --- token rejection ---------------------------------------------------


async def carriers(
    database: Database,
    titled: dict[str, str],
    *,
    linking: tuple[str, str] | None = None,
) -> None:
    """Overwrite payloads: channels whose titles carry one token, one
    description naming another carrier — what a corroborated group is
    made of."""
    async with database.session() as session:
        for channel, title in titled.items():
            number = int(channel.removeprefix("UCfake_channel_"))
            payload = channel_resource(
                number, handle=f"example{number}", title=title
            )
            if linking is not None and channel == linking[0]:
                payload["snippet"]["description"] = (
                    f"We also run youtube.com/channel/{linking[1]}"
                )
            await store_payloads(
                session, {channel: payload}, fetched_at=datetime.now(UTC)
            )


async def test_rejecting_a_token_rejects_its_pending_pairs_only(
    database: Database,
) -> None:
    await seed(database)
    await carriers(
        database,
        {A: "Acme Show", B: "Acme Blog", C: "Acme Extra"},
        linking=(A, B),
    )

    await run_detection(
        database, thresholds=DEFAULT_THRESHOLDS, weights=DEFAULT_WEIGHTS
    )
    async with database.session() as session:
        # One pair of the block decided by hand, as review would leave it.
        await confirm_affiliation(session, [B, C])

    async with database.session() as session:
        rejected = await reject_token(session, "acme", note="a subject")

    # Three carriers are three pairs; the confirmed one stays confirmed.
    assert rejected == 2
    rows: list[tuple[str, str, str]] = []
    async with database.session() as session:
        result = await session.execute(
            text(
                "SELECT channel_a, channel_b, decision::text, decision_note "
                "FROM yt_affiliation_candidates ORDER BY 1, 2"
            )
        )
        for a, b, decision, note in result.all():
            rows.append((a, b, decision))
            if decision == "rejected":
                assert note == "a subject"
    assert rows == [
        (A, B, "rejected"),
        (A, C, "rejected"),
        (B, C, "confirmed"),
    ]


async def test_a_token_rejection_without_a_note_names_the_token(
    database: Database,
) -> None:
    await seed(database)
    await carriers(database, {A: "Acme Show", B: "Acme Blog"}, linking=(A, B))
    await run_detection(
        database, thresholds=DEFAULT_THRESHOLDS, weights=DEFAULT_WEIGHTS
    )

    async with database.session() as session:
        await reject_token(session, "acme")

    async with database.session() as session:
        note = (
            await session.execute(
                text(
                    "SELECT decision_note FROM yt_affiliation_candidates "
                    "WHERE decision = 'rejected'"
                )
            )
        ).scalar_one()
    assert note == "token 'acme'"


async def test_a_rejected_token_does_not_veto_new_carriers(
    database: Database,
) -> None:
    """The rejection is pair-scoped: a channel that later starts
    carrying the token asks its questions anew."""
    await seed(database)
    await carriers(database, {A: "Acme Show", B: "Acme Blog"}, linking=(A, B))
    await run_detection(
        database, thresholds=DEFAULT_THRESHOLDS, weights=DEFAULT_WEIGHTS
    )

    async with database.session() as session:
        rejected = await reject_token(session, "acme")

    assert rejected == 1

    # A third channel picks the token up.
    await carriers(database, {C: "Acme Extra"})
    await run_detection(
        database, thresholds=DEFAULT_THRESHOLDS, weights=DEFAULT_WEIGHTS
    )

    async with database.session() as session:
        rows = await list_candidates(session, seeds_only=False)

    # The old pair stays rejected and out of the list; the new carrier's
    # pairs are new questions, pending.
    assert {(row.channel_a, row.channel_b) for row in rows} == {
        (A, C),
        (B, C),
    }


# --- what the review is recorded against --------------------------------


async def test_a_confirmation_without_a_prior_candidate_is_the_operator_s(
    database: Database,
) -> None:
    """A pair no signal proposed is still recordable; it just must not
    read afterwards as something detection found."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B], note="same owner")

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT origin::text, decision::text, decision_note "
                    "FROM yt_affiliation_candidates"
                )
            )
        ).one()

    assert row == ("operator", "confirmed", "same owner")


async def test_a_confirmation_of_a_proposed_pair_leaves_the_signal_origin(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await store_payloads(
            session,
            {
                A: channel_resource(1, handle="example1", title="Acme Show"),
                B: channel_resource(2, handle="example2", title="Acme Blog"),
            },
            fetched_at=datetime.now(UTC),
        )
    await run_detection(
        database, thresholds=DEFAULT_THRESHOLDS, weights=DEFAULT_WEIGHTS
    )

    async with database.session() as session:
        await confirm_affiliation(session, [A, B])

    async with database.session() as session:
        origin = (
            await session.execute(
                text("SELECT origin::text FROM yt_affiliation_candidates")
            )
        ).scalar_one()
    assert origin == "signal"


# --- what a family must not touch --------------------------------------


async def test_confirming_a_family_leaves_the_payloads_alone(
    database: Database,
) -> None:
    """What YouTube said stays said: excluding family-internal
    references is analysis, not deletion, and the references are read
    from the payloads exactly as before."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B])

    async with database.session() as session:
        records = (
            (
                await session.execute(
                    select(YtChannel.channel_id).order_by(YtChannel.channel_id)
                )
            )
            .scalars()
            .all()
        )
        payloads = await session.execute(
            text(
                "SELECT channel_id FROM yt_raw_channels "
                "WHERE payload -> 'snippet' ->> 'description' "
                "IS NOT NULL ORDER BY 1"
            )
        )
        assert len(records) == 5
        assert len(payloads.all()) == 5


async def test_the_family_is_queryable_for_exclusion(
    database: Database,
) -> None:
    """A reference's endpoints resolve to their families, and one whose
    endpoints share a family is distinguishable from one whose do not."""
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B])

    async with database.session() as session:
        keys = await family_keys(session)

    def family(channel: str) -> str:
        return family_of(keys, channel)

    assert family(A) == family(B)
    assert family(A) != family(C)
