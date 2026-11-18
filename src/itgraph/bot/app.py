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

**And the weekly lists** of both platforms — one per YouTube topic and
week, and Telegram's one a week to its chat: not alerts, so neither
capped nor held for the digest, but sent only outside quiet hours, in
parts, each part marked as it goes — so a failed part is sent again and
the parts before it are not. That loop is one; what differs per platform
— which lists a destination takes, how a header and an entry read, how
a list is marked — is its ``WeeklySource``.

**A list's second wind** (`add-second-wind`) follows its entries in the
same parts, and only for a list computed while its platform's section was
switched on: entries and second wind entries are one run of items, each
part marking what it carried, so a resumed list sends neither twice.

**And Telegram's rising offers** (`tg-rising-review`): a card per offer to
Telegram's chat, in rank order, with the buttons that review it — outside
quiet hours, neither capped nor digested, each marked as it goes. An offer
whose channel was reviewed some other way first is closed unsent.

**And YouTube's** (`yt-rising-review`): the same cards into the YouTube
chat's topics, each to the topic of its language — a chart offer's by its
region, a link offer's read from its payload's `defaultLanguage` when that
is one of the two, else the English topic — each topic claiming its own,
so a topic that refuses fails the send and nothing of the offer goes
anywhere else.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.bot.destinations import (
    Destination,
    Platform,
    telegram_destination,
    youtube_destinations,
)
from itgraph.bot.queues import Queue, TelegramQueue, YoutubeQueue
from itgraph.bot.render import (
    SECOND_WIND_NONE,
    TgRevivalView,
    TgWeeklyEntryView,
    WeeklyEntryView,
    YtRevivalView,
    digest_parts,
    message_parts,
    render_tg_revival,
    render_tg_weekly_entry,
    render_weekly_entry,
    render_yt_revival,
    tg_weekly_empty,
    tg_weekly_gone,
    tg_weekly_header,
    weekly_empty,
    weekly_gone,
    weekly_header,
)
from itgraph.bot.review import (
    CardView,
    Keyboard,
    channel_link,
    first_step,
    render_card,
)
from itgraph.bot.yt_review import (
    ChannelView,
    ChartCardView,
    LinkCardView,
    read_channel,
    render_chart_card,
    render_link_card,
)
from itgraph.bot.yt_review import (
    channel_link as yt_channel_link,
)
from itgraph.bot.yt_review import (
    first_step as yt_first_step,
)
from itgraph.config import settings
from itgraph.db import weekly as tg_weekly
from itgraph.db.alerts import alert_evidence, digest_is_due
from itgraph.db.models import ChannelStatus, YtRisingRanking, YtVideoFormat
from itgraph.db.offers import (
    PendingOffer,
    card_posts,
    claim_offers,
    mark_offer_delivered,
    mark_offer_failed,
)
from itgraph.db.session import Database
from itgraph.db.yt_offers import (
    PendingOffer as YtPendingOffer,
)
from itgraph.db.yt_offers import (
    charting_video,
)
from itgraph.db.yt_offers import (
    claim_offers as claim_yt_offers,
)
from itgraph.db.yt_offers import (
    mark_offer_delivered as mark_yt_offer_delivered,
)
from itgraph.db.yt_offers import (
    mark_offer_failed as mark_yt_offer_failed,
)
from itgraph.db.yt_rising import moscow_day_start
from itgraph.db.yt_weekly import (
    PendingList,
    claim_lists,
    entry_evidence,
    mark_entries_delivered,
    mark_list_delivered,
    mark_list_failed,
    mark_revivals_delivered,
    revival_evidence,
)
from itgraph.scoring.weeks import WEEK

__all__ = [
    "BotStats",
    "Sender",
    "TelegramLists",
    "WeeklySource",
    "YoutubeLists",
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
        self,
        text: str,
        *,
        destination: Destination,
        preview: bool = True,
        preview_url: str | None = None,
        keyboard: Keyboard | None = None,
    ) -> None:
        """Send ``text`` to the destination; ``preview=False`` shows no
        link preview, for a message of many links. ``preview_url`` names
        the link to preview, and ``keyboard`` the buttons under the
        message — a rising offer's card alone has either."""
        raise NotImplementedError


@dataclass(slots=True)
class BotStats:
    """What the bot has done since it started."""

    sent: int = 0
    digested: int = 0
    excluded: int = 0
    failed: int = 0
    lists: int = 0
    offers: int = 0
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
        if self.offers:
            line += f", {self.offers} rising offer(s)"
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
                database,
                WEEKLY[queue.platform],
                destination,
                sender,
                stats,
                now=moment,
            )
            await _deliver_offers(
                database, destination, sender, stats, now=moment
            )
            await _deliver_yt_offers(
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


class WeeklyPending(Protocol):
    """What delivery needs to know of a claimed list, whichever platform.

    ``sent`` is how many of its entries went in an earlier part, and
    ``entries`` those still to send.
    """

    @property
    def id(self) -> int: ...

    @property
    def sent(self) -> int: ...

    @property
    def entries(self) -> Sequence[object]: ...

    @property
    def second_wind_shown(self) -> bool: ...

    @property
    def revivals_sent(self) -> int: ...


class WeeklySource[L: WeeklyPending](ABC):
    """One platform's weekly lists, as delivery sees them."""

    @abstractmethod
    def takes(self, destination: Destination) -> bool:
        """Whether this destination receives this platform's lists."""

    @abstractmethod
    async def claim(
        self, session: AsyncSession, destination: Destination
    ) -> Sequence[L]:
        """The destination's undelivered lists, oldest week first."""

    @abstractmethod
    def header(
        self,
        pending: L,
        destination: Destination,
        *,
        continued: bool,
        now: datetime,
    ) -> str: ...

    @abstractmethod
    def empty(self, header: str) -> str:
        """A list with nothing on it."""

    @abstractmethod
    def gone(self, header: str) -> str:
        """A list whose every entry went before it could be sent."""

    @abstractmethod
    async def show(
        self,
        database: Database,
        pending: L,
        destination: Destination,
        *,
        now: datetime,
    ) -> list[tuple[int, str]]:
        """Each entry whose post or video is still there, as its rank and
        text, numbered on from the entries already sent."""

    @abstractmethod
    async def show_revivals(
        self,
        database: Database,
        pending: L,
        destination: Destination,
        *,
        now: datetime,
    ) -> list[tuple[int, str]]:
        """Each second wind entry whose post or video is still there, as
        its rank and text, numbered on from those already sent; the
        section's heading in the first, when none went before."""

    @abstractmethod
    async def entries_delivered(
        self,
        session: AsyncSession,
        list_id: int,
        ranks: Sequence[int],
        *,
        at: datetime,
    ) -> None: ...

    @abstractmethod
    async def revivals_delivered(
        self,
        session: AsyncSession,
        list_id: int,
        ranks: Sequence[int],
        *,
        at: datetime,
    ) -> None: ...

    @abstractmethod
    async def delivered(
        self, session: AsyncSession, list_id: int, *, at: datetime
    ) -> None: ...

    @abstractmethod
    async def failed(
        self, session: AsyncSession, list_id: int, *, error: str
    ) -> None: ...


class YoutubeLists(WeeklySource[PendingList]):
    """``yt_weekly_lists``: each language's list to its topic."""

    def takes(self, destination: Destination) -> bool:
        return destination.route is not None

    async def claim(
        self, session: AsyncSession, destination: Destination
    ) -> Sequence[PendingList]:
        if destination.route is None:  # pragma: no cover - takes() said so
            return []
        return await claim_lists(session, destination.route)

    def header(
        self,
        pending: PendingList,
        destination: Destination,
        *,
        continued: bool,
        now: datetime,
    ) -> str:
        return weekly_header(
            route=pending.route,
            week_start=pending.week_start,
            computed_at=pending.computed_at,
            videos_scored=pending.videos_scored,
            continued=continued,
            now=now,
            zone=destination.zone,
        )

    def empty(self, header: str) -> str:
        return weekly_empty(header)

    def gone(self, header: str) -> str:
        return weekly_gone(header)

    async def show(
        self,
        database: Database,
        pending: PendingList,
        destination: Destination,
        *,
        now: datetime,
    ) -> list[tuple[int, str]]:
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
        return shown

    async def show_revivals(
        self,
        database: Database,
        pending: PendingList,
        destination: Destination,
        *,
        now: datetime,
    ) -> list[tuple[int, str]]:
        shown: list[tuple[int, str]] = []
        for revival in pending.revivals:
            async with database.session() as session:
                found = await revival_evidence(session, revival)
            if found is None:
                continue
            view = YtRevivalView(
                video_id=revival.video_id,
                title=found.evidence.title,
                channel_title=found.evidence.channel_title,
                started_at=found.evidence.started_at,
                start_views=found.start_views,
                start_at=revival.start_at,
                end_views=found.end_views,
                end_at=revival.end_at,
                acceleration=revival.acceleration,
                share=revival.share,
                others=revival.others,
            )
            number = pending.revivals_sent + len(shown) + 1
            shown.append(
                (
                    revival.rank,
                    render_yt_revival(
                        number,
                        view,
                        now=now,
                        zone=destination.zone,
                        heading=number == 1,
                    ),
                )
            )
        return shown

    async def entries_delivered(
        self,
        session: AsyncSession,
        list_id: int,
        ranks: Sequence[int],
        *,
        at: datetime,
    ) -> None:
        await mark_entries_delivered(session, list_id, ranks, at=at)

    async def revivals_delivered(
        self,
        session: AsyncSession,
        list_id: int,
        ranks: Sequence[int],
        *,
        at: datetime,
    ) -> None:
        await mark_revivals_delivered(session, list_id, ranks, at=at)

    async def delivered(
        self, session: AsyncSession, list_id: int, *, at: datetime
    ) -> None:
        await mark_list_delivered(session, list_id, at=at)

    async def failed(
        self, session: AsyncSession, list_id: int, *, error: str
    ) -> None:
        await mark_list_failed(session, list_id, error=error)


class TelegramLists(WeeklySource[tg_weekly.PendingList]):
    """``tg_weekly_lists``: one list a week, to Telegram's chat.

    An entry's post is read at sending as an alert's is, by
    ``alert_evidence``; who carried it is not this message's subject.
    """

    def takes(self, destination: Destination) -> bool:
        return destination.platform is Platform.TELEGRAM

    async def claim(
        self, session: AsyncSession, destination: Destination
    ) -> Sequence[tg_weekly.PendingList]:
        return await tg_weekly.claim_lists(session)

    def header(
        self,
        pending: tg_weekly.PendingList,
        destination: Destination,
        *,
        continued: bool,
        now: datetime,
    ) -> str:
        return tg_weekly_header(
            week_start=pending.week_start,
            computed_at=pending.computed_at,
            posts_scored=pending.posts_scored,
            continued=continued,
            now=now,
            zone=destination.zone,
        )

    def empty(self, header: str) -> str:
        return tg_weekly_empty(header)

    def gone(self, header: str) -> str:
        return tg_weekly_gone(header)

    async def show(
        self,
        database: Database,
        pending: tg_weekly.PendingList,
        destination: Destination,
        *,
        now: datetime,
    ) -> list[tuple[int, str]]:
        shown: list[tuple[int, str]] = []
        for entry in pending.entries:
            async with database.session() as session:
                evidence = await alert_evidence(
                    session,
                    channel_id=entry.channel_id,
                    msg_id=entry.msg_id,
                    window=timedelta(0),
                )
            if evidence is None:
                continue
            view = TgWeeklyEntryView(
                channel_title=evidence.channel_title,
                channel_username=evidence.channel_username,
                msg_id=entry.msg_id,
                published_at=evidence.published_at,
                text=evidence.text,
                kind=entry.metric.alert_kind(),
                z=entry.value,
                observed=entry.observed,
                expected=entry.expected,
                others=entry.others,
            )
            number = pending.sent + len(shown) + 1
            shown.append(
                (entry.rank, render_tg_weekly_entry(number, view, now=now))
            )
        return shown

    async def show_revivals(
        self,
        database: Database,
        pending: tg_weekly.PendingList,
        destination: Destination,
        *,
        now: datetime,
    ) -> list[tuple[int, str]]:
        shown: list[tuple[int, str]] = []
        for revival in pending.revivals:
            async with database.session() as session:
                evidence = await alert_evidence(
                    session,
                    channel_id=revival.channel_id,
                    msg_id=revival.msg_id,
                    window=timedelta(0),
                )
            if evidence is None:
                continue
            view = TgRevivalView(
                channel_title=evidence.channel_title,
                channel_username=evidence.channel_username,
                msg_id=revival.msg_id,
                published_at=evidence.published_at,
                text=evidence.text,
                acceleration=(
                    None
                    if revival.previous_pace is None
                    else revival.pace / revival.previous_pace
                ),
                share=WEEK.days * revival.pace / revival.norm,
                others=revival.others,
            )
            number = pending.revivals_sent + len(shown) + 1
            shown.append(
                (
                    revival.rank,
                    render_tg_revival(
                        number, view, now=now, heading=number == 1
                    ),
                )
            )
        return shown

    async def entries_delivered(
        self,
        session: AsyncSession,
        list_id: int,
        ranks: Sequence[int],
        *,
        at: datetime,
    ) -> None:
        await tg_weekly.mark_entries_delivered(session, list_id, ranks, at=at)

    async def revivals_delivered(
        self,
        session: AsyncSession,
        list_id: int,
        ranks: Sequence[int],
        *,
        at: datetime,
    ) -> None:
        await tg_weekly.mark_revivals_delivered(session, list_id, ranks, at=at)

    async def delivered(
        self, session: AsyncSession, list_id: int, *, at: datetime
    ) -> None:
        await tg_weekly.mark_list_delivered(session, list_id, at=at)

    async def failed(
        self, session: AsyncSession, list_id: int, *, error: str
    ) -> None:
        await tg_weekly.mark_list_failed(session, list_id, error=error)


# Each queue's platform, and its lists.
WEEKLY: dict[Platform, WeeklySource[Any]] = {
    Platform.TELEGRAM: TelegramLists(),
    Platform.YOUTUBE: YoutubeLists(),
}


async def _deliver_weekly[L: WeeklyPending](
    database: Database,
    source: WeeklySource[L],
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """Send this destination's undelivered weekly lists, oldest week first.

    Only outside quiet hours: a list computed at night by a run catching
    up waits for the morning. It is no alert, so the cap does not count it
    and the digest does not take it.
    """
    if not source.takes(destination) or destination.is_quiet(now):
        return
    async with database.session() as session:
        claimed = await source.claim(session, destination)
    for pending in claimed:
        await _send_list(
            database, source, pending, destination, sender, stats, now=now
        )


# What an offer's channel may be for its card to go out: not yet reviewed
# some other way.
OFFERABLE = (ChannelStatus.CANDIDATE, ChannelStatus.MAYBE)


async def _deliver_offers(
    database: Database,
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """Send Telegram's undelivered rising offers, a card each, in order.

    Only to Telegram's chat and outside its quiet hours; no alert, so
    neither the cap nor the digest counts it. A card that fails counts an
    attempt and ends this pass's try, so that the cards after it are not
    sent ahead of it. An offer whose channel was reviewed some other way,
    or lost its username, before its card went is closed unsent.
    """
    if destination.platform is not Platform.TELEGRAM or destination.is_quiet(
        now
    ):
        return
    async with database.session() as session:
        claimed = await claim_offers(session)
    for offer in claimed:
        if offer.status not in OFFERABLE or not offer.username:
            logger.info(
                "rising offer %d closed unsent: its channel was reviewed "
                "or has no username",
                offer.id,
            )
            async with database.session() as session:
                await mark_offer_delivered(
                    session, offer.id, at=datetime.now(UTC)
                )
            continue
        text = await _card(database, offer, offer.username, destination)
        try:
            await sender.send(
                text,
                destination=destination,
                preview_url=channel_link(offer.username),
                keyboard=first_step(offer.id),
            )
        except Exception as exc:
            # Everything, as for an alert: a card that cannot be sent
            # waits, and the bot goes on.
            logger.warning(
                "could not deliver rising offer %d to %s",
                offer.id,
                destination.key,
                exc_info=True,
            )
            async with database.session() as session:
                await mark_offer_failed(
                    session, offer.id, error=f"{type(exc).__name__}: {exc}"
                )
            stats.failed += 1
            return
        async with database.session() as session:
            await mark_offer_delivered(session, offer.id, at=datetime.now(UTC))
        stats.offers += 1


async def _card(
    database: Database,
    offer: PendingOffer,
    username: str,
    destination: Destination,
) -> str:
    """An offer's card, with the seed posts of its window read now."""
    window = timedelta(days=settings.rising_window_days)
    async with database.session() as session:
        posts = await card_posts(
            session,
            offer.channel_id,
            since=offer.offered_at - window,
            until=offer.offered_at,
        )
    return render_card(
        CardView(
            username=username,
            title=offer.title,
            families=offer.families,
            families_before=offer.families_before,
            first_in_window=offer.first_in_window,
            posts=posts,
        ),
        window_days=settings.rising_window_days,
        zone=destination.zone,
    )


async def _deliver_yt_offers(
    database: Database,
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """Send this topic's undelivered YouTube rising offers, a card each,
    in order.

    Only outside this topic's quiet hours; no alert, so neither the cap
    nor the digest counts it. A card that fails counts an attempt and
    ends this pass's try, so the cards after it are not sent ahead of
    it. An offer whose channel was reviewed some other way before its
    card went is closed unsent. Each topic claims its own offers — the
    offer's, by its payload's language when that names one — so a topic
    that refuses fails the send and nothing of the offer goes to any
    other topic or chat.
    """
    if (
        destination.platform is not Platform.YOUTUBE
        or destination.route is None
        or destination.is_quiet(now)
    ):
        return
    async with database.session() as session:
        claimed = await claim_yt_offers(session, destination.route)
    for offer in claimed:
        if offer.status not in OFFERABLE:
            logger.info(
                "YouTube rising offer %d closed unsent: its channel was "
                "reviewed some other way",
                offer.id,
            )
            async with database.session() as session:
                await mark_yt_offer_delivered(
                    session, offer.id, at=datetime.now(UTC)
                )
            continue
        view = read_channel(offer.payload)
        try:
            await sender.send(
                await _yt_card(database, offer, view, destination),
                destination=destination,
                preview_url=yt_channel_link(view, offer.channel_id),
                keyboard=yt_first_step(offer.id),
            )
        except Exception as exc:
            # Everything, as for Telegram's cards: one that cannot be
            # sent waits, and the bot goes on.
            logger.warning(
                "could not deliver YouTube rising offer %d to %s",
                offer.id,
                destination.key,
                exc_info=True,
            )
            async with database.session() as session:
                await mark_yt_offer_failed(
                    session, offer.id, error=f"{type(exc).__name__}: {exc}"
                )
            stats.failed += 1
            return
        async with database.session() as session:
            await mark_yt_offer_delivered(
                session, offer.id, at=datetime.now(UTC)
            )
        stats.offers += 1


async def _yt_card(
    database: Database,
    offer: YtPendingOffer,
    view: ChannelView,
    destination: Destination,
) -> str:
    """A YouTube offer's card, whichever ranking's shape it fills. The
    charting video of a chart offer is read from the stored reading at
    sending, like the channel's own payload."""
    if offer.ranking is YtRisingRanking.CHART:
        async with database.session() as session:
            video = await charting_video(
                session,
                offer.channel_id,
                region=offer.region or "",
                day_start=moscow_day_start(offer.offered_at),
            )
        return render_chart_card(
            ChartCardView(
                channel_id=offer.channel_id,
                channel=view,
                region=offer.region or "",
                position=offer.position or 0,
                video_id=video,
            )
        )
    return render_link_card(
        LinkCardView(
            channel_id=offer.channel_id,
            channel=view,
            families=offer.families or 0,
            families_before=offer.families_before or 0,
            first_in_window=offer.first_in_window,
        ),
        window_days=settings.rising_window_days,
        zone=destination.zone,
    )


# What one item of a list's parts is: an entry, a second wind entry, or the
# one line that says nothing sped up.
ENTRY, REVIVAL, NONE = "entry", "revival", "none"


async def _send_list[L: WeeklyPending](
    database: Database,
    source: WeeklySource[L],
    pending: L,
    destination: Destination,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime,
) -> None:
    """One list, in parts; each part's items marked as it goes.

    A part that fails counts an attempt on the list and ends this pass's
    try, so that what follows is not sent ahead of it; the next pass
    resumes with it, under a header that says the list continues. An entry
    whose post or video went before it was sent is left out, and the
    numbering closes up.

    The second wind follows the entries, for a list computed while it was
    switched on; with nothing in it, one line says so. A list whose own
    entries are none, or gone, says that under its header, and its second
    wind follows.
    """

    def header(*, continued: bool) -> str:
        return source.header(
            pending, destination, continued=continued, now=now
        )

    shown = await source.show(database, pending, destination, now=now)
    items: list[tuple[str, int, str]] = [
        (ENTRY, rank, text) for rank, text in shown
    ]
    if pending.second_wind_shown:
        revived = await source.show_revivals(
            database, pending, destination, now=now
        )
        items += [(REVIVAL, rank, text) for rank, text in revived]
        if not revived and pending.revivals_sent == 0:
            items.append((NONE, 0, SECOND_WIND_NONE))

    started = pending.sent > 0 or pending.revivals_sent > 0
    first = header(continued=started)
    if not shown and not started:
        # Nothing of the list went yet and none of its entries is left: an
        # empty week is said, and so is a list whose every entry went
        # before it could be sent — with its second wind after, if any.
        head = header(continued=False)
        first = source.gone(head) if pending.entries else source.empty(head)

    if not items:
        # What is left of a list whose first part went is nothing to say.
        if not started and not await _send_part(
            database, source, pending, destination, sender, stats, first
        ):
            return
        async with database.session() as session:
            await source.delivered(session, pending.id, at=datetime.now(UTC))
        stats.lists += 1
        return

    parts = message_parts(
        [text for _, _, text in items],
        first=first,
        later=header(continued=True),
    )
    for part in parts:
        if not await _send_part(
            database, source, pending, destination, sender, stats, part.text
        ):
            return
        carried = [items[index] for index in part.entries]
        async with database.session() as session:
            await source.entries_delivered(
                session,
                pending.id,
                [rank for kind, rank, _ in carried if kind == ENTRY],
                at=datetime.now(UTC),
            )
            await source.revivals_delivered(
                session,
                pending.id,
                [rank for kind, rank, _ in carried if kind == REVIVAL],
                at=datetime.now(UTC),
            )
    async with database.session() as session:
        await source.delivered(session, pending.id, at=datetime.now(UTC))
    stats.lists += 1


async def _send_part[L: WeeklyPending](
    database: Database,
    source: WeeklySource[L],
    pending: L,
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
            "could not deliver %s weekly list %d to %s",
            destination.platform.value,
            pending.id,
            destination.key,
            exc_info=True,
        )
        async with database.session() as session:
            await source.failed(
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
