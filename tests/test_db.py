import pytest
from sqlalchemy import text

from itgraph.db.session import Database


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
