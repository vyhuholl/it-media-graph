"""Recording that channels share an author.

A family is a set with no main channel, and it is the connected
components of the confirmed pairs rather than anything stored. So the
cases worth pinning are the ones that shape made possible and the old
star model did not: a group assembled from pairs that form no star,
confirmation order not mattering, a bridging pair merging two families,
and a withdrawal splitting one only when nothing else holds it together.
"""

import pytest
from sqlalchemy import text

from itgraph.db.affiliation import family_keys, family_of
from itgraph.db.channels import (
    ChannelNotFoundError,
    DiscoveredChannel,
    confirm_affiliation,
    count_families,
    list_channels,
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
E = 1005


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
                for tg_id in (A, B, C, D, E)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )


async def families(database: Database) -> dict[int, int]:
    """The family of every seeded channel, solo channels included."""
    async with database.session() as session:
        keys = await family_keys(session)
    return {tg_id: family_of(keys, tg_id) for tg_id in (A, B, C, D, E)}


async def confirmed_pairs(database: Database) -> set[tuple[int, int]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT channel_a, channel_b FROM affiliation_candidates "
                "WHERE decision = 'confirmed' ORDER BY 1, 2"
            )
        )
        return {(a, b) for a, b in rows.all()}


# --- the case this change exists for ----------------------------------


async def test_a_group_whose_pairs_form_no_star_is_one_family(
    database: Database,
) -> None:
    """The vacancies case: detection finds A-B, A-C, D-B, and no channel
    is the hub. The old model refused the second confirmation."""
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
    """The other refusal the old model had, with no command to perform
    what it refused."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [C, D])
        assert (await count_families(session)).families == 2

    async with database.session() as session:
        group = await confirm_affiliation(session, [B, C])

    assert group.channels == 4
    assert await families(database) == {A: A, B: A, C: A, D: A, E: E}


async def test_a_pair_inside_one_family_succeeds_and_changes_nothing(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [B, C])

    before = await families(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, C])

    assert await families(database) == before


# --- confirming a whole group at once ---------------------------------


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


async def test_a_group_confirmed_at_once_survives_one_withdrawal(
    database: Database,
) -> None:
    """Which is why every pair is stored rather than a chain."""
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B, C, D])

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    assert await families(database) == {A: A, B: A, C: A, D: A, E: E}
    assert (A, B) not in await confirmed_pairs(database)


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


async def test_an_unknown_channel_writes_nothing(database: Database) -> None:
    """Including no pair among the channels that were valid."""
    await seed(database)

    with pytest.raises(ChannelNotFoundError):
        async with database.session() as session:
            await confirm_affiliation(session, [A, B, 999999])

    assert await confirmed_pairs(database) == set()


# --- withdrawal, and the split that falls out of the derivation -------


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
        await confirm_affiliation(session, [A, B])
        await confirm_affiliation(session, [B, C])

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    assert await confirmed_pairs(database) == {(B, C)}


async def test_a_withdrawn_pair_returns_to_pending(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B], note="same author")

    async with database.session() as session:
        await withdraw_affiliation(session, A, B)

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, decided_at, decision_note "
                    "FROM affiliation_candidates"
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
        await reject_affiliation(session, A, B, note="different people")

    assert await families(database) == {A: A, B: B, C: C, D: D, E: E}
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT decision::text, decision_note "
                    "FROM affiliation_candidates"
                )
            )
        ).one()
    assert row == ("rejected", "different people")


async def test_a_rejected_pair_does_not_veto_a_family(
    database: Database,
) -> None:
    """A rejection says "this pair is not evidence", not "these two are
    not family". If other confirmations connect them anyway, they are
    one family and the rejection keeps stopping the pair being proposed.
    """
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


# --- what the inventory reports ---------------------------------------


async def test_a_confirmation_is_recorded_as_the_operator_s(
    database: Database,
) -> None:
    """A pair no signal proposed is still recordable; it just must not
    read afterwards as something detection found."""
    await seed(database)

    async with database.session() as session:
        await confirm_affiliation(session, [A, B], note="same author")

    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT origin::text, decision::text, decision_note "
                    "FROM affiliation_candidates"
                )
            )
        ).one()

    assert row == ("operator", "confirmed", "same author")


async def test_the_family_listing_answers_from_any_member(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B, C])

    async with database.session() as session:
        keys = await family_keys(session)
        for member in (A, B, C):
            listed = await list_channels(
                session, family=family_of(keys, member)
            )
            assert [channel.tg_id for channel in listed] == [A, B, C]


async def test_a_channel_in_no_family_is_its_own_family_of_one(
    database: Database,
) -> None:
    await seed(database)

    async with database.session() as session:
        listed = await list_channels(session, family=E)

    assert [channel.tg_id for channel in listed] == [E]


async def test_families_are_counted_not_memberships(
    database: Database,
) -> None:
    await seed(database)
    async with database.session() as session:
        await confirm_affiliation(session, [A, B, C])

    async with database.session() as session:
        counts = await count_families(session)

    assert counts.families == 1
    assert counts.channels == 3


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
        await confirm_affiliation(session, [A, B])

    async with database.session() as session:
        remaining = await session.scalar(text("SELECT count(*) FROM edges"))
        # The family key is what lets analysis tell the edge apart.
        intra = await session.scalar(
            text(
                "SELECT count(*) FROM edges e "
                "JOIN channels s ON s.tg_id = e.src_channel_id "
                "JOIN channels d ON d.tg_id = e.dst_channel_id "
                "LEFT JOIN channel_families fs ON fs.channel_id = s.tg_id "
                "LEFT JOIN channel_families fd ON fd.channel_id = d.tg_id "
                "WHERE COALESCE(fs.family_key, s.tg_id) "
                "= COALESCE(fd.family_key, d.tg_id)"
            )
        )

    assert remaining == 5
    assert intra == 5
