"""Recording that two channels share an author.

The only place `operator_id` is ever written, so the invariants the
column cannot express itself are checked here: depth exactly one, no
accidental merge of two families, and the observed edges left alone.
"""

import pytest
from sqlalchemy import text

from itgraph.db.channels import (
    ChannelNotFoundError,
    DiscoveredChannel,
    confirm_affiliation,
    count_families,
    list_channels,
    recanonicalize_family,
    reject_affiliation,
    upsert_channels,
    withdraw_affiliation,
)
from itgraph.db.models import DiscoverySource
from itgraph.db.session import Database

A = 1001
B = 1002
C = 1003
D = 1004


async def seed(database: Database) -> None:
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
                for tg_id in (A, B, C, D)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )


async def operator_ids(database: Database) -> dict[int, int | None]:
    async with database.session() as session:
        rows = await session.execute(
            text("SELECT tg_id, operator_id FROM channels ORDER BY tg_id")
        )
        return dict(rows.all())


async def test_confirming_writes_the_pointer_one_way(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        link = await confirm_affiliation(session, A, B, canonical=A)

    assert link.canonical == A
    assert link.member == B
    pointers = await operator_ids(database)
    assert pointers[B] == A
    # The canonical channel names nobody — the shape `linked_to` uses.
    assert pointers[A] is None


async def test_a_confirmation_is_recorded_as_the_operator_s(
    database: Database,
) -> None:
    """A pair no signal proposed is still recordable; it just must not
    read afterwards as something detection found."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(
            session, A, B, canonical=A, note="same author"
        )

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT origin::text, decision::text, canonical_id, "
                    "decision_note FROM affiliation_candidates"
                )
            )
        ).one()

    assert row == ("operator", "confirmed", A, "same author")


async def test_the_canonical_channel_must_be_one_of_the_two(
    database: Database,
) -> None:
    await seed(database)

    with pytest.raises(ValueError, match="must be one of the two"):
        async with database.session() as session:
            await confirm_affiliation(session, A, B, canonical=C)


async def test_a_channel_cannot_be_affiliated_with_itself(
    database: Database,
) -> None:
    await seed(database)

    with pytest.raises(ValueError, match="itself"):
        async with database.session() as session:
            await confirm_affiliation(session, A, A, canonical=A)


async def test_an_unknown_channel_is_refused(database: Database) -> None:
    await seed(database)

    with pytest.raises(ChannelNotFoundError):
        async with database.session() as session:
            await confirm_affiliation(session, A, 999999, canonical=A)


async def test_a_chain_is_refused(database: Database) -> None:
    """Depth one, or `COALESCE(operator_id, tg_id)` needs transitive
    closure and every consumer of the family key gets a wrong answer."""
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)

    with pytest.raises(ValueError, match="itself in family"):
        async with database.session() as session:
            await confirm_affiliation(session, B, C, canonical=B)

    assert (await operator_ids(database))[C] is None


async def test_a_third_channel_joins_the_existing_family(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)
        await confirm_affiliation(session, A, C, canonical=A)

    pointers = await operator_ids(database)
    assert pointers[B] == A
    assert pointers[C] == A


async def test_merging_two_families_is_refused(database: Database) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)
        await confirm_affiliation(session, C, D, canonical=C)

    with pytest.raises(ValueError, match="merging two families"):
        async with database.session() as session:
            await confirm_affiliation(session, B, D, canonical=B)


async def test_rejecting_writes_no_pointer(database: Database) -> None:
    await seed(database)

    async with database.session() as session:
        await reject_affiliation(session, A, B, note="different people")

    assert await operator_ids(database) == {A: None, B: None, C: None, D: None}
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, canonical_id, decision_note "
                    "FROM affiliation_candidates"
                )
            )
        ).one()
    assert row == ("rejected", None, "different people")


async def test_withdrawal_clears_the_pointer_and_reopens_the_pair(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    assert (await operator_ids(database))[B] is None
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, canonical_id, decided_at "
                    "FROM affiliation_candidates"
                )
            )
        ).one()
    assert row == ("pending", None, None)


async def test_recanonicalizing_moves_every_member(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)
        await confirm_affiliation(session, A, C, canonical=A)

    async with database.session() as session:
        counts = await recanonicalize_family(session, B)

    assert counts.channels == 3
    pointers = await operator_ids(database)
    assert pointers[B] is None
    assert pointers[A] == B
    assert pointers[C] == B
    # Nobody is left naming the former canonical channel.
    assert A not in [value for value in pointers.values() if value is not None]


async def test_promoting_an_already_canonical_channel_is_refused(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)

    with pytest.raises(ValueError, match="already the canonical"):
        async with database.session() as session:
            await recanonicalize_family(session, A)


async def test_confirming_a_family_leaves_every_edge_in_place(
    database: Database,
) -> None:
    """The repost happened. Excluding it is analysis, not deletion."""
    await seed(database)
    async with database.session() as session:
        for index in range(5):
            await session.execute(
                text(
                    "INSERT INTO edges (src_channel_id, dst_channel_id, "
                    "kind, msg_id, published_at) "
                    "VALUES (:a, :b, 'forward', :msg, now())"
                ),
                {"a": A, "b": B, "msg": index},
            )

    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)

    async with database.session() as session:
        remaining = await session.scalar(text("SELECT count(*) FROM edges"))
        # And the family key is what lets analysis tell the edge apart.
        intra = await session.scalar(
            text(
                "SELECT count(*) FROM edges e "
                "JOIN channels s ON s.tg_id = e.src_channel_id "
                "JOIN channels d ON d.tg_id = e.dst_channel_id "
                "WHERE COALESCE(s.operator_id, s.tg_id) "
                "= COALESCE(d.operator_id, d.tg_id)"
            )
        )

    assert remaining == 5
    assert intra == 5


async def test_the_family_listing_includes_the_canonical_channel(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)

    async with database.session() as session:
        # Asked by either member, the answer is the whole family.
        from_member = await list_channels(session, family=A)
        rows = [channel.tg_id for channel in from_member]

    assert rows == [A, B]


async def test_a_channel_in_no_family_is_its_own_family_of_one(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        rows = await list_channels(session, family=C)

    assert [channel.tg_id for channel in rows] == [C]


async def test_families_are_counted_not_memberships(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)
        await confirm_affiliation(session, A, C, canonical=A)

    async with database.session() as session:
        counts = await count_families(session)

    assert counts.families == 1
    assert counts.channels == 3


async def test_confirming_an_existing_family_names_the_promote_command(
    database: Database,
) -> None:
    """The error that sent a real operator to ask what was wrong.

    Confirming the pair again with the other side as canonical is the
    natural thing to type, and "re-canonicalize first" is a remedy
    nobody can act on without the syntax.
    """
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)

    with pytest.raises(ValueError, match="already one family") as caught:
        async with database.session() as session:
            await confirm_affiliation(session, A, B, canonical=B)

    message = str(caught.value)
    assert "itgraph family" in message
    # Pasteable, not retyped off a listing.
    assert "@example_1002 --canonical @example_1002" in message


async def test_the_two_family_error_still_names_both(
    database: Database,
) -> None:
    """The other branch keeps its own message — merging is a different
    problem from re-canonicalizing."""
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, A, B, canonical=A)
        await confirm_affiliation(session, C, D, canonical=C)

    with pytest.raises(ValueError, match="merging two families"):
        async with database.session() as session:
            await confirm_affiliation(session, B, D, canonical=B)
