"""Telethon client lifecycle: the only place a ``TelegramClient`` is built."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from telethon import TelegramClient
from telethon.utils import maybe_async

from itgraph.config import settings

if TYPE_CHECKING:
    # Under `TYPE_CHECKING` so importing the client does not drag in the
    # database layer; the lease itself is imported inside the function
    # that takes it.
    from itgraph.db.session_lease import SessionLease

__all__ = [
    "NotAuthorizedError",
    "build_client",
    "connected",
    "connected_with_lease",
    "persist_peers",
]

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


async def persist_peers(client: TelegramClient) -> None:
    """Commit what the session has learned. Call before the database commit.

    Telethon writes a learned entity into the session's SQLite without
    committing it — ``process_entities`` inserts and returns, and the
    commit happens in the keepalive loop once a minute and again on
    disconnect. That is a sensible default for a long-lived client and a
    bad one here: an ``access_hash`` is what makes a channel reachable at
    all, and a pass that records a channel as resolved in Postgres while
    the session file never commits the hash leaves a channel the
    inventory believes in and no session can walk. ``backfill`` then
    skips it on every run, for good, because a missing peer is
    deliberately not a permanent failure.

    Hence the ordering, which is the whole point of calling this by hand:
    **session first, database second**. A crash between the two leaves a
    warm session and a row that was never marked resolved — which the
    next run simply redoes. The other order is what produced the six
    channels this exists to stop happening again.
    """
    await maybe_async(client.session.save())


@asynccontextmanager
async def connected_with_lease(
    command: str,
) -> AsyncIterator[tuple[TelegramClient, SessionLease]]:
    """Connect an already-authorized session, exclusively, and say so.

    Deliberately never starts an interactive login: collection runs
    unattended, and an auth prompt in the middle of a backfill means the
    session is wrong. Authorize once via ``itgraph login``.

    The session lease is taken here rather than in each command, and
    ``command`` is required rather than defaulted for the same reason:
    this is the one place every networked command passes through, so a
    new command cannot be written that forgets to claim the session. It
    only has to say which command it is, which it cannot do wrongly by
    omission.

    The lease is released after the client disconnects, not before — the
    thing being protected is the session file, and Telethon writes to it
    on the way out.

    The lease is yielded alongside the client for the one caller that
    needs it: a process that runs for days has to keep asking whether it
    still holds the session, which a command that exits in minutes never
    does. Everything else uses ``connected`` and never sees it.
    """
    from itgraph.db.session_lease import session_lease

    async with session_lease(command) as lease:
        client = build_client()
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise NotAuthorizedError(
                    f"No authorized session at {settings.telegram_session}; "
                    "run `itgraph login` once — see the Telegram "
                    "authorization section of src/itgraph/README.md."
                )
            logger.debug("connected as an authorized user")
            yield client, lease
        finally:
            await client.disconnect()


@asynccontextmanager
async def connected(command: str) -> AsyncIterator[TelegramClient]:
    """Connect an already-authorized session, exclusively.

    What every command but the loop uses. See ``connected_with_lease``.
    """
    async with connected_with_lease(command) as (client, _):
        yield client
