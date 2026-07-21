"""Database access. Engines and sessions are built here, nowhere else."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from itgraph.config import settings

__all__ = ["Database"]


class Database:
    """Owns one engine and its session factory.

    The caller (a CLI command, a worker, a test fixture) creates one and
    disposes it when done — there is no module-level engine to leak
    connections across event loops.
    """

    def __init__(self, url: str | None = None, *, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url or str(settings.database_url), echo=echo
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Session scope: commit on success, roll back on exception."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()
