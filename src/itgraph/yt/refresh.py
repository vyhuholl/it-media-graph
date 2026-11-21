"""The refresh pass: fetch again what has grown stale.

Every channel whose payload is missing or older than the refresh age is
asked for again, fifty to a call, whatever its status — rejected ones
included, because five hundred channels cost ten units a cycle. A
channel YouTube no longer returns is marked gone; one that comes back is
restored.

Then every seed's featured list — the channel ids its channel sections
list — is read again once its newest reading is absent or older than the
sections age of its own, a unit a seed, every read appending a reading.
After the channels, so a seed just found gone is not asked about; before
past uploads, which are the larger bill.

Each call's results are committed before the next call is made, so a
spent quota or a failure part way keeps everything refreshed before it,
and the next run starts where this one stopped — the due list is simply
shorter.

While the history walk is enabled, the refresh also keeps the year: it
fetches again every seed's past upload — walked, or finished by the poll
loop — that is younger than the walk's depth, and keeps its counts as a
reading. Past the depth a video stays as it was last read.

**And it ends with the late readings** (`add-late-readings`): the counts
of every past upload it keeps, asked for alone and stored as a reading —
daily while the video is young, weekly after — so a video that takes off
weeks or months after it was published is seen within days. Last,
because fetching a past upload's data takes a reading too, and a video
refreshed in this run is then not due another.

Each run is recorded at its end — how it ended, and how many late
readings were due and taken — which is what the monitoring reads.

Logs carry ids and nothing YouTube said.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import batched

from itgraph.config import settings
from itgraph.db.models import YtCommand, YtPassOutcome
from itgraph.db.session import Database
from itgraph.db.yt_channels import due_for_refresh, mark_gone, store_payloads
from itgraph.db.yt_quota import (
    PoolSpentError,
    QuotaError,
    QuotaExhaustedError,
    check_not_exhausted,
)
from itgraph.db.yt_refresh import record_run
from itgraph.db.yt_sections import sections_due, store_sections
from itgraph.db.yt_videos import (
    past_uploads_due_for_refresh,
    past_uploads_for_late_readings,
    remove_videos,
    replace_payloads,
    store_readings,
)
from itgraph.derive.yt_videos import counters_of
from itgraph.yt import client as yt_client
from itgraph.yt import sections
from itgraph.yt import videos as yt_videos
from itgraph.yt.backfill import Clock
from itgraph.yt.channels import IDS_PER_CALL, by_ids
from itgraph.yt.client import YouTube
from itgraph.yt.errors import MissingKeyError, YtError
from itgraph.yt_schedule import late_reading_due

__all__ = ["RefreshSummary", "refresh_channels", "run_refresh"]

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RefreshSummary:
    """What one refresh did, and why it stopped if it did not finish.

    ``attempted`` is false for a run refused before its first request,
    on a quota day YouTube had already declared over.
    """

    due: int = 0
    refreshed: int = 0
    gone: list[str] = field(default_factory=list)
    returned: list[str] = field(default_factory=list)
    sections_due: int = 0
    sections_read: int = 0
    past_due: int = 0
    past_refreshed: int = 0
    past_readings: int = 0
    past_removed: int = 0
    late_due: int = 0
    late_read: int = 0
    stopped: QuotaError | YtError | None = None
    attempted: bool = True

    def line(self) -> str:
        line = (
            f"due {self.due}, refreshed {self.refreshed}, "
            f"gone {len(self.gone)}, back {len(self.returned)}; "
            f"featured lists due {self.sections_due}, "
            f"read {self.sections_read}"
        )
        if settings.youtube_backfill_enabled:
            line += (
                f"; past uploads due {self.past_due}, refreshed "
                f"{self.past_refreshed}, removed {self.past_removed}"
            )
            if self.past_readings:
                line += f", readings {self.past_readings}"
            line += (
                f"; late readings due {self.late_due}, read {self.late_read}"
            )
        return line

    @property
    def outcome(self) -> YtPassOutcome:
        """How the run ended, in the poll loop's terms."""
        if self.stopped is None:
            return YtPassOutcome.COMPLETE
        if isinstance(self.stopped, QuotaExhaustedError):
            return YtPassOutcome.EXHAUSTED
        if isinstance(self.stopped, PoolSpentError):
            return YtPassOutcome.POOL_SPENT
        return YtPassOutcome.FAILED


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
    sections_before = moment - timedelta(
        days=settings.youtube_sections_after_days
    )
    async with database.session() as session:
        due = await due_for_refresh(session, older_than=older_than)

    summary = RefreshSummary(due=len(due))
    if summary.stopped is None:
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

    if summary.stopped is None:
        await _refresh_sections(youtube, database, summary, sections_before)
    if summary.stopped is None and settings.youtube_backfill_enabled:
        await _refresh_past_uploads(
            youtube, database, summary, older_than, moment
        )
    if summary.stopped is None and settings.youtube_backfill_enabled:
        await _late_readings(youtube, database, summary, moment)

    logger.info("refresh: %s", summary.line())
    return summary


async def run_refresh(
    database: Database, *, clock: Clock = _now
) -> RefreshSummary:
    """One refresh, recorded. What the timer runs.

    Two runs end before their first request, and both are recorded: on a
    quota day YouTube already declared over, as exhausted; without an API
    key, as failed — "every run is recorded" includes the one that could
    not start. The record is written after the run, in a transaction of
    its own; a run that raises writes none.
    """
    started = clock()
    try:
        await check_not_exhausted(database, now=started)
    except QuotaExhaustedError as exc:
        summary = RefreshSummary(stopped=exc, attempted=False)
    else:
        try:
            # Through the module, not a name imported from it, so a test
            # can put a fake API behind the same call the timer makes.
            async with yt_client.youtube(
                database, YtCommand.REFRESH
            ) as client:
                summary = await refresh_channels(client, database, now=started)
        except MissingKeyError as exc:
            summary = RefreshSummary(stopped=exc, attempted=False)

    async with database.session() as session:
        await record_run(
            session,
            started_at=started,
            finished_at=clock(),
            outcome=summary.outcome,
            late_due=summary.late_due,
            late_read=summary.late_read,
            error=None if summary.stopped is None else str(summary.stopped),
        )
    logger.info("refresh run: %s", summary.outcome.value)
    return summary


async def _refresh_sections(
    youtube: YouTube,
    database: Database,
    summary: RefreshSummary,
    older_than: datetime,
) -> None:
    """Read again every seed whose newest featured reading is stale.

    ``older_than`` is the sections age, not the channels' — a list is
    re-read on its own shorter cadence, and each read appends a reading.
    """
    async with database.session() as session:
        due = await sections_due(session, older_than=older_than)
    summary.sections_due = len(due)

    for channel_id in due:
        try:
            payload = await sections.of_channel(youtube, channel_id)
        except (QuotaError, YtError) as exc:
            summary.stopped = exc
            return
        async with database.session() as session:
            await store_sections(
                session, channel_id, payload, fetched_at=datetime.now(UTC)
            )
        summary.sections_read += 1
        logger.debug("read the featured list of %s", channel_id)


async def _refresh_past_uploads(
    youtube: YouTube,
    database: Database,
    summary: RefreshSummary,
    older_than: datetime,
    now: datetime,
) -> None:
    """Fetch again the year of past uploads the history walk keeps."""
    since = now - timedelta(days=settings.youtube_backfill_days)
    async with database.session() as session:
        due = await past_uploads_due_for_refresh(
            session, older_than=older_than, since=since
        )
    summary.past_due = len(due)

    for batch in batched(due, yt_videos.IDS_PER_CALL):
        try:
            found = await yt_videos.by_ids(youtube, batch)
        except (QuotaError, YtError) as exc:
            summary.stopped = exc
            return

        fetched_at = datetime.now(UTC)
        readings = {
            video_id: counters_of(found[video_id]) for video_id in found
        }
        async with database.session() as session:
            summary.past_readings += await replace_payloads(
                session, found, fetched_at=fetched_at, readings=readings
            )
            removed = await remove_videos(
                session,
                [video_id for video_id in batch if video_id not in found],
            )
        summary.past_refreshed += len(found)
        summary.past_removed += len(removed)
        for video_id in removed:
            logger.info("YouTube no longer returns %s", video_id)


async def _late_readings(
    youtube: YouTube,
    database: Database,
    summary: RefreshSummary,
    now: datetime,
) -> None:
    """Read the counts of every past upload whose interval has come round.

    The counts alone, fifty to a unit; the stored data stays as it was. A
    video the answer does not hold is gone, and removed as the step before
    removes one. Each call is committed before the next.
    """
    since = now - timedelta(days=settings.youtube_backfill_days)
    async with database.session() as session:
        kept = await past_uploads_for_late_readings(session, since=since)
    due = [
        upload.video_id
        for upload in kept
        if late_reading_due(upload.published_at, upload.newest_reading_at, now)
    ]
    summary.late_due = len(due)

    for batch in batched(due, yt_videos.IDS_PER_CALL):
        try:
            found = await yt_videos.by_ids(
                youtube, batch, asked=yt_videos.COUNTS
            )
        except (QuotaError, YtError) as exc:
            summary.stopped = exc
            return

        observed_at = datetime.now(UTC)
        async with database.session() as session:
            summary.late_read += await store_readings(
                session,
                {video_id: counters_of(found[video_id]) for video_id in found},
                observed_at=observed_at,
            )
            removed = await remove_videos(
                session,
                [video_id for video_id in batch if video_id not in found],
            )
        summary.past_removed += len(removed)
        for video_id in removed:
            logger.info("YouTube no longer returns %s", video_id)
