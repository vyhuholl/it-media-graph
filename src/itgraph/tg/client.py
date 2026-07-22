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
        # Waits under this Telethon sleeps through itself; longer ones
        # reach the collector as `FloodWaitError`, which it also waits
        # out. Both paths wait — there is no third path, because the
        # alternative to waiting is the behaviour that gets accounts
        # banned.
        flood_sleep_threshold=settings.flood_sleep_threshold,
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
                f"No authorized session at {settings.telegram_session}; "
                "run `itgraph login` once — see the Telegram authorization "
                "section of README.md."
            )
        logger.debug("connected as an authorized user")
        yield client
    finally:
        await client.disconnect()
