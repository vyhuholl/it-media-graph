"""The operator's own subscriptions, read as inventory rows."""

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient

from itgraph.db.channels import (
    DiscoveredChannel,
    UpsertCounts,
    upsert_channels,
)
from itgraph.db.models import DiscoverySource

__all__ = ["import_dialogs", "iter_dialog_channels"]

logger = logging.getLogger(__name__)


async def iter_dialog_channels(
    client: TelegramClient,
) -> AsyncIterator[DiscoveredChannel]:
    """Yield every channel and group in the account's dialog list.

    Private conversations are skipped — a person is not a channel. A
    supergroup reports as both a channel and a group, and is stored as a
    chat: that is the column the comments phase reads.
    """
    async for dialog in client.iter_dialogs():
        if dialog.is_user:
            continue
        entity = dialog.entity
        yield DiscoveredChannel(
            tg_id=entity.id,
            username=getattr(entity, "username", None),
            title=getattr(entity, "title", None),
            is_chat=bool(dialog.is_group),
        )


async def import_dialogs(
    client: TelegramClient, session: AsyncSession
) -> UpsertCounts:
    """Import the dialog list into the inventory. Safe to re-run.

    Reads only: no dialog is joined, left or otherwise touched.
    """
    channels = [channel async for channel in iter_dialog_channels(client)]
    logger.info("read %d channels and chats from dialogs", len(channels))
    return await upsert_channels(
        session,
        channels,
        discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
    )
