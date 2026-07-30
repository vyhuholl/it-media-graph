import pytest
from sqlalchemy import text

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import DiscoverySource
from itgraph.db.session import Database

KNOWN = 1000000001


async def _channels(database: Database, count: int) -> None:
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=KNOWN + offset,
                    username=f"example_{offset}",
                    title=f"Example {offset}",
                    is_chat=False,
                )
                for offset in range(count)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )


async def _confirm(database: Database, pairs: list[tuple[int, int]]) -> None:
    async with database.session() as session:
        for first, second in pairs:
            await session.execute(
                text(
                    "INSERT INTO affiliation_candidates "
                    "(channel_a, channel_b, score, decision, decided_at) "
                    "VALUES (:a, :b, 1.0, 'confirmed', now())"
                ),
                {"a": min(first, second), "b": max(first, second)},
            )


async def _families(database: Database) -> dict[int, int]:
    """The family of every channel, the way the analysis asks for it."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT c.tg_id, COALESCE(f.family_key, c.tg_id) "
                "FROM channels c "
                "LEFT JOIN channel_families f ON f.channel_id = c.tg_id "
                "ORDER BY 1"
            )
        )
        return dict(rows.all())


async def test_a_cycle_of_three_is_one_family(database: Database) -> None:
    """The shape that broke the canonical model: A-B, B-C and A-C
    together. The recursion has to terminate on it, which is why the
    view unions rather than unions all."""
    await _channels(database, 3)
    await _confirm(
        database,
        [(KNOWN, KNOWN + 1), (KNOWN + 1, KNOWN + 2), (KNOWN, KNOWN + 2)],
    )

    assert await _families(database) == {
        KNOWN: KNOWN,
        KNOWN + 1: KNOWN,
        KNOWN + 2: KNOWN,
    }


async def test_a_chain_of_four_is_one_family(database: Database) -> None:
    """No pair joins the ends, and they are still one family."""
    await _channels(database, 4)
    await _confirm(
        database,
        [
            (KNOWN, KNOWN + 1),
            (KNOWN + 1, KNOWN + 2),
            (KNOWN + 2, KNOWN + 3),
        ],
    )

    assert set((await _families(database)).values()) == {KNOWN}


async def test_every_member_answers_with_the_same_key(
    database: Database,
) -> None:
    await _channels(database, 3)
    await _confirm(database, [(KNOWN, KNOWN + 1), (KNOWN + 1, KNOWN + 2)])

    async with database.session() as session:
        keys = await session.execute(
            text("SELECT DISTINCT family_key FROM channel_families")
        )

    assert keys.scalars().all() == [KNOWN]


async def test_two_families_stay_apart(database: Database) -> None:
    await _channels(database, 4)
    await _confirm(database, [(KNOWN, KNOWN + 1), (KNOWN + 2, KNOWN + 3)])

    assert await _families(database) == {
        KNOWN: KNOWN,
        KNOWN + 1: KNOWN,
        KNOWN + 2: KNOWN + 2,
        KNOWN + 3: KNOWN + 2,
    }


async def test_a_channel_with_no_confirmed_pair_is_its_own_family(
    database: Database,
) -> None:
    await _channels(database, 2)

    async with database.session() as session:
        rows = await session.execute(text("SELECT * FROM channel_families"))
        assert rows.all() == []

    assert await _families(database) == {KNOWN: KNOWN, KNOWN + 1: KNOWN + 1}


async def test_a_pending_pair_makes_no_family(database: Database) -> None:
    """Only a confirmed pair says two channels share an author."""
    await _channels(database, 2)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO affiliation_candidates "
                "(channel_a, channel_b, score) VALUES (:a, :b, 1.0)"
            ),
            {"a": KNOWN, "b": KNOWN + 1},
        )

    assert await _families(database) == {KNOWN: KNOWN, KNOWN + 1: KNOWN + 1}


async def test_session_runs_a_query(database: Database) -> None:
    async with database.session() as session:
        result = await session.execute(text("select 1"))
        assert result.scalar_one() == 1


async def test_session_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            await session.execute(text("create table t (id int)"))
            raise RuntimeError("boom")

    async with database.session() as session:
        exists = await session.execute(text("select to_regclass('t')"))
        assert exists.scalar_one() is None
