"""The alert bot: reads the queues, sends to the configured chats.

The only process here that talks to Telegram over the Bot API rather than
MTProto, and the difference is the point. It holds no Telethon session,
takes no session lease, and therefore runs while collection runs. It also
carries a token that may end up on a machine the operator does not own,
which is why its database role can write nothing but the delivery state
of the two alert queues.

**The poll is the correctness mechanism.** Every alert is delivered
because the poll comes round; a notification, were one listened for,
would only shorten the wait. ``NOTIFY`` is not durable: a bot that was
down when one fired has no way to learn about it afterwards.

The delivery ordering is claim → commit → send → mark, and it is spelled
out in ``db/alerts.py``. The residual failure is a duplicate message, not
a lost one, which is the right way round for something whose whole job is
to tell you when something happened.

Two queues — Telegram's posts, YouTube's videos — and a destination per
chat or topic, each keeping its own account. Everything here is the same
for all of them; what differs is in ``bot/queues.py``.

**And the weekly lists,** one per YouTube topic and week: not alerts, so
neither capped nor held for the digest, but sent only outside quiet
hours, in parts, each part marked as it goes — so a failed part is sent
again and the parts before it are not.
"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from itgraph.bot.destinations import (
    Destination,
    telegram_destination,
    youtube_destinations,
)
from itgraph.bot.queues import Queue, TelegramQueue, YoutubeQueue
from itgraph.bot.render import (
    WeeklyEntryView,
    digest_parts,
    message_parts,
    render_weekly_entry,
    weekly_empty,
    weekly_header,
)
from itgraph.config import settings
from itgraph.db.alerts import digest_is_due
from itgraph.db.models import YtVideoFormat
from itgraph.db.session import Database
from itgraph.db.yt_weekly import (
    PendingList,
    claim_lists,
    entry_evidence,
    mark_entries_delivered,
    mark_list_delivered,
    mark_list_failed,
)

__all__ = [
    "BotStats",
    "Sender",
    "configured_queues",
    "deliver_once",
    "run_bot",
    "supervise",
]

logger = logging.getLogger(__name__)

# How many alerts one pass will claim for one destination. Bounded so a
# burst is delivered in several passes rather than as one wall of
# messages the reader scrolls past.
BATCH = 10

type Queues = Sequence[tuple[Queue[Any], Sequence[Destination]]]


class Sender:
    """What the delivery loop needs from Telegram, and nothing more.

    A seam rather than a wrapper for its own sake: everything below is
    testable without aiogram, a network, or a token, and the one place
    that touches the Bot API is small enough to read in full.
    """

    async def send(
        self, text: str, *, destination: Destination, preview: bool = True
    ) -> None:
        """Send ``text`` to the destination; ``preview=False`` shows no
        link preview, for a message of many links."""
        raise NotImplementedError


@dataclass(slots=True)
class BotStats:
    """What the bot has done since it started."""

    sent: int = 0
    digested: int = 0
    excluded: int = 0
    failed: int = 0
    lists: int = 0
    cycles: int = 0
    # When each destination's digest last went out, by its key. In memory:
    # a bot that restarts sends its next digest at the next hour, not at
    # once, so restarting is not a way to make it repeat itself.
    last_digest_at: dict[str, datetime] = field(default_factory=dict)

    def line(self) -> str:
        line = f"sent {self.sent}, digested {self.digested}"
        if self.excluded:
            line += f", {self.excluded} excluded"
        if self.lists:
            line += f", {self.lists} weekly list(s)"
        if self.failed:
            line += f", {self.failed} failed"
        return line


def configured_queues() -> Queues:
    """Each queue with the destinations the settings give it.

    YouTube's only where its chat is configured: without it no YouTube
    alert is sent, and one raised waits, counted as undelivered.
    """
    queues: list[tuple[Queue[Any], Sequence[Destination]]] = []
    telegram = telegram_destination(settings)
    if telegram is not None:
        queues.append((TelegramQueue(), [telegram]))
    youtube = youtube_destinations(settings)
    if youtube:
        queues.append((YoutubeQueue(), list(youtube.values())))
    return queues


async def deliver_once(
    database: Database,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime | None = None,
    queues: Queues | None = None,
) -> None:
    """One pass over every queue: send what is due, hold what is not.

    Per queue: first close what no destination takes, then for each
    destination send what may go directly and the digest if it is due,
    then settle every fold whose head has gone out — on every pass and
    whatever the pass did, so a fold recorded after its head was sent is
    settled by the same statement as one that was counted in it.
    """
    moment = now or datetime.now(UTC)
    for queue, destinations in (
        configured_queues() if queues is None else queues
    ):
        async with database.session() as session:
            excluded = await queue.exclude(session, at=moment)
        if excluded:
            logger.info(
                "%d %s alert(s) excluded: no topic for their language",
                excluded,
                queue.platform.value,
            )
            stats.excluded += excluded
        for destination in destinations:
            await _deliver_due(
                database, queue, destination, sender, stats, now=moment
            )
            await _maybe_digest(
                database, queue, destination, sender, stats, now=moment
            )
            await _deliver_weekly(
                database, destination, sender, stats, now=moment
            )
        async with database.session() as session:
            await queue.settle(session, at=datetime.now(UTC))


async def _deliver_due(
    database: Database,
    queue: Queue[Any],
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """Send what is due here and set aside what belongs to the digest.

    Two reasons an alert is held rather than sent, and neither is a drop.
    Raised in quiet hours, it belongs to the digest even once the bot may
    speak: otherwise the end of quiet hours would fire the whole night at
    once, which is the burst the window exists to prevent, and a six-hour
    old alert is a record rather than news. Past the day's cap, enough
    has been said today. Either way it is marked held, so the next pass
    claims what was raised after it rather than claiming it again — ten
    of them at the head of the queue used to stop every direct send until
    the digest.
    """
    async with database.session() as session:
        claimed = await queue.claim(
            session, destination, held=False, limit=BATCH
        )
    if not claimed:
        return

    hold = [
        alert.id for alert in claimed if destination.is_quiet(alert.raised_at)
    ]

    # While the window holds, nothing is sent. What was raised before it
    # and not yet sent waits for it to end and goes directly then.
    if not destination.is_quiet(now):
        async with database.session() as session:
            sent_today = await queue.sent_directly_since(
                session, destination, since=now - timedelta(days=1)
            )
        for alert in claimed:
            if alert.id in hold:
                continue
            if sent_today >= destination.daily_cap:
                hold.append(alert.id)
                continue
            text = await queue.render(database, alert, destination, now=now)
            if text is None:
                logger.warning(
                    "%s alert %d refers to something that is gone; skipping",
                    queue.platform.value,
                    alert.id,
                )
                continue
            try:
                await sender.send(text, destination=destination)
            except Exception as exc:
                # Deliberately everything. A send failing for any reason
                # must leave the alert outstanding rather than take the
                # loop down; the alternative is a bot that dies on one
                # malformed message and stops reporting anything at all.
                logger.warning(
                    "could not deliver %s alert %d to %s",
                    queue.platform.value,
                    alert.id,
                    destination.key,
                    exc_info=True,
                )
                async with database.session() as session:
                    await queue.failed(
                        session,
                        [alert.id],
                        error=f"{type(exc).__name__}: {exc}",
                    )
                stats.failed += 1
                continue

            async with database.session() as session:
                await queue.delivered(
                    session,
                    [alert.id],
                    destination,
                    delivery="direct",
                    at=datetime.now(UTC),
                )
            stats.sent += 1
            sent_today += 1

    if hold:
        logger.debug(
            "%d alert(s) held for %s's digest", len(hold), destination.key
        )
        async with database.session() as session:
            await queue.hold(session, hold, at=now)


async def _maybe_digest(
    database: Database,
    queue: Queue[Any],
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """Send a destination's summary, if one is due and it holds anything.

    A digest covering nothing is not sent: a daily message saying nothing
    happened trains the reader to ignore the channel, which is the one
    outcome that makes every other guarantee here worthless.

    Every held alert goes, in as many parts as fit. A part that fails
    counts a failed attempt on each of its alerts and is sent again on the
    next pass; the parts already sent are marked and are not. The digest
    counts as sent only once every part has gone.
    """
    if not digest_is_due(
        now,
        hour=destination.digest_hour,
        last=stats.last_digest_at.get(destination.key),
        zone=destination.zone,
    ):
        return

    async with database.session() as session:
        held = await queue.claim(session, destination, held=True, limit=None)

    if not held:
        stats.last_digest_at[destination.key] = now
        return

    entries: list[tuple[int, str]] = []
    gone: list[int] = []
    for alert in held:
        text = await queue.render(database, alert, destination, now=now)
        if text is None:
            gone.append(alert.id)
        else:
            entries.append((alert.id, text))

    parts = digest_parts([text for _, text in entries], held=len(held))
    complete = True
    for number, part in enumerate(parts):
        ids = [entries[index][0] for index in part.entries]
        # What vanished is counted in the header and goes with the first
        # part, so it is settled once the reader has been told the count.
        if number == 0:
            ids += gone
        try:
            await sender.send(part.text, destination=destination)
        except Exception as exc:
            logger.warning(
                "could not deliver part %d of %d of %s's digest",
                number + 1,
                len(parts),
                destination.key,
                exc_info=True,
            )
            async with database.session() as session:
                await queue.failed(
                    session, ids, error=f"{type(exc).__name__}: {exc}"
                )
            stats.failed += len(ids)
            complete = False
            continue
        async with database.session() as session:
            await queue.delivered(
                session,
                ids,
                destination,
                delivery="digest",
                at=datetime.now(UTC),
            )
        stats.digested += len(ids)

    if complete:
        stats.last_digest_at[destination.key] = now


async def _deliver_weekly(
    database: Database,
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """Send this topic's undelivered weekly lists, oldest week first.

    Only YouTube's topics have them, and only outside quiet hours: a list
    computed at night by a run catching up waits for the morning. It is no
    alert, so the cap does not count it and the digest does not take it.
    """
    if destination.route is None or destination.is_quiet(now):
        return
    async with database.session() as session:
        claimed = await claim_lists(session, destination.route)
    for pending in claimed:
        await _send_list(
            database, pending, destination, sender, stats, now=now
        )


async def _send_list(
    database: Database,
    pending: PendingList,
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """One list, in parts; each part's entries marked as it goes.

    A part that fails counts an attempt on the list and ends this pass's
    try, so that what follows is not sent ahead of it; the next pass
    resumes with it, under a header that says the list continues. An entry
    whose video went before it was sent is left out, and the numbering
    closes up.
    """

    def header(*, continued: bool) -> str:
        return weekly_header(
            route=pending.route,
            week_start=pending.week_start,
            computed_at=pending.computed_at,
            videos_scored=pending.videos_scored,
            continued=continued,
            now=now,
            zone=destination.zone,
        )

    shown: list[tuple[int, str]] = []
    for entry in pending.entries:
        async with database.session() as session:
            evidence = await entry_evidence(
                session,
                entry,
                short_max_seconds=settings.youtube_scoring_short_max_seconds,
            )
        if evidence is None:
            continue
        view = WeeklyEntryView(
            video_id=entry.video_id,
            title=evidence.title,
            channel_title=evidence.channel_title,
            short=evidence.format is YtVideoFormat.SHORT,
            started_at=evidence.started_at,
            counters=evidence.counters,
            counted_at=evidence.counted_at,
            metric=entry.metric,
            z=entry.value,
            observed=getattr(evidence.scored, entry.metric.value),
            expected=entry.expected,
            others=entry.others,
        )
        number = pending.sent + len(shown) + 1
        shown.append(
            (
                entry.rank,
                render_weekly_entry(
                    number, view, now=now, zone=destination.zone
                ),
            )
        )

    if not shown:
        if pending.sent == 0:
            text = weekly_empty(header(continued=False))
            if not await _send_part(
                database, pending, destination, sender, stats, text
            ):
                return
        async with database.session() as session:
            await mark_list_delivered(
                session, pending.id, at=datetime.now(UTC)
            )
        stats.lists += 1
        return

    parts = message_parts(
        [text for _, text in shown],
        first=header(continued=pending.sent > 0),
        later=header(continued=True),
    )
    for part in parts:
        if not await _send_part(
            database, pending, destination, sender, stats, part.text
        ):
            return
        async with database.session() as session:
            await mark_entries_delivered(
                session,
                pending.id,
                [shown[index][0] for index in part.entries],
                at=datetime.now(UTC),
            )
    async with database.session() as session:
        await mark_list_delivered(session, pending.id, at=datetime.now(UTC))
    stats.lists += 1


async def _send_part(
    database: Database,
    pending: PendingList,
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    text: str,
) -> bool:
    """Send one message of a list; a failure counted on the list."""
    try:
        await sender.send(text, destination=destination, preview=False)
    except Exception as exc:
        # Everything, as for an alert: a list that cannot be sent waits,
        # and the bot goes on.
        logger.warning(
            "could not deliver weekly list %d to %s",
            pending.id,
            destination.key,
            exc_info=True,
        )
        async with database.session() as session:
            await mark_list_failed(
                session, pending.id, error=f"{type(exc).__name__}: {exc}"
            )
        stats.failed += 1
        return False
    return True


async def run_bot(
    database: Database,
    sender: Sender,
    *,
    stop: asyncio.Event | None = None,
    max_cycles: int | None = None,
) -> BotStats:
    """Deliver alerts until stopped.

    The poll interval is the only thing keeping this correct; a
    notification, were one listened for, would only shorten the wait.
    """
    stats = BotStats()
    stop = stop or asyncio.Event()

    while not stop.is_set():
        if max_cycles is not None and stats.cycles >= max_cycles:
            break
        stats.cycles += 1
        await deliver_once(database, sender, stats)
        with suppress(TimeoutError):
            await asyncio.wait_for(
                stop.wait(), timeout=settings.alert_poll_seconds
            )

    return stats


async def supervise(
    siblings: Sequence[asyncio.Task[Any]], *, stop: asyncio.Event
) -> None:
    """Wait until any sibling finishes, then ask the rest to stop.

    **Returning is the point, including when a sibling returns by
    raising.** The bot runs delivery and the aiogram handlers as
    siblings, and waiting only on ``stop`` made a dead one invisible: if
    delivery raised, its exception sat unread inside a task object
    nobody awaited until shutdown — and because that object stayed
    referenced, even asyncio's "Task exception was never retrieved"
    never fired. The process kept answering ``/status``, systemd kept
    reporting ``active``, and alerts piled up undelivered for hours in
    complete silence. Silence is this system's healthy state, so it is
    the one failure mode it cannot afford to have.

    This only ends the waiting. The caller must then await each task, so
    that whatever killed a sibling is raised rather than discarded, and
    the process exits non-zero for ``Restart=always`` to act on.
    """
    halted = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait(
            [*siblings, halted], return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        # Set unconditionally: a sibling that died has already stopped
        # doing its half, and leaving the other half running would be a
        # bot that receives commands and delivers nothing — precisely
        # the state this exists to end.
        stop.set()
        halted.cancel()
        with suppress(asyncio.CancelledError):
            await halted
