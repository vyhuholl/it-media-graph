"""The two alert queues, behind the one interface delivery needs.

Telegram's queue references the posts it collected; YouTube's, the
readings of videos it tracks. Everything about *how* an alert reaches a
chat — claiming, the cap, quiet hours, the digest, the retry — is the same
for both and lives once, in ``bot/app.py``. What differs lives here: which
rows a destination claims, how an alert reads, and what "excluded" means
for a queue that has it.

The delivery path stays kind-blind and, now, platform-blind: only the
wording looks at what an alert is about.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.bot.destinations import Destination, Platform
from itgraph.bot.render import (
    Carrier,
    SummaryLine,
    render_cascade,
    render_spike,
    render_tg_summary_entry,
    render_youtube,
    render_yt_summary_entry,
)
from itgraph.config import settings
from itgraph.db import alerts as tg
from itgraph.db import yt_alerts as yt
from itgraph.db.models import (
    AlertDelivery,
    AlertKind,
    YtAlertDelivery,
    YtAlertKind,
    YtAlertRoute,
)
from itgraph.db.session import Database

__all__ = ["Delivery", "Pending", "Queue", "TelegramQueue", "YoutubeQueue"]

type Delivery = Literal["direct", "digest"]


class Pending(Protocol):
    """What delivery needs to know of a claimed alert, whichever queue."""

    @property
    def id(self) -> int: ...

    @property
    def raised_at(self) -> datetime: ...


class Queue[P: Pending](ABC):
    """One platform's alerts, as delivery sees them."""

    platform: Platform

    async def exclude(self, session: AsyncSession, *, at: datetime) -> int:
        """Close what no destination takes. Nothing, unless a queue has
        a reason to exclude."""
        return 0

    @abstractmethod
    async def claim(
        self,
        session: AsyncSession,
        destination: Destination,
        *,
        held: bool,
        limit: int | None,
    ) -> Sequence[P]: ...

    @abstractmethod
    async def hold(
        self, session: AsyncSession, ids: Sequence[int], *, at: datetime
    ) -> None: ...

    @abstractmethod
    async def delivered(
        self,
        session: AsyncSession,
        ids: Sequence[int],
        destination: Destination,
        *,
        delivery: Delivery,
        at: datetime,
    ) -> None: ...

    @abstractmethod
    async def failed(
        self, session: AsyncSession, ids: Sequence[int], *, error: str
    ) -> None: ...

    @abstractmethod
    async def sent_directly_since(
        self,
        session: AsyncSession,
        destination: Destination,
        *,
        since: datetime,
    ) -> int: ...

    @abstractmethod
    async def settle(self, session: AsyncSession, *, at: datetime) -> None: ...

    @abstractmethod
    async def render(
        self,
        database: Database,
        alert: P,
        destination: Destination,
        *,
        now: datetime,
    ) -> str | None:
        """The alert as text, or ``None`` if what it is about is gone."""

    @abstractmethod
    async def summary_entries(
        self,
        database: Database,
        destination: Destination,
        *,
        start: datetime,
        end: datetime,
    ) -> list[SummaryLine]:
        """The alerts sent directly here inside ``[start, end)`` as the
        day summary's lines, ordered by publication time.

        Evidence is read as the queues read it; an alert whose post or
        video went before the summary is left out, as a digest leaves
        it out."""


class TelegramQueue(Queue[tg.PendingAlert]):
    """``tg_alerts``: one destination, the chat Telegram's alerts always had."""

    platform = Platform.TELEGRAM

    async def claim(
        self,
        session: AsyncSession,
        destination: Destination,
        *,
        held: bool,
        limit: int | None,
    ) -> Sequence[tg.PendingAlert]:
        return await tg.claim_undelivered(session, limit=limit, held=held)

    async def hold(
        self, session: AsyncSession, ids: Sequence[int], *, at: datetime
    ) -> None:
        await tg.hold_alerts(session, ids, at=at)

    async def delivered(
        self,
        session: AsyncSession,
        ids: Sequence[int],
        destination: Destination,
        *,
        delivery: Delivery,
        at: datetime,
    ) -> None:
        await tg.mark_delivered(
            session, ids, delivery=AlertDelivery(delivery), at=at
        )

    async def failed(
        self, session: AsyncSession, ids: Sequence[int], *, error: str
    ) -> None:
        await tg.mark_failed(session, ids, error=error)

    async def sent_directly_since(
        self,
        session: AsyncSession,
        destination: Destination,
        *,
        since: datetime,
    ) -> int:
        return await tg.count_delivered_since(
            session, since=since, delivery=AlertDelivery.DIRECT
        )

    async def settle(self, session: AsyncSession, *, at: datetime) -> None:
        await tg.settle_folds(session, at=at)

    async def render(
        self,
        database: Database,
        alert: tg.PendingAlert,
        destination: Destination,
        *,
        now: datetime,
    ) -> str | None:
        """Dispatched on the kind, because a kind is exactly a claim about
        what the message should say — the alternative is one wording that
        fits a cascade and describes a view spike as three sources
        reposting."""
        window = timedelta(hours=settings.alert_cascade_window_hours)
        spike = alert.kind is not AlertKind.REPOST_CASCADE
        async with database.session() as session:
            evidence = await tg.alert_evidence(
                session,
                channel_id=alert.channel_id,
                msg_id=alert.msg_id,
                window=window,
            )
            folded = await tg.folded_posts(session, head=alert) if spike else 0
        if evidence is None:
            return None

        if spike:
            return render_spike(
                alert_id=alert.id,
                kind=alert.kind,
                channel_title=evidence.channel_title,
                channel_username=evidence.channel_username,
                msg_id=alert.msg_id,
                published_at=evidence.published_at,
                now=now,
                z=alert.value,
                text=evidence.text,
                folded=folded,
                observed=alert.observed,
                expected=alert.expected,
            ).text

        return render_cascade(
            alert_id=alert.id,
            channel_title=evidence.channel_title,
            channel_username=evidence.channel_username,
            msg_id=alert.msg_id,
            published_at=evidence.published_at,
            now=now,
            families=int(alert.value),
            carriers=[
                Carrier(title=title, username=username)
                for title, username in evidence.carriers
            ],
            text=evidence.text,
        ).text

    async def summary_entries(
        self,
        database: Database,
        destination: Destination,
        *,
        start: datetime,
        end: datetime,
    ) -> list[SummaryLine]:
        lines: list[SummaryLine] = []
        async with database.session() as session:
            alerts = await tg.delivered_between(session, start=start, end=end)
            for alert in alerts:
                # No carriers to read: a cascade's summary entry keeps
                # its families and loses its carrier list.
                evidence = await tg.alert_evidence(
                    session,
                    channel_id=alert.channel_id,
                    msg_id=alert.msg_id,
                    window=timedelta(0),
                )
                if evidence is None:
                    continue
                lines.append(
                    SummaryLine(
                        published_at=evidence.published_at,
                        raised_at=alert.raised_at,
                        alert_id=alert.id,
                        text=render_tg_summary_entry(
                            kind=alert.kind,
                            z=alert.value,
                            observed=alert.observed,
                            expected=alert.expected,
                            channel_title=evidence.channel_title,
                            channel_username=evidence.channel_username,
                            msg_id=alert.msg_id,
                            published_at=evidence.published_at,
                            zone=destination.zone,
                        ),
                    )
                )
        return _as_a_timeline(lines)


def _route(destination: Destination) -> YtAlertRoute:
    if destination.route is None:  # pragma: no cover - built that way
        raise ValueError(f"{destination.key} is not a YouTube topic")
    return destination.route


def _as_a_timeline(lines: list[SummaryLine]) -> list[SummaryLine]:
    """A day's entries as the reader re-reads the day: by publication
    time, not by when the alerts were sent, with ``raised_at`` and then
    the id breaking ties so a post's spike precedes its cascade
    deterministically."""
    return sorted(
        lines,
        key=lambda line: (line.published_at, line.raised_at, line.alert_id),
    )


class YoutubeQueue(Queue[yt.YtPendingAlert]):
    """``yt_alerts``: a destination per language that has a topic.

    An alert belongs to a topic by its channel's language as labelled when
    it is sent, so a claim is per route, and what no route takes is
    excluded before anything is claimed.
    """

    platform = Platform.YOUTUBE

    async def exclude(self, session: AsyncSession, *, at: datetime) -> int:
        return await yt.exclude_unroutable(session, at=at)

    async def claim(
        self,
        session: AsyncSession,
        destination: Destination,
        *,
        held: bool,
        limit: int | None,
    ) -> Sequence[yt.YtPendingAlert]:
        return await yt.claim(
            session, _route(destination), held=held, limit=limit
        )

    async def hold(
        self, session: AsyncSession, ids: Sequence[int], *, at: datetime
    ) -> None:
        await yt.hold(session, ids, at=at)

    async def delivered(
        self,
        session: AsyncSession,
        ids: Sequence[int],
        destination: Destination,
        *,
        delivery: Delivery,
        at: datetime,
    ) -> None:
        await yt.mark_delivered(
            session,
            ids,
            route=_route(destination),
            delivery=YtAlertDelivery(delivery),
            at=at,
        )

    async def failed(
        self, session: AsyncSession, ids: Sequence[int], *, error: str
    ) -> None:
        await yt.mark_failed(session, ids, error=error)

    async def sent_directly_since(
        self,
        session: AsyncSession,
        destination: Destination,
        *,
        since: datetime,
    ) -> int:
        return await yt.count_direct_since(
            session, _route(destination), since=since
        )

    async def settle(self, session: AsyncSession, *, at: datetime) -> None:
        await yt.settle_folds(session, at=at)

    async def render(
        self,
        database: Database,
        alert: yt.YtPendingAlert,
        destination: Destination,
        *,
        now: datetime,
    ) -> str | None:
        async with database.session() as session:
            evidence = await yt.yt_alert_evidence(session, alert)
            folded = await yt.folded_videos(session, head=alert)
        if evidence is None:
            return None
        return render_youtube(
            alert_id=alert.id,
            kind=alert.kind,
            route=_route(destination),
            video_id=alert.video_id,
            title=evidence.title,
            channel_title=evidence.channel_title,
            started_at=evidence.started_at,
            counters=evidence.counters,
            counted_at=evidence.counted_at,
            z=alert.value,
            scored_at=alert.scored_at,
            now=now,
            zone=destination.zone,
            observed=(
                evidence.scored_comments
                if alert.kind is YtAlertKind.COMMENT_SPIKE
                else None
            ),
            expected=alert.expected,
            folded=folded,
        ).text

    async def summary_entries(
        self,
        database: Database,
        destination: Destination,
        *,
        start: datetime,
        end: datetime,
    ) -> list[SummaryLine]:
        lines: list[SummaryLine] = []
        async with database.session() as session:
            alerts = await yt.delivered_between(
                session, _route(destination), start=start, end=end
            )
            for alert in alerts:
                evidence = await yt.yt_alert_evidence(session, alert)
                if evidence is None:
                    continue
                lines.append(
                    SummaryLine(
                        published_at=evidence.started_at or alert.raised_at,
                        raised_at=alert.raised_at,
                        alert_id=alert.id,
                        text=render_yt_summary_entry(
                            kind=alert.kind,
                            z=alert.value,
                            observed=(
                                evidence.scored_comments
                                if alert.kind is YtAlertKind.COMMENT_SPIKE
                                else None
                            ),
                            expected=alert.expected,
                            title=evidence.title,
                            channel_title=evidence.channel_title,
                            started_at=evidence.started_at,
                            video_id=alert.video_id,
                            zone=destination.zone,
                        ),
                    )
                )
        return _as_a_timeline(lines)
