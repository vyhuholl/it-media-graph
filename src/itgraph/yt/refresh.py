"""The refresh pass: fetch again what is about to expire.

Every channel whose payload is missing or older than the refresh age is
asked for again, fifty to a call, whatever its status — rejected ones
included, because five hundred channels cost ten units a cycle and a
channel nobody refreshes is one whose title quietly expires. A channel
YouTube no longer returns is marked gone; one that comes back is
restored.

Each call's results are committed before the next call is made, so a
spent quota or a failure part way keeps everything refreshed before it,
and the next run starts where this one stopped — the due list is simply
shorter.

Logs carry channel ids and nothing YouTube said: the timer runs this
under systemd, and journald keeps its own counsel about how long.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import batched

from itgraph.config import settings
from itgraph.db.session import Database
from itgraph.db.yt_channels import due_for_refresh, mark_gone, store_payloads
from itgraph.db.yt_quota import QuotaError
from itgraph.yt.channels import IDS_PER_CALL, by_ids
from itgraph.yt.client import YouTube
from itgraph.yt.errors import YtError

__all__ = ["RefreshSummary", "refresh_channels"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefreshSummary:
    """What one refresh did, and why it stopped if it did not finish."""

    due: int = 0
    refreshed: int = 0
    gone: list[str] = field(default_factory=list)
    returned: list[str] = field(default_factory=list)
    stopped: QuotaError | YtError | None = None

    def line(self) -> str:
        return (
            f"due {self.due}, refreshed {self.refreshed}, "
            f"gone {len(self.gone)}, back {len(self.returned)}"
        )


async def refresh_channels(
    youtube: YouTube,
    database: Database,
    *,
    now: datetime | None = None,
) -> RefreshSummary:
    """Fetch again every payload due for it. Never raises for YouTube.

    A spent quota or a failure is returned as ``stopped`` with the work
    done before it, which is already committed.
    """
    moment = now or datetime.now(UTC)
    older_than = moment - timedelta(days=settings.youtube_refresh_after_days)
    async with database.session() as session:
        due = await due_for_refresh(session, older_than=older_than)

    summary = RefreshSummary(due=len(due))
    for batch in batched(due, IDS_PER_CALL):
        try:
            found = await by_ids(youtube, batch)
        except (QuotaError, YtError) as exc:
            summary.stopped = exc
            break

        fetched_at = datetime.now(UTC)
        async with database.session() as session:
            returned = await store_payloads(
                session, found, fetched_at=fetched_at
            )
            gone = await mark_gone(
                session,
                [
                    channel_id
                    for channel_id in batch
                    if channel_id not in found
                ],
                now=fetched_at,
            )

        summary.refreshed += len(found)
        summary.returned += returned
        summary.gone += gone
        for channel_id in gone:
            logger.info("YouTube no longer returns %s", channel_id)
        for channel_id in returned:
            logger.info("YouTube returns %s again", channel_id)

    logger.info("refresh: %s", summary.line())
    return summary
