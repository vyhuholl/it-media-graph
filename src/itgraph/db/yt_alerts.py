"""YouTube's alert queue: claiming, holding, delivering, excluding, and
what a message about one reads.

Telegram's queue's discipline with one difference that runs through all
of it: where an alert goes is its channel's language **at the moment of
sending**, so every claim joins through the video to the channel's
current label. A relabelled channel's alert follows the label; one whose
channel turned `other`, or has no label left to route by, is closed as
excluded rather than sent.

Raising is the scoring pass's: a head and its folds written in one
transaction, each naming the reading its score came from. Nothing here
copies what YouTube said onto an alert: the evidence is read from where it
is stored, at the moment of sending.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from itgraph.db.alerts import OutstandingAlerts
from itgraph.db.models import (
    YtAlert,
    YtAlertDelivery,
    YtAlertKind,
    YtAlertRoute,
    YtChannel,
    YtLang,
    YtMetric,
    YtRawChannel,
    YtRawVideo,
    YtRawVideoStat,
    YtVideoFormat,
)
from itgraph.derive.yt_videos import Counters, facts_of, format_of
from itgraph.scoring.fold import Head, Placed
from itgraph.scoring.score import Spike

__all__ = [
    "YtEvidence",
    "YtPendingAlert",
    "YtRouteCounts",
    "claim",
    "count_direct_since",
    "exclude_unroutable",
    "failing",
    "folded_videos",
    "hold",
    "mark_delivered",
    "mark_failed",
    "open_yt_heads",
    "outstanding",
    "raise_yt_spikes",
    "raised_yt_spikes",
    "route_counts",
    "settle_folds",
    "video_evidence",
    "yt_alert_evidence",
]

# The language each route stands for. The values coincide, and the
# mapping is written out so that a later route cannot silently mean a
# language nobody labels.
LANG = {YtAlertRoute.EN: YtLang.EN, YtAlertRoute.RU: YtLang.RU}


@dataclass(frozen=True, slots=True)
class YtPendingAlert:
    """One YouTube alert the bot has claimed and not yet delivered."""

    id: int
    kind: YtAlertKind
    video_id: str
    scored_at: datetime
    band: int
    value: float
    expected: float | None
    raised_at: datetime
    attempts: int


def _pending(alert: YtAlert) -> YtPendingAlert:
    return YtPendingAlert(
        id=alert.id,
        kind=alert.kind,
        video_id=alert.video_id,
        scored_at=alert.scored_at,
        band=alert.band,
        value=alert.value,
        expected=alert.expected,
        raised_at=alert.raised_at,
        attempts=alert.attempts,
    )


async def claim(
    session: AsyncSession,
    route: YtAlertRoute,
    *,
    held: bool,
    limit: int | None,
) -> list[YtPendingAlert]:
    """Take the outstanding alerts of one route, those never failed first.

    Telegram's ``claim_undelivered``, with the route read through the
    video's channel as it is labelled now. The lock is on the alerts
    alone: the bot reads the inventory and the payloads, and has no
    business locking them against the collector.
    """
    rows = await session.execute(
        select(YtAlert)
        .join(YtRawVideo, YtRawVideo.video_id == YtAlert.video_id)
        .join(YtChannel, YtChannel.channel_id == YtRawVideo.channel_id)
        .where(
            YtAlert.delivered_at.is_(None),
            YtAlert.folded_into.is_(None),
            YtAlert.held_at.is_not(None)
            if held
            else YtAlert.held_at.is_(None),
            YtChannel.lang == LANG[route],
        )
        .order_by(YtAlert.attempts > 0, YtAlert.raised_at, YtAlert.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=YtAlert)
    )
    return [_pending(alert) for alert in rows.scalars()]


async def exclude_unroutable(session: AsyncSession, *, at: datetime) -> int:
    """Close as excluded every outstanding alert no topic takes.

    A channel labelled `other` gets no alerts. So does a channel whose
    label was cleared, or a video YouTube now attributes to a channel not
    in the inventory: neither has a language to route by, and leaving the
    alert outstanding forever would read as delivery being stuck. Closed
    rather than deleted, so that "excluded by its label" and "never
    sent" stay different facts.
    """
    routable = (
        select(YtRawVideo.video_id)
        .join(YtChannel, YtChannel.channel_id == YtRawVideo.channel_id)
        .where(YtChannel.lang.in_(list(LANG.values())))
    )
    result = await session.execute(
        update(YtAlert)
        .where(
            YtAlert.delivered_at.is_(None),
            YtAlert.folded_into.is_(None),
            YtAlert.video_id.not_in(routable),
        )
        .values(delivered_at=at, delivery=YtAlertDelivery.EXCLUDED)
        .returning(YtAlert.id)
    )
    return len(result.all())


async def hold(
    session: AsyncSession, alert_ids: Sequence[int], *, at: datetime
) -> None:
    """Set these alerts aside for their topic's digest."""
    if not alert_ids:
        return
    await session.execute(
        update(YtAlert)
        .where(YtAlert.id.in_(alert_ids), YtAlert.held_at.is_(None))
        .values(held_at=at)
    )


async def mark_delivered(
    session: AsyncSession,
    alert_ids: Sequence[int],
    *,
    route: YtAlertRoute,
    delivery: YtAlertDelivery,
    at: datetime,
) -> None:
    """Record that these alerts reached a topic, and which.

    After the send, in a transaction of its own: Telegram's ordering and
    Telegram's reasons (``db/alerts.mark_delivered``).
    """
    if not alert_ids:
        return
    await session.execute(
        update(YtAlert)
        .where(YtAlert.id.in_(alert_ids))
        .values(
            delivered_at=at, delivery=delivery, route=route, last_error=None
        )
    )


async def mark_failed(
    session: AsyncSession, alert_ids: Sequence[int], *, error: str
) -> None:
    """Record a failed send, leaving the alerts outstanding."""
    if not alert_ids:
        return
    await session.execute(
        update(YtAlert)
        .where(YtAlert.id.in_(alert_ids))
        .values(attempts=YtAlert.attempts + 1, last_error=error[:500])
    )


async def count_direct_since(
    session: AsyncSession, route: YtAlertRoute, *, since: datetime
) -> int:
    """How many alerts went straight to this topic since a moment.

    Counted from the rows, per route, so that one topic's busy day
    spends its own cap and not the other's.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(YtAlert)
            .where(
                YtAlert.delivered_at >= since,
                YtAlert.delivery == YtAlertDelivery.DIRECT,
                YtAlert.route == route,
            )
        )
        or 0
    )


async def settle_folds(session: AsyncSession, *, at: datetime) -> int:
    """Close every fold whose head has left the queue. Returns how many.

    Folded where the head was told, excluded where the head was excluded
    — a fold is about the same channel, so it has the same label — and
    with the head's route either way.
    """
    head = aliased(YtAlert)
    # Typed, or Postgres reads the branches of the CASE as text.
    delivery_type = YtAlert.__table__.c.delivery.type
    result = await session.execute(
        update(YtAlert)
        .where(
            YtAlert.delivered_at.is_(None),
            YtAlert.folded_into == head.id,
            head.delivered_at.is_not(None),
        )
        .values(
            delivered_at=at,
            delivery=case(
                (
                    head.delivery == YtAlertDelivery.EXCLUDED,
                    literal(YtAlertDelivery.EXCLUDED, delivery_type),
                ),
                else_=literal(YtAlertDelivery.FOLDED, delivery_type),
            ),
            route=head.route,
        )
        .returning(YtAlert.id)
    )
    return len(result.all())


async def folded_videos(session: AsyncSession, *, head: YtPendingAlert) -> int:
    """How many *other* videos were folded into this head."""
    return (
        await session.scalar(
            select(func.count(func.distinct(YtAlert.video_id))).where(
                YtAlert.folded_into == head.id,
                YtAlert.video_id != head.video_id,
            )
        )
        or 0
    )


async def outstanding(session: AsyncSession) -> OutstandingAlerts:
    """Everything undelivered, held included, folds and excluded not.

    Counted without naming a column the exporter's role cannot read, so
    the series can ask it through that role.
    """
    total, never, oldest, held = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(
                    YtAlert.attempts == 0, YtAlert.held_at.is_(None)
                ),
                func.min(YtAlert.raised_at),
                func.count().filter(YtAlert.held_at.is_not(None)),
            )
            .select_from(YtAlert)
            .where(
                YtAlert.delivered_at.is_(None), YtAlert.folded_into.is_(None)
            )
        )
    ).one()
    return OutstandingAlerts(
        total=int(total or 0),
        never_attempted=int(never or 0),
        oldest_raised_at=oldest,
        held=int(held or 0),
    )


async def failing(session: AsyncSession, *, attempts: int) -> int:
    """How many outstanding YouTube alerts have failed at least this often."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(YtAlert)
            .where(
                YtAlert.delivered_at.is_(None), YtAlert.attempts >= attempts
            )
        )
        or 0
    )


@dataclass(frozen=True, slots=True)
class YtRouteCounts:
    """What `/status` says about one topic, or about every alert at once."""

    undelivered: int = 0
    held: int = 0
    failing: int = 0


async def route_counts(
    session: AsyncSession, *, failing_after: int
) -> tuple[dict[str, int], dict[YtAlertRoute, YtRouteCounts]]:
    """Totals of the queue, and what waits for each topic as labelled now.

    The totals: raised, folded, excluded. What waits is routed the way a
    send would route it, through the channel's current label, so the
    status and the delivery cannot disagree about where an alert is going.
    """
    raised, folded, excluded = (
        await session.execute(
            select(
                func.count(YtAlert.id),
                func.count(YtAlert.id).filter(
                    YtAlert.folded_into.is_not(None)
                ),
                func.count(YtAlert.id).filter(
                    YtAlert.delivery == YtAlertDelivery.EXCLUDED,
                    YtAlert.folded_into.is_(None),
                ),
            )
        )
    ).one()
    rows = await session.execute(
        select(
            YtChannel.lang,
            func.count(YtAlert.id),
            func.count(YtAlert.id).filter(YtAlert.held_at.is_not(None)),
            func.count(YtAlert.id).filter(YtAlert.attempts >= failing_after),
        )
        .join(YtRawVideo, YtRawVideo.video_id == YtAlert.video_id)
        .join(YtChannel, YtChannel.channel_id == YtRawVideo.channel_id)
        .where(YtAlert.delivered_at.is_(None), YtAlert.folded_into.is_(None))
        .group_by(YtChannel.lang)
    )
    by_lang = {lang: (n, h, f) for lang, n, h, f in rows}
    waiting = {
        route: YtRouteCounts(*by_lang.get(lang, (0, 0, 0)))
        for route, lang in LANG.items()
    }
    totals = {
        "raised": int(raised or 0),
        "folded": int(folded or 0),
        "excluded": int(excluded or 0),
    }
    return totals, waiting


@dataclass(frozen=True, slots=True)
class YtEvidence:
    """What a message about a scored video needs, read when it is sent.

    Never stored: each field is read from where it is kept, and goes when
    that does. ``started_at`` is the broadcast's actual start where there
    is one, else publication, as the schedule counts age.
    """

    title: str | None
    channel_title: str | None
    started_at: datetime | None
    counters: Counters
    counted_at: datetime | None
    # A comment spike's count, from the reading it was scored on.
    scored_comments: int | None
    # Every counter of the reading it was scored on, absent ones absent.
    scored: Counters = field(default_factory=Counters)
    # Worked out from the payload when the caller asks, never stored.
    format: YtVideoFormat | None = None


def _title(payload: dict[str, Any] | None) -> str | None:
    snippet = (payload or {}).get("snippet") or {}
    title = snippet.get("title")
    return title if isinstance(title, str) and title.strip() else None


async def yt_alert_evidence(
    session: AsyncSession, alert: YtPendingAlert
) -> YtEvidence | None:
    """The video an alert is about, its channel and its newest counts."""
    return await video_evidence(
        session, video_id=alert.video_id, scored_at=alert.scored_at
    )


async def video_evidence(
    session: AsyncSession,
    *,
    video_id: str,
    scored_at: datetime,
    short_max_seconds: int | None = None,
) -> YtEvidence | None:
    """A scored video, its channel, its newest counts and its scored ones.

    ``None`` when the video is no longer stored — YouTube stopped
    returning it. A channel without a stored payload is left out, and the
    message goes without the channel's name. The format is worked out
    only when ``short_max_seconds`` is given.
    """
    video = (
        await session.execute(
            select(YtRawVideo.payload, YtRawVideo.channel_id).where(
                YtRawVideo.video_id == video_id
            )
        )
    ).first()
    if video is None:
        return None
    payload, channel_id = video

    channel_payload = await session.scalar(
        select(YtRawChannel.payload).where(
            YtRawChannel.channel_id == channel_id
        )
    )
    newest = (
        await session.execute(
            select(
                YtRawVideoStat.observed_at,
                YtRawVideoStat.views,
                YtRawVideoStat.likes,
                YtRawVideoStat.comments,
            )
            .where(YtRawVideoStat.video_id == video_id)
            .order_by(YtRawVideoStat.observed_at.desc())
            .limit(1)
        )
    ).first()
    scored_row = (
        await session.execute(
            select(
                YtRawVideoStat.views,
                YtRawVideoStat.likes,
                YtRawVideoStat.comments,
            ).where(
                YtRawVideoStat.video_id == video_id,
                YtRawVideoStat.observed_at == scored_at,
            )
        )
    ).first()
    scored = (
        Counters(
            views=scored_row[0], likes=scored_row[1], comments=scored_row[2]
        )
        if scored_row is not None
        else Counters()
    )

    facts = facts_of(payload)
    return YtEvidence(
        title=_title(payload),
        channel_title=_title(channel_payload),
        started_at=facts.actual_start or facts.published_at,
        counters=(
            Counters(views=newest[1], likes=newest[2], comments=newest[3])
            if newest is not None
            else Counters()
        ),
        counted_at=newest[0] if newest is not None else None,
        scored_comments=scored.comments,
        scored=scored,
        format=(
            format_of(payload, short_max_seconds=short_max_seconds)
            if short_max_seconds is not None
            else None
        ),
    )


def _kind(spike: Spike[str, str]) -> YtAlertKind:
    return YtMetric(spike.score.metric).alert_kind()


async def raised_yt_spikes(
    session: AsyncSession, video_ids: Iterable[str]
) -> set[tuple[YtAlertKind, str]]:
    """Which of these videos already have an alert of a kind, folded or not.

    What a live pass removes from its spikes before folding them: it
    re-scores every video in its window, and one raised an hour ago would
    otherwise take part in folding as a new crossing.
    """
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        return set()
    rows = await session.execute(
        select(YtAlert.kind, YtAlert.video_id).where(YtAlert.video_id.in_(ids))
    )
    return {(kind, video) for kind, video in rows.tuples()}


async def open_yt_heads(
    session: AsyncSession, *, since: datetime
) -> dict[str, Head[str, str]]:
    """Each channel's newest head raised since a moment.

    The channel is the one YouTube attributes the head's video to now, as
    for a spike being folded: two videos of one channel are one event
    whichever channel they were published on.
    """
    rows = await session.execute(
        select(
            YtAlert.id,
            YtRawVideo.channel_id,
            YtAlert.video_id,
            YtAlert.raised_at,
        )
        .join(YtRawVideo, YtRawVideo.video_id == YtAlert.video_id)
        .where(
            YtAlert.folded_into.is_(None),
            YtAlert.raised_at >= since,
            YtRawVideo.channel_id.is_not(None),
        )
        .distinct(YtRawVideo.channel_id)
        .order_by(
            YtRawVideo.channel_id, YtAlert.raised_at.desc(), YtAlert.id.desc()
        )
    )
    return {
        channel: Head(
            alert_id=alert_id, post_key=(channel, video), raised_at=raised_at
        )
        for alert_id, channel, video, raised_at in rows.tuples()
        if channel is not None
    }


async def raise_yt_spikes(
    session: AsyncSession, placed: Sequence[Placed[str, str]]
) -> tuple[int, int]:
    """Record spike alerts, heads and folds. Returns (new, of which folded).

    Telegram's ``raise_spikes`` for videos: heads first, so their ids exist
    for the folds, in the caller's one transaction, and a second raise of a
    kind and video absorbed by the constraint. The band is always 1. The
    reading named is the one the score came from; ``expected`` is kept for
    a comment spike alone, whose message states it — the count is read back
    from that reading.
    """
    if not placed:
        return 0, 0

    def row(spike: Spike[str, str]) -> dict[str, object]:
        if spike.scored_at is None:
            raise ValueError(
                f"a spike of {spike.post_key[1]} names no reading to point at"
            )
        kind = _kind(spike)
        return {
            "kind": kind,
            "video_id": spike.post_key[1],
            "scored_at": spike.scored_at,
            "band": 1,
            "value": float(spike.score.z),
            "expected": (
                float(spike.score.expected)
                if kind is YtAlertKind.COMMENT_SPIKE
                else None
            ),
        }

    async def write(
        rows: list[dict[str, object]],
    ) -> list[tuple[int, YtAlertKind, str]]:
        if not rows:
            return []
        result = await session.execute(
            insert(YtAlert)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_yt_alerts_video_band")
            .returning(YtAlert.id, YtAlert.kind, YtAlert.video_id)
        )
        return [(alert_id, kind, video) for alert_id, kind, video in result]

    heads = await write(
        [row(entry.spike) for entry in placed if entry.into is None]
    )
    ids = {(kind, video): alert_id for alert_id, kind, video in heads}

    folds: list[dict[str, object]] = []
    for entry in placed:
        if entry.into is None:
            continue
        if isinstance(entry.into, Head):
            head_id: int | None = entry.into.alert_id
        else:
            head_id = ids.get((_kind(entry.into), entry.into.post_key[1]))
        if head_id is None:
            continue
        folds.append({**row(entry.spike), "folded_into": head_id})
    folded = await write(folds)
    return len(heads) + len(folded), len(folded)
