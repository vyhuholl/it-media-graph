"""The scheduled lookup pass: both queues, each under the day's bound.

What ``itgraph yt rising-lookups`` — and only it — runs: two passes in
a fixed order, first the names the Telegram seeds link (``tg-lookup``'s
pass), then the ones the seeds' videos reference (``mention-lookup``'s),
each through the code its own command runs, each stopped after its
setting's requests. The order inside a pass is the pass's own, most
evidence first, and one answer is committed before the next request, so
a run stopped by its bound keeps what it learned and the next run does
not pay for it again.

Each run is recorded in ``yt_lookup_runs`` — moments, each pass's
requests and what it left awaiting, and how it ended — an empty run
included, so a quiet day and a broken one stay distinguishable. The
manual lookup commands are unchanged: their ``--limit`` is the
operator's, and a scheduled run that finds the pool spent records its
stop and refuses nothing to whoever runs next.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from itgraph.config import settings
from itgraph.db.models import YtCommand, YtPassOutcome
from itgraph.db.session import Database
from itgraph.db.yt_lookup_runs import record_run
from itgraph.db.yt_quota import (
    PoolSpentError,
    QuotaError,
    QuotaExhaustedError,
    check_not_exhausted,
)
from itgraph.yt import client as yt_client
from itgraph.yt.backfill import Clock
from itgraph.yt.errors import MissingKeyError, YtError
from itgraph.yt.lookup_pass import LookupSummary
from itgraph.yt.mention_lookup import run_mention_lookups
from itgraph.yt.tg_lookup import run_lookups

__all__ = ["RisingLookupsSummary", "run_rising_lookups"]

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RisingLookupsSummary:
    """What one scheduled run's two passes did, and why it stopped.

    ``tg`` and ``mention`` are each pass's own summary, ``None`` for a
    pass the run never reached. ``attempted`` is false for a run refused
    before its first request — no key, or a quota day YouTube had
    already declared over.
    """

    tg: LookupSummary | None = None
    mention: LookupSummary | None = None
    stopped: QuotaError | YtError | None = None
    attempted: bool = True

    @property
    def outcome(self) -> YtPassOutcome:
        """How the run ended, in the refresh's terms.

        A pass stopped at its setting's bound is a complete run; the
        first genuine stop — the run's own or a pass's — names the
        outcome.
        """
        stops: list[QuotaError | YtError] = [
            stop
            for stop in (
                self.stopped,
                self.tg.stopped if self.tg else None,
                self.mention.stopped if self.mention else None,
            )
            if stop is not None
        ]
        for stop in stops:
            if isinstance(stop, QuotaExhaustedError):
                return YtPassOutcome.EXHAUSTED
            if isinstance(stop, PoolSpentError):
                return YtPassOutcome.POOL_SPENT
            return YtPassOutcome.FAILED
        return YtPassOutcome.COMPLETE

    @property
    def error(self) -> str | None:
        """The first stop's sentence, if the run or a pass stopped."""
        for stop in (
            self.stopped,
            self.tg.stopped if self.tg else None,
            self.mention.stopped if self.mention else None,
        ):
            if stop is not None:
                return str(stop)
        return None


async def run_rising_lookups(
    database: Database, *, clock: Clock = _now
) -> RisingLookupsSummary:
    """Both lookup passes under the day's bounds, recorded. What the
    timer runs.

    Never raises for YouTube: a spent quota or a failure is returned in
    the summaries and the run's record. The record is written after the
    run, in a transaction of its own; a run that raises writes none.
    """
    started = clock()
    summary = RisingLookupsSummary()
    try:
        await check_not_exhausted(database, now=started)
    except QuotaExhaustedError as exc:
        summary.stopped = exc
        summary.attempted = False
    else:
        try:
            # Through the module, not a name imported from it, so a test
            # can put a fake API behind the same call the timer makes.
            async with yt_client.youtube(
                database,
                YtCommand.TG_LOOKUP,
                limit=settings.youtube_tg_lookup_daily_requests,
            ) as client:
                summary.tg = await run_lookups(client, database)
        except MissingKeyError as exc:
            summary.stopped = exc
            summary.attempted = False
        else:
            async with yt_client.youtube(
                database,
                YtCommand.MENTION_LOOKUP,
                limit=settings.youtube_mention_lookup_daily_requests,
            ) as client:
                summary.mention = await run_mention_lookups(client, database)

    async with database.session() as session:
        await record_run(
            session,
            started_at=started,
            finished_at=clock(),
            outcome=summary.outcome,
            tg_requests=summary.tg.requests if summary.tg else 0,
            tg_awaiting=summary.tg.awaiting if summary.tg else 0,
            mention_requests=(
                summary.mention.requests if summary.mention else 0
            ),
            mention_awaiting=(
                summary.mention.awaiting if summary.mention else 0
            ),
            error=summary.error,
        )
    logger.info("rising lookups run: %s", summary.outcome.value)
    return summary
