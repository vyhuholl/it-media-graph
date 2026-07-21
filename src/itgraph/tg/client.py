"""Telethon client lifecycle: the only place a ``TelegramClient`` is built."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient

from itgraph.config import settings

__all__ = ["NotAuthorizedError", "build_client", "connected"]

logger = logging.getLogger(__name__)


class NotAuthorizedError(RuntimeError):
    """The session holds no authorized user."""


def build_client() -> TelegramClient:
    """Construct the client. Does not connect and does not authorize."""
    return TelegramClient(
        str(settings.telegram_session),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
        device_model=settings.device_model,
        system_version=settings.system_version,
        app_version=settings.app_version,
    )


@asynccontextmanager
async def connected() -> AsyncIterator[TelegramClient]:
    """Connect an already-authorized session.

    Deliberately never starts an interactive login: collection runs
    unattended, and an auth prompt in the middle of a backfill means the
    session is wrong. Authorize once via ``itgraph login``.
    """
    client = build_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise NotAuthorizedError(
                "No authorized session at "
                f"{settings.telegram_session}; run `itgraph login` first."
            )
        logger.debug("connected as an authorized user")
        yield client
    finally:
        await client.disconnect()
