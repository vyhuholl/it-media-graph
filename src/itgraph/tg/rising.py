"""The poll loop's turn at resolution: the head of the rising ranking.

When no poll is due and no walk has a page, the loop asks here for one
resolution. It asks Telegram nothing when any of these holds:

- fewer than ``rising_resolve_gap_minutes`` since the journal's newest
  ``watch`` row — read from the journal, so a restart does not reset it;
- ``resolution_cap`` resolutions or more in the last 24 hours, by any
  command, the operator's included;
- a recorded wait on ``ResolveUsernameRequest`` or ``GetChannelsRequest``
  that has not ended, whichever command met it;
- nothing rising awaits an identity.

Otherwise it resolves the first candidate awaiting one with the
resolution pass's own steps — a channel by id, a pending name by name —
paced as that pass is, journaled under ``watch``. A wait Telegram names
is recorded and ends the turn at once, never slept off: polls and walks
go on, and the record holds resolution until the wait is over. A failure
is recorded where the resolution pass records it, and is not tried
again.

A pending name is never logged here: the loop's log is kept, and the name
may be a person's.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from telethon import TelegramClient

from itgraph.config import settings
from itgraph.db.floods import held_until
from itgraph.db.models import CollectionCommand
from itgraph.db.rising import (
    load_rising_rows,
    newest_resolution,
    resolutions_since,
)
from itgraph.db.session import Database
from itgraph.derive.rising import rank_rising
from itgraph.tg.backfill import FloodWaitTooLong
from itgraph.tg.floods import FloodRecorder
from itgraph.tg.pacing import pace
from itgraph.tg.resolve import (
    ResolveSummary,
    resolve_channel,
    resolve_pending,
)

__all__ = ["RESOLUTION_METHODS", "ResolutionTurn", "resolve_rising_once"]

logger = logging.getLogger(__name__)

# The methods a resolution spends, as the flood record names them: a
# username's lookup, and a channel's by id.
RESOLUTION_METHODS = ("ResolveUsernameRequest", "GetChannelsRequest")


@dataclass(slots=True)
class ResolutionTurn:
    """What one turn at resolution amounted to.

    ``asked`` is true when a request was made. ``timed_out`` when it never
    answered — the loop then drops its connection, as after a poll.
    ``halt`` carries a recorded wait, which holds resolution alone.
    ``nothing_awaits`` when the ranking was read and had no head: the
    loop need not read it again for a while.
    """

    asked: bool = False
    nothing_awaits: bool = False
    timed_out: bool = False
    halt: FloodWaitTooLong | None = None


async def resolve_rising_once(
    client: TelegramClient,
    database: Database,
    *,
    recorder: FloodRecorder,
    now: datetime,
) -> ResolutionTurn:
    """Resolve the head of the ranking, if the gap, the cap and every
    recorded wait allow it and anything awaits."""
    if not await _may_resolve(database, now):
        return ResolutionTurn()

    async with database.session() as session:
        rows = await load_rising_rows(session)
    ranking = rank_rising(
        rows,
        now,
        window_days=settings.rising_window_days,
        min_families=settings.rising_min_families,
    )
    if not ranking.awaiting:
        return ResolutionTurn(nothing_awaits=True)
    head = ranking.awaiting[0]

    await pace(settings.backfill_request_delay)
    summary = ResolveSummary()
    async with database.session() as session:
        try:
            if head.tg_id is not None:
                logger.info(
                    "resolving rising channel %d (%d families)",
                    head.tg_id,
                    head.families,
                )
                await resolve_channel(
                    client,
                    session,
                    head.tg_id,
                    summary,
                    recorder,
                    halt_after=0,
                )
            elif head.username is not None:
                logger.info(
                    "resolving a rising name (%d families)", head.families
                )
                await resolve_pending(
                    client,
                    session,
                    head.username,
                    summary,
                    recorder,
                    halt_after=0,
                    log_name=False,
                )
        except FloodWaitTooLong as exc:
            logger.warning(
                "resolution held until %s UTC: Telegram asked for %.0fs",
                f"{exc.resume_after:%Y-%m-%d %H:%M}",
                exc.seconds,
            )
            return ResolutionTurn(asked=True, halt=exc)
    logger.info("rising resolution: %s", summary.line())
    return ResolutionTurn(asked=True, timed_out=bool(summary.timed_out))


async def _may_resolve(database: Database, now: datetime) -> bool:
    """Whether the gap, the cap and every recorded wait allow a request."""
    async with database.session() as session:
        newest = await newest_resolution(session, CollectionCommand.WATCH)
        gap = timedelta(minutes=settings.rising_resolve_gap_minutes)
        if newest is not None and now - newest < gap:
            return False
        spent = await resolutions_since(session, now - timedelta(hours=24))
        if sum(spent.values()) >= settings.resolution_cap:
            return False
        held = await held_until(session, methods=RESOLUTION_METHODS, now=now)
        return held is None
