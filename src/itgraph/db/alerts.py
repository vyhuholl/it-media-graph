"""The alert queue: raising, claiming, delivering, and what was thought.

The one interface between detection and delivery. Everything here is
either a write a pass makes or a write the bot makes, and the two never
touch the same columns — which is what lets the bot run under a database
role that cannot reach anything else.

Two things in this module are easy to get subtly wrong and are called out
where they happen: the transaction boundary around a send, and the fact
that the cap is counted from rows rather than remembered.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from itgraph.alerts.cascade import Cascade
from itgraph.db.models import (
    Alert,
    AlertDelivery,
    AlertFeedback,
    AlertKind,
    AlertVerdict,
    Metric,
)
from itgraph.scoring.score import Spike

__all__ = [
    "CHANNEL",
    "AlertEvidence",
    "OutstandingAlerts",
    "PendingAlert",
    "alert_evidence",
    "claim_undelivered",
    "count_delivered_since",
    "digest_is_due",
    "failing_alerts",
    "listen",
    "mark_delivered",
    "mark_failed",
    "outstanding_alerts",
    "raise_cascades",
    "raise_spikes",
    "raised_bands",
    "record_verdict",
]

logger = logging.getLogger(__name__)

# The notification channel. A constant rather than a setting: both sides
# are in this repository, and a configurable name is one more thing that
# can be configured differently in two places.
CHANNEL = "itgraph_alerts"


@dataclass(frozen=True, slots=True)
class PendingAlert:
    """One alert the bot has claimed and not yet delivered."""

    id: int
    kind: AlertKind
    channel_id: int
    msg_id: int
    band: int
    value: float
    raised_at: datetime
    attempts: int


async def raised_bands(
    session: AsyncSession, *, kind: AlertKind, since: datetime
) -> dict[tuple[int, int], set[int]]:
    """Which bands have already been raised for each recent post.

    An optimisation handed to the detection, never a correctness
    mechanism — the unique constraint is what actually prevents a second
    alert. Bounded by ``since`` for the same reason the detection window
    is bounded: a query that scanned every alert ever raised would grow
    without limit to save inserts that Postgres discards in microseconds.
    """
    rows = await session.execute(
        select(Alert.channel_id, Alert.msg_id, Alert.band).where(
            Alert.kind == kind, Alert.raised_at >= since
        )
    )
    raised: dict[tuple[int, int], set[int]] = {}
    for channel_id, msg_id, band in rows:
        raised.setdefault((channel_id, msg_id), set()).add(band)
    return raised


async def raise_cascades(
    session: AsyncSession, cascades: Sequence[Cascade]
) -> int:
    """Record cascade alerts. Returns how many were new.

    ``ON CONFLICT DO NOTHING`` against the unique constraint, so a
    re-run over unchanged edges writes nothing and a pass on a short
    schedule is safe. The constraint is also the escalation rule: a post
    already raised at band 2 conflicts there and not at band 3.

    The notification goes out in the same transaction as the insert.
    Postgres delivers ``NOTIFY`` at commit, so a listener can never be
    told about a row it cannot yet read — the ordering is the database's
    guarantee rather than something this has to arrange.
    """
    if not cascades:
        return 0

    statement = insert(Alert).values(
        [
            {
                "kind": AlertKind.REPOST_CASCADE,
                "channel_id": cascade.post_key[0],
                "msg_id": cascade.post_key[1],
                "band": cascade.band,
                "value": float(cascade.value),
            }
            for cascade in cascades
        ]
    )
    result = await session.execute(
        statement.on_conflict_do_nothing(
            constraint="uq_alerts_post_band"
        ).returning(Alert.id)
    )
    written = len(result.all())

    if written:
        await session.execute(select(func.pg_notify(CHANNEL, str(written))))
    return written


async def raise_spikes(session: AsyncSession, spikes: Sequence[Spike]) -> int:
    """Record spike alerts. Returns how many were new.

    The same insert ``raise_cascades`` makes, against the same
    constraint, and deliberately not a generalisation of it: the two
    differ in what ``band`` and ``value`` mean, and a shared function
    taking both as opaque numbers would be the place where that
    distinction is lost.

    **The band is always 1.** For a cascade the band is escalation — two
    families and three families are different events. For a spike it
    would not be: a post that is unusually popular and then more so is
    the same event, and a second message about it would reintroduce from
    the age axis exactly the noise that scoring one metric per post
    removes from the metric axis. So a post raises at most one alert per
    metric, ever, and the constraint enforces it without a flag.

    ``value`` is the z. It is the number that crossed the threshold, and
    it is what a replay at another threshold has to be able to compare
    against. The multiple it corresponds to depends on the spread of the
    baseline run that produced it, which is why the run's parameters are
    kept in ``baseline_runs`` rather than the ratio being frozen here.
    """
    if not spikes:
        return 0

    statement = insert(Alert).values(
        [
            {
                "kind": Metric(spike.score.metric).alert_kind(),
                "channel_id": spike.post_key[0],
                "msg_id": spike.post_key[1],
                "band": 1,
                "value": float(spike.score.z),
            }
            for spike in spikes
        ]
    )
    result = await session.execute(
        statement.on_conflict_do_nothing(
            constraint="uq_alerts_post_band"
        ).returning(Alert.id)
    )
    written = len(result.all())

    if written:
        await session.execute(select(func.pg_notify(CHANNEL, str(written))))
    return written


async def claim_undelivered(
    session: AsyncSession, *, limit: int
) -> list[PendingAlert]:
    """Take up to ``limit`` outstanding alerts, oldest first.

    ``FOR UPDATE SKIP LOCKED`` rather than a process-wide lease. The
    collector's session file genuinely cannot be shared, so it takes a
    lease; an outbound HTTP API can be called from anywhere, so what has
    to be prevented here is one *row* going out twice. Putting the
    protection on the row is also what makes a second bot harmless
    instead of catastrophic.

    The caller must **commit before sending**. Holding this transaction
    open across the send would keep a row lock for the duration of a
    network call — see ``mark_delivered`` for what that trade actually
    is.
    """
    rows = await session.execute(
        select(Alert)
        .where(Alert.delivered_at.is_(None))
        .order_by(Alert.raised_at, Alert.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return [
        PendingAlert(
            id=alert.id,
            kind=alert.kind,
            channel_id=alert.channel_id,
            msg_id=alert.msg_id,
            band=alert.band,
            value=alert.value,
            raised_at=alert.raised_at,
            attempts=alert.attempts,
        )
        for alert in rows.scalars()
    ]


async def mark_delivered(
    session: AsyncSession,
    alert_ids: Sequence[int],
    *,
    delivery: AlertDelivery,
    at: datetime,
) -> None:
    """Record that these alerts reached the operator.

    Called **after** the send, in a transaction of its own, and that
    ordering is the whole of the delivery guarantee. Marking first would
    lose an alert whenever the send failed after the commit; marking
    inside the same transaction as the send would hold a row lock across
    a network call. So: claim, commit, send, mark.

    The residual failure is a process that dies between the send and this
    write, which delivers one message twice. That is at-least-once, and
    it is the best available — Telegram offers no idempotency key that
    could buy exactly-once, and a duplicate alert is a far cheaper
    failure than a missing one.
    """
    if not alert_ids:
        return
    await session.execute(
        update(Alert)
        .where(Alert.id.in_(alert_ids))
        .values(delivered_at=at, delivery=delivery, last_error=None)
    )


async def mark_failed(
    session: AsyncSession, alert_id: int, *, error: str
) -> None:
    """Record a failed send, leaving the alert outstanding.

    ``delivered_at`` stays null, so the next tick picks it up again.
    ``attempts`` is what a backoff and the status report read — an alert
    that has failed many times is worth mentioning rather than retrying
    at full rate forever.
    """
    await session.execute(
        update(Alert)
        .where(Alert.id == alert_id)
        .values(attempts=Alert.attempts + 1, last_error=error[:500])
    )


async def count_delivered_since(
    session: AsyncSession, *, since: datetime, delivery: AlertDelivery
) -> int:
    """How many alerts went out this way since a moment.

    Counted from the rows rather than kept in a counter. ``delivered_at``
    and ``delivery`` already state it, and a second place recording the
    same fact is a second place that can disagree — which, for a cap,
    means either silence or a flood, depending on which way it drifted.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.delivered_at >= since,
                Alert.delivery == delivery,
            )
        )
        or 0
    )


async def record_verdict(
    session: AsyncSession,
    *,
    alert_id: int,
    verdict: AlertVerdict,
    at: datetime,
) -> None:
    """Store what the operator thought, replacing any earlier answer.

    Keyed by the alert, so changing one's mind overwrites rather than
    appends: the question is what they think, not what they have thought.
    """
    statement = insert(AlertFeedback).values(
        alert_id=alert_id, verdict=verdict, given_at=at
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[AlertFeedback.alert_id],
            set_={"verdict": statement.excluded.verdict, "given_at": at},
        )
    )


@dataclass(frozen=True, slots=True)
class OutstandingAlerts:
    """What is waiting, and whether anything has tried to send it.

    ``never_attempted`` is the distinction the status report was missing
    and an operator spent an evening rediscovering with psql. An alert
    that has failed twice and one that nothing has ever picked up are
    both "undelivered", and they mean opposite things: the first is a
    send that keeps failing, the second is a delivery loop that is not
    running. Only the second is invisible everywhere else.
    """

    total: int
    never_attempted: int
    oldest_raised_at: datetime | None

    def oldest_wait(self, now: datetime) -> timedelta | None:
        """How long the oldest outstanding alert has been waiting."""
        if self.oldest_raised_at is None:
            return None
        return now - self.oldest_raised_at


async def outstanding_alerts(session: AsyncSession) -> OutstandingAlerts:
    """Everything undelivered, counted the way the two failures differ."""
    total, never, oldest = (
        await session.execute(
            select(
                func.count(Alert.id),
                func.count(Alert.id).filter(Alert.attempts == 0),
                func.min(Alert.raised_at),
            ).where(Alert.delivered_at.is_(None))
        )
    ).one()
    return OutstandingAlerts(
        total=int(total or 0),
        never_attempted=int(never or 0),
        oldest_raised_at=oldest,
    )


async def failing_alerts(session: AsyncSession, *, attempts: int) -> int:
    """How many outstanding alerts have failed at least this often.

    Read by the status report. An alerting system whose healthy state is
    silence has to be able to say "quiet" and "stuck" in different words.
    """
    return (
        await session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.delivered_at.is_(None), Alert.attempts >= attempts)
        )
        or 0
    )


async def listen(session: AsyncSession) -> None:
    """Subscribe to the notification channel on this connection.

    Latency only. Every alert is delivered by the poll whether or not a
    notification ever arrives — ``NOTIFY`` is not durable, and a bot that
    was down when one fired has no way to learn about it afterwards.
    """
    await session.execute(text(f"LISTEN {CHANNEL}"))


def digest_is_due(now: datetime, *, hour: int, last: datetime | None) -> bool:
    """Whether the summary should go out now.

    True once per day, at or after the configured hour, and not again
    until the next one. ``last`` is when a digest was last sent; a bot
    that has never sent one gets its first at the next occurrence of the
    hour rather than immediately on startup, so restarting is not a way
    to make it repeat itself.
    """
    if now.hour < hour:
        return False
    if last is None:
        return True
    return (now - last) >= timedelta(hours=24) or last.date() < now.date()


@dataclass(frozen=True, slots=True)
class AlertEvidence:
    """What an alert needs to be readable, read when it is rendered.

    Deliberately fetched rather than stored: see ``bot/render.py`` for
    why fresher-at-render is the intended behaviour and not drift.

    Every table touched here is one the bot's database role may
    ``SELECT`` and no more — the inventory, the raw layer, the edges and
    the families view. If this query ever needs another table, the grant
    has to be widened deliberately rather than by the query quietly
    working for whoever ran it in development.
    """

    channel_title: str | None
    channel_username: str | None
    published_at: datetime
    text: str | None
    carriers: list[tuple[str | None, str | None]]


async def alert_evidence(
    session: AsyncSession, *, channel_id: int, msg_id: int, window: timedelta
) -> AlertEvidence | None:
    """The post an alert is about, and who was seen carrying it.

    ``None`` when the post has gone from the raw layer, which should not
    happen and is not worth crashing a delivery loop over.
    """
    from itgraph.db.models import Channel, Edge, EdgeKind, RawMessage

    published = RawMessage.payload["date"].astext.cast(Edge.published_at.type)
    row = (
        await session.execute(
            select(
                Channel.title,
                Channel.username,
                published,
                RawMessage.payload["message"].astext,
            )
            .join(RawMessage, RawMessage.channel_id == Channel.tg_id)
            .where(
                RawMessage.channel_id == channel_id,
                RawMessage.msg_id == msg_id,
            )
        )
    ).first()
    if row is None:
        return None
    title, username, published_at, body = row

    source = aliased(Channel)
    carriers = (
        await session.execute(
            select(source.title, source.username)
            .join(Edge, Edge.src_channel_id == source.tg_id)
            .where(
                Edge.kind == EdgeKind.FORWARD,
                Edge.dst_channel_id == channel_id,
                Edge.dst_msg_id == msg_id,
                Edge.published_at <= published_at + window,
            )
            .distinct()
            .order_by(source.username)
        )
    ).all()

    return AlertEvidence(
        channel_title=title,
        channel_username=username,
        published_at=published_at,
        text=body,
        carriers=[(title, username) for title, username in carriers],
    )
