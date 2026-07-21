"""The operator's own subscriptions, read as inventory rows."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.tl.custom import Dialog

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import DiscoverySource

__all__ = ["ImportCounts", "import_dialogs"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportCounts:
    """How a dialog import split between new, known and private dialogs."""

    inserted: int
    updated: int
    skipped: int


def _public_channel(dialog: Dialog) -> DiscoveredChannel | None:
    """The dialog as an inventory row, or ``None`` if it is private.

    Only publicly addressable entities belong in the inventory. A direct
    message is not a channel at all, and a legacy group chat is a
    ``Chat`` rather than a ``Channel`` — small, invite-only by
    construction. A channel or supergroup that has no username cannot be
    resolved by anyone who was not let in, so it is private too.

    A supergroup reports as both a channel and a group, and is stored as
    a chat: that is the column the comments phase reads.
    """
    if not dialog.is_channel:
        return None
    username: str | None = getattr(dialog.entity, "username", None)
    if not username:
        return None
    return DiscoveredChannel(
        tg_id=dialog.entity.id,
        username=username,
        title=getattr(dialog.entity, "title", None),
        is_chat=bool(dialog.is_group),
    )


async def import_dialogs(
    client: TelegramClient, session: AsyncSession
) -> ImportCounts:
    """Import the public part of the dialog list. Safe to re-run.

    Reads only: no dialog is joined, left or otherwise touched.
    """
    channels: list[DiscoveredChannel] = []
    skipped = 0
    async for dialog in client.iter_dialogs():
        channel = _public_channel(dialog)
        if channel is None:
            # Counted, never named: what was skipped is private.
            skipped += 1
            continue
        channels.append(channel)

    logger.info(
        "read %d public channels from dialogs, skipped %d private dialogs",
        len(channels),
        skipped,
    )
    counts = await upsert_channels(
        session,
        channels,
        discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
    )
    return ImportCounts(
        inserted=counts.inserted, updated=counts.updated, skipped=skipped
    )
