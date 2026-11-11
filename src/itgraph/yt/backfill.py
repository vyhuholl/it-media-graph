"""The history walk: a year of each seed's uploads, stored as payloads.

What the poll loop sees is a channel's last 29 days. The mention graph
and discovery read who a channel mentions and links to, and a year holds
about twelve times that. So each seed's uploads playlist is walked back
to a depth once, and from then on the year rolls forward without
walking again: the poll loop feeds it at the front, and the refresh
keeps it current.

Five rules shape it.

**Stop where the listing crosses the depth.** A page costs one unit
whether it is read to the end or not, so the walk finishes the page on
which the first item older than the depth appears and lists no further.
Asked to walk a channel whole, it goes to the end of the playlist and
still stores only what the depth admits — which is how the first run
checks the order the stop relies on.

**The order is counted, not trusted.** Nothing documents that an
uploads playlist is newest-first. The newest page measurably is; the
pages after it are not checked by anything but this. Every item dated
later than one listed before it in the same walk is counted, and so is
every item the listing gives no date. An undated item is asked about:
it may be a broadcast yet to start.

**What the poll loop owns is left to it.** A video the listing dates
inside the loop's horizon is not asked about, a video already stored is
not asked about again, and nothing stored is ever overwritten. A
broadcast yet to start, which the loop never sees when its listing date
is old, is stored due at its scheduled start and becomes the loop's.

**Every stored video is also a reading.** The walk asks for the poll
loop's shape, counters included, and keeps the counts as a reading of
the video at the age it was found — the mature level scoring needs.

**Progress per page; no token kept.** Each page and the videos it led
to are committed together, with the walk record's counts. A walk that
stops for any reason leaves its channel unfinished, and the next run
walks it again from the newest page: videos already stored cost no
further unit, and how long a page token lives is not documented.

Nothing is derived — no edge, no mention, no format. Logs carry ids.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from itgraph.config import settings
from itgraph.db.models import YtCommand
from itgraph.db.session import Database
from itgraph.db.yt_backfill import (
    ChannelToWalk,
    PageCounts,
    backfill_lock,
    channels_to_walk,
    finish_walk,
    record_page,
    record_walk_failure,
    start_walk,
)
from itgraph.db.yt_quota import QuotaError, check_not_exhausted
from itgraph.db.yt_videos import (
    FoundVideo,
    known_video_ids,
    store_found_videos,
)
from itgraph.derive.yt_videos import counters_of, facts_of
from itgraph.yt import client as yt_client
from itgraph.yt import videos as yt_videos
from itgraph.yt.client import YouTube
from itgraph.yt.errors import ListingFailed, RequestLimitReached, YtError
from itgraph.yt.uploads import Upload, uploads_page
from itgraph.yt_schedule import horizon, next_check_of_upcoming

__all__ = ["Clock", "WalkReport", "run_walk", "walk_channels"]

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class WalkReport:
    """What one run did, and why it stopped if it did not finish.

    ``stopped`` is a quiet stop — quota, the floor, the request limit —
    after which the next run carries on. ``failed`` is a real failure.
    """

    channels: int = 0
    finished: int = 0
    pages: int = 0
    videos: int = 0
    handed_over: int = 0
    out_of_order: int = 0
    undated: int = 0
    unreturned: int = 0
    capped: int = 0
    failures: int = 0
    stopped: QuotaError | RequestLimitReached | None = None
    failed: YtError | None = None

    def line(self) -> str:
        """The run in one line, as the command prints it."""
        head = (
            "failed"
            if self.failed is not None
            else "stopped"
            if self.stopped is not None
            else "complete"
        )
        line = (
            f"{head}: {self.finished} of {self.channels} channels walked, "
            f"{self.pages} pages, {self.videos} videos, "
            f"{self.handed_over} broadcasts handed to the poll loop; "
            f"out of order {self.out_of_order}, undated {self.undated}, "
            f"not returned {self.unreturned}, capped {self.capped}"
        )
        if self.failures:
            line += f", listing failures {self.failures}"
        return line


@dataclass(slots=True)
class _Order:
    """The earliest date listed so far in one walk, against which each
    later item is checked."""

    earliest: datetime | None = None

    def out_of_place(self, published: datetime) -> bool:
        late = self.earliest is not None and published > self.earliest
        if self.earliest is None or published < self.earliest:
            self.earliest = published
        return late


@dataclass(frozen=True, slots=True)
class _Bounds:
    """The two edges of what a walk stores, fixed when the walk begins."""

    cutoff: datetime  # older than this is past the depth
    horizon_edge: datetime  # this or younger is the poll loop's

    def admits(self, published: datetime) -> bool:
        return self.cutoff <= published < self.horizon_edge


@dataclass(slots=True)
class _Page:
    """One listed page, sorted into what to ask about and what it says."""

    ask: list[str]
    crossed: bool
    counts: PageCounts


def _sort_page(
    uploads: Sequence[Upload], bounds: _Bounds, order: _Order
) -> _Page:
    ask: list[str] = []
    crossed = False
    out_of_order = undated = 0
    for upload in uploads:
        if upload.published_at is None:
            undated += 1
            ask.append(upload.video_id)
            continue
        if order.out_of_place(upload.published_at):
            out_of_order += 1
        if upload.published_at < bounds.cutoff:
            crossed = True
        elif upload.published_at < bounds.horizon_edge:
            ask.append(upload.video_id)
    return _Page(
        ask=ask,
        crossed=crossed,
        counts=PageCounts(out_of_order=out_of_order, undated=undated),
    )


def _found(
    answered: dict[str, yt_videos.Resource],
    ask: Sequence[str],
    bounds: _Bounds,
    at: datetime,
) -> tuple[list[FoundVideo], int]:
    """What of the answer is stored, and how many asked-for ids were missing."""
    found: list[FoundVideo] = []
    missing = 0
    for video_id in ask:
        resource = answered.get(video_id)
        if resource is None:
            missing += 1
            continue
        facts = facts_of(resource)
        if facts.upcoming:
            # The poll loop's from here: checked at its start, given up a
            # week after a start that never came.
            found.append(
                FoundVideo(
                    video_id=video_id,
                    payload=resource,
                    due_at=next_check_of_upcoming(
                        at,
                        scheduled_start=facts.scheduled_start,
                        tracked_since=at,
                        fetched_at=at,
                    ),
                    reading=None,
                )
            )
        elif facts.published_at is not None and bounds.admits(
            facts.published_at
        ):
            found.append(
                FoundVideo(
                    video_id=video_id,
                    payload=resource,
                    due_at=None,
                    reading=counters_of(resource),
                )
            )
    return found, missing


async def _walk_channel(
    youtube: YouTube,
    database: Database,
    channel: ChannelToWalk,
    report: WalkReport,
    *,
    whole: bool,
    clock: Clock,
) -> None:
    """Walk one channel to its end, page by page, committing each.

    ``ListingFailed`` is recorded against the channel and ends its walk;
    every other failure propagates, leaving the channel unfinished.
    """
    began = clock()
    bounds = _Bounds(
        cutoff=began - timedelta(days=settings.youtube_backfill_days),
        horizon_edge=began - horizon(),
    )
    ceiling = settings.youtube_backfill_max_videos
    async with database.session() as session:
        await start_walk(
            session,
            channel.channel_id,
            now=began,
            cutoff=bounds.cutoff,
            whole=whole,
        )

    order = _Order()
    stored = 0
    token: str | None = None
    while True:
        try:
            if channel.uploads is None:
                raise ListingFailed(
                    channel.channel_id,
                    "no uploads playlist in its stored data",
                )
            page = await uploads_page(youtube, channel.uploads, token)
        except ListingFailed as exc:
            report.failures += 1
            logger.warning(
                "listing %s failed: %s", channel.channel_id, exc.said
            )
            async with database.session() as session:
                await record_walk_failure(
                    session, channel.channel_id, error=exc.said
                )
            return

        sorted_page = _sort_page(page.uploads, bounds, order)
        async with database.session() as session:
            known = await known_video_ids(session, sorted_page.ask)
        ask = [
            video_id for video_id in sorted_page.ask if video_id not in known
        ]
        answered = await yt_videos.by_ids(youtube, ask) if ask else {}
        at = clock()
        found, missing = _found(answered, ask, bounds, at)
        capped = False
        if ceiling and stored + len(found) >= ceiling:
            found = found[: ceiling - stored]
            capped = True

        async with database.session() as session:
            kept = await store_found_videos(session, found, fetched_at=at)
            counts = PageCounts(
                videos=len(kept),
                out_of_order=sorted_page.counts.out_of_order,
                undated=sorted_page.counts.undated,
                unreturned=missing,
            )
            await record_page(session, channel.channel_id, counts)
        stored += len(kept)
        report.pages += 1
        report.videos += len(kept)
        report.out_of_order += counts.out_of_order
        report.undated += counts.undated
        report.unreturned += missing
        handed = {video.video_id for video in found if video.due_at}
        for video_id in kept:
            if video_id in handed:
                report.handed_over += 1
                logger.info("handing %s to the poll loop", video_id)

        done = (
            capped
            or page.next_token is None
            or (sorted_page.crossed and not whole)
        )
        if done:
            async with database.session() as session:
                await finish_walk(
                    session, channel.channel_id, now=clock(), capped=capped
                )
            report.finished += 1
            report.capped += int(capped)
            logger.info(
                "walked %s: %d videos%s",
                channel.channel_id,
                stored,
                ", capped" if capped else "",
            )
            return
        token = page.next_token


async def walk_channels(
    youtube: YouTube,
    database: Database,
    *,
    named: Sequence[str] | None = None,
    whole: bool = False,
    clock: Clock = _now,
) -> WalkReport:
    """Walk every seed due, or the named ones. Never raises for YouTube.

    A quota stop or the request limit ends the run quietly, a failed
    request loudly; either way everything before it is committed, and
    the channel in hand is left to be walked again.
    """
    report = WalkReport()
    async with database.session() as session:
        channels = await channels_to_walk(session, named=named)
    report.channels = len(channels)
    try:
        for channel in channels:
            await _walk_channel(
                youtube, database, channel, report, whole=whole, clock=clock
            )
    except (QuotaError, RequestLimitReached) as exc:
        report.stopped = exc
    except YtError as exc:
        report.failed = exc
    return report


async def run_walk(
    database: Database,
    *,
    named: Sequence[str] | None = None,
    whole: bool = False,
    limit: int | None = None,
    clock: Clock = _now,
) -> WalkReport:
    """Take the lock, walk, report. Raises ``BackfillBusyError``.

    The lock is taken before anything else, so a walk refused because
    another runs has done nothing. An exhausted day is a quiet stop with
    no request made.
    """
    async with backfill_lock(database):
        try:
            await check_not_exhausted(database, now=clock())
        except QuotaError as exc:
            report = WalkReport(stopped=exc)
        else:
            # Through the module, so a test can put a fake API behind the
            # same call the CronJob makes.
            async with yt_client.youtube(
                database, YtCommand.BACKFILL, limit=limit
            ) as yt:
                report = await walk_channels(
                    yt, database, named=named, whole=whole, clock=clock
                )
    logger.info("backfill: %s", report.line())
    return report
