"""The YouTube poll loop: one pass, run every fifteen minutes by a timer.

A pass notices new uploads, then reads every video whose moment has come.
It is a oneshot rather than a daemon: there is no account to keep warm,
no connection to keep alive, and a timer that starts a fresh process is
the simplest thing that cannot get stuck in a way nobody sees.

Four rules shape it.

**Discovery is priced per fifty channels, not per channel.** Listing a
channel's uploads is one unit a channel; asking for fifty channels is one
unit for all fifty, and the answer carries each one's public video count.
So every pass asks for every seed, and lists a channel's uploads only
when its count has moved — plus once a day whatever the count says,
because the count is a cheap trigger rather than the truth: an upload and
a deletion in one interval cancel out, and how fast it moves is not
documented. Which path found each video is recorded, so how often the
count misses is measured rather than guessed.

**A moved count is a debt until it is paid.** The count is compared with
the one stored before the payload is replaced, and the debt is written
in that same transaction. It is cleared only once the videos the listing
revealed are stored. A pass that stops in between lists the channel again
next time, for one unit, instead of finding the new count already seen.

**One request serves payload and reading.** ``videos.list`` takes fifty
ids of any channels for one unit and returns each video with its
counters, so the payload is replaced and the reading taken from the same
answer. A video's first reading is the request that starts tracking it.

**The day is paced, and a spent pool is not a failure.** When the loop is
ahead of an even spread across the quota day, the daily check and the
readings of videos older than a week wait. When its pool is spent, or
YouTube has declared the day over, the pass stops and says when spending
resumes; the next pass after the reset picks up where the schedule is.

Nothing is derived. Payloads and readings are stored as YouTube gave
them; no format, score or alert is worked out here. Logs carry ids.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched
from typing import Any

from itgraph.db.models import (
    YtCommand,
    YtDiscoveryPath,
    YtPassOutcome,
    YtQuotaPool,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import mark_gone, store_payloads
from itgraph.db.yt_quota import (
    PoolSpentError,
    QuotaError,
    QuotaExhaustedError,
    check_not_exhausted,
    pool_cap,
    pool_spent,
)
from itgraph.db.yt_videos import (
    DueVideo,
    VideoWrite,
    due_videos,
    finish_videos,
    known_video_ids,
    remove_videos,
    store_videos,
)
from itgraph.db.yt_watch import (
    ChannelToList,
    PassRecord,
    channels_to_list,
    owe_listings,
    record_listing_failure,
    record_listings,
    record_pass,
    stored_video_counts,
    watch_lock,
    watched_channels,
)
from itgraph.derive.yt_videos import VideoFacts, counters_of, facts_of
from itgraph.yt import channels as yt_channels
from itgraph.yt import client as yt_client
from itgraph.yt import videos as yt_videos
from itgraph.yt.client import YouTube
from itgraph.yt.errors import ListingFailed, YtError
from itgraph.yt.uploads import Upload, newest_uploads
from itgraph.yt_schedule import (
    deferrable,
    horizon,
    next_check_of_upcoming,
    next_reading_at,
    origin,
    pace_allows,
)

__all__ = ["Clock", "run_pass", "watch_pass"]

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _Found:
    """What this pass's listings turned up, waiting to be read.

    ``owed`` holds, per listed channel, the candidates not yet handled:
    a channel's debt is cleared in the transaction that handles its last
    one, and not before.
    """

    reasons: dict[str, YtDiscoveryPath] = field(default_factory=dict)
    owed: dict[str, set[str]] = field(default_factory=dict)

    def handled(self, video_ids: set[str]) -> list[str]:
        """Channels whose every candidate is now handled."""
        paid = []
        for channel_id, pending in self.owed.items():
            pending -= video_ids
            if not pending:
                paid.append(channel_id)
        for channel_id in paid:
            del self.owed[channel_id]
        return paid


async def _detect(
    youtube: YouTube,
    database: Database,
    record: PassRecord,
    clock: Clock,
) -> set[str]:
    """Ask for every seed; owe a listing to those whose count moved.

    Returns the seeds' ids — the channels whose videos are read.
    """
    async with database.session() as session:
        watched = await watched_channels(session)
    record.channels = len(watched)
    ids = [channel.channel_id for channel in watched]

    for batch in batched(ids, yt_channels.IDS_PER_CALL):
        async with database.session() as session:
            before = await stored_video_counts(session, batch)
        found = await yt_channels.by_ids(youtube, batch)
        at = clock()
        moved = [
            channel_id
            for channel_id, resource in found.items()
            if before.get(channel_id) != _video_count(resource)
        ]
        async with database.session() as session:
            # One transaction: once the new count is stored, the debt is
            # the only memory that it moved.
            returned = await store_payloads(session, found, fetched_at=at)
            gone = await mark_gone(
                session,
                [
                    channel_id
                    for channel_id in batch
                    if channel_id not in found
                ],
                now=at,
            )
            await owe_listings(session, moved)
        for channel_id in gone:
            logger.info("YouTube no longer returns %s", channel_id)
        for channel_id in returned:
            logger.info("YouTube returns %s again", channel_id)
    return set(ids)


def _video_count(resource: dict[str, Any]) -> str | None:
    statistics = resource.get("statistics")
    if not isinstance(statistics, dict):
        return None
    count = statistics.get("videoCount")
    return count if isinstance(count, str) else None


async def _list(
    youtube: YouTube,
    database: Database,
    record: PassRecord,
    clock: Clock,
    *,
    checks_allowed: bool,
) -> _Found:
    """List what is owed, first or due a check; collect the candidates."""
    now = clock()
    async with database.session() as session:
        listing, deferred = await channels_to_list(
            session, now=now, checks_allowed=checks_allowed
        )
    record.deferred_checks = deferred

    found = _Found()
    oldest = now - horizon()
    for channel in listing:
        uploads = await _uploads_of(youtube, database, record, channel, clock)
        if uploads is None:
            continue
        async with database.session() as session:
            known = await known_video_ids(
                session, [upload.video_id for upload in uploads]
            )
        fresh = {
            upload.video_id
            for upload in uploads
            if upload.video_id not in known
            and upload.video_id not in found.reasons
            # A date older than the horizon is an old video. No date at
            # all is asked about: it may be a broadcast yet to start.
            and (upload.published_at is None or upload.published_at >= oldest)
        }
        for video_id in fresh:
            found.reasons[video_id] = channel.reason
        if fresh:
            found.owed[channel.channel_id] = set(fresh)
        else:
            async with database.session() as session:
                await record_listings(session, [channel.channel_id], now=now)
    return found


async def _uploads_of(
    youtube: YouTube,
    database: Database,
    record: PassRecord,
    channel: ChannelToList,
    clock: Clock,
) -> list[Upload] | None:
    """One channel's newest uploads, or ``None`` if that channel failed."""
    try:
        if channel.uploads is None:
            raise ListingFailed(
                channel.channel_id, "no uploads playlist in its stored data"
            )
        uploads = await newest_uploads(youtube, channel.uploads)
    except ListingFailed as exc:
        record.list_failures += 1
        logger.warning("listing %s failed: %s", channel.channel_id, exc.said)
        async with database.session() as session:
            await record_listing_failure(
                session, channel.channel_id, now=clock(), error=exc.said
            )
        return None
    record.listed += 1
    return uploads


def _next_due(
    facts: VideoFacts,
    *,
    now: datetime,
    tracked_since: datetime,
    watched: set[str],
) -> datetime | None:
    """When this video is read or checked next; ``None`` when it is done."""
    if facts.channel_id not in watched:
        return None
    if facts.upcoming:
        return next_check_of_upcoming(
            now,
            scheduled_start=facts.scheduled_start,
            tracked_since=tracked_since,
            fetched_at=now,
        )
    start = origin(facts.published_at, facts.actual_start)
    return next_reading_at(start, now) if start is not None else None


def _worth_tracking(facts: VideoFacts, now: datetime) -> bool:
    """Whether a listed video is new enough to start tracking."""
    if facts.upcoming:
        return True
    return (
        facts.published_at is not None
        and facts.published_at >= now - horizon()
    )


def _youngest_first(videos: list[DueVideo], now: datetime) -> list[DueVideo]:
    def age(video: DueVideo) -> float:
        facts = facts_of(video.payload)
        start = origin(facts.published_at, facts.actual_start)
        return (now - start).total_seconds() if start is not None else 0.0

    return sorted(videos, key=age)


async def _read(
    youtube: YouTube,
    database: Database,
    record: PassRecord,
    clock: Clock,
    found: _Found,
    watched: set[str],
    *,
    defer_old: bool,
) -> None:
    """Read the candidates, then every due video, fifty to a request."""
    now = clock()
    async with database.session() as session:
        due = _youngest_first(list(await due_videos(session, now=now)), now)

    tracked: dict[str, DueVideo] = {}
    finished: list[str] = []
    for video in due:
        facts = facts_of(video.payload)
        start = origin(facts.published_at, facts.actual_start)
        if (
            not facts.upcoming
            and start is not None
            and now - start >= horizon()
        ):
            # Its last reading was missed; a reading now would be one the
            # schedule never asked for.
            finished.append(video.video_id)
            continue
        if (
            defer_old
            and not facts.upcoming
            and start is not None
            and deferrable(start, now)
        ):
            record.deferred_readings += 1
            continue
        tracked[video.video_id] = video
    if finished:
        async with database.session() as session:
            await finish_videos(session, finished)

    order = [*found.reasons, *tracked]
    for batch in batched(order, yt_videos.IDS_PER_CALL):
        answered = await yt_videos.by_ids(youtube, batch)
        at = clock()
        writes: list[VideoWrite] = []
        missing: list[str] = []
        for video_id in batch:
            resource = answered.get(video_id)
            known = tracked.get(video_id)
            if resource is None:
                if known is not None:
                    missing.append(video_id)
                continue
            facts = facts_of(resource)
            if known is None and not _worth_tracking(facts, at):
                continue
            writes.append(
                VideoWrite(
                    video_id=video_id,
                    payload=resource,
                    due_at=_next_due(
                        facts,
                        now=at,
                        tracked_since=known.discovered_at if known else at,
                        watched=watched,
                    ),
                    reading=None if facts.upcoming else counters_of(resource),
                    discovered_via=(
                        known.discovered_via
                        if known is not None
                        else found.reasons[video_id]
                    ),
                )
            )
        paid = found.handled(set(batch))
        async with database.session() as session:
            record.readings += await store_videos(
                session, writes, observed_at=at
            )
            removed = await remove_videos(session, missing)
            await record_listings(session, paid, now=at)
        record.new_videos += sum(
            1 for write in writes if write.video_id not in tracked
        )
        record.removed += len(removed)
        for write in writes:
            if write.video_id not in tracked:
                logger.info("tracking %s", write.video_id)
        for video_id in removed:
            logger.info("YouTube no longer returns %s", video_id)


async def watch_pass(
    youtube: YouTube, database: Database, *, clock: Clock = _now
) -> PassRecord:
    """One pass: detect, list, read. Never raises for YouTube or the quota.

    A spent pool, an exhausted day or a failed request ends the pass with
    its outcome set and everything before it committed.
    """
    record = PassRecord(started_at=clock())
    try:
        watched = await _detect(youtube, database, record, clock)
        on_pace = pace_allows(
            await pool_spent(database, YtQuotaPool.WATCH, now=clock()),
            pool_cap(YtQuotaPool.WATCH),
            clock(),
        )
        found = await _list(
            youtube, database, record, clock, checks_allowed=on_pace
        )
        await _read(
            youtube,
            database,
            record,
            clock,
            found,
            watched,
            defer_old=not on_pace,
        )
    except QuotaError as exc:
        _stopped(record, exc)
    except YtError as exc:
        record.outcome = YtPassOutcome.FAILED
        record.error = str(exc)
    return record


def _stopped(record: PassRecord, exc: QuotaError) -> None:
    record.outcome = (
        YtPassOutcome.EXHAUSTED
        if isinstance(exc, QuotaExhaustedError)
        else YtPassOutcome.POOL_SPENT
        if isinstance(exc, PoolSpentError)
        else YtPassOutcome.FAILED
    )
    record.error = str(exc)


async def run_pass(database: Database, *, clock: Clock = _now) -> PassRecord:
    """Take the lock, make one pass, record it. Raises ``WatchBusyError``.

    The lock is taken before anything else, so a pass refused because
    another runs has done nothing and records nothing. An exhausted day
    is recorded as a pass that made no request.
    """
    async with watch_lock(database):
        try:
            await check_not_exhausted(database, now=clock())
        except QuotaExhaustedError as exc:
            record = PassRecord(started_at=clock())
            _stopped(record, exc)
        else:
            # Through the module, not a name imported from it, so a test
            # can put a fake API behind the same call the timer makes.
            async with yt_client.youtube(database, YtCommand.WATCH) as yt:
                record = await watch_pass(yt, database, clock=clock)
        async with database.session() as session:
            await record_pass(session, record, finished_at=clock())
    logger.info("watch: %s", record.line())
    return record
