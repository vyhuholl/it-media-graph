import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import DiscoverySource
from itgraph.db.session import Database

KNOWN = 1000000001
UNKNOWN = 999999999


async def _two_channels(database: Database) -> None:
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
                for offset in range(2)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )


async def test_a_channel_cannot_be_its_own_operator(
    database: Database,
) -> None:
    """The cheap half of the family invariant, and the only half a
    CHECK can express: naming yourself is refused by the database."""
    await _two_channels(database)

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await session.execute(
                text(
                    "UPDATE channels SET operator_id = :id WHERE tg_id = :id"
                ),
                {"id": KNOWN},
            )


async def test_an_operator_outside_the_inventory_is_refused(
    database: Database,
) -> None:
    await _two_channels(database)

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await session.execute(
                text(
                    "UPDATE channels SET operator_id = :absent "
                    "WHERE tg_id = :id"
                ),
                {"absent": UNKNOWN, "id": KNOWN},
            )


async def test_naming_another_channel_as_operator_is_accepted(
    database: Database,
) -> None:
    await _two_channels(database)

    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE channels SET operator_id = :canonical WHERE tg_id = :id"
            ),
            {"canonical": KNOWN, "id": KNOWN + 1},
        )

    async with database.session() as session:
        family = await session.execute(
            text(
                "SELECT COALESCE(operator_id, tg_id) FROM channels ORDER BY 1"
            )
        )
        # Both channels answer with the same family key — the canonical
        # one through its own id, the member through its pointer.
        assert family.scalars().all() == [KNOWN, KNOWN]


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
