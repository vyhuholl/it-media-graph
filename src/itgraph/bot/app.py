"""The alert bot: reads a queue, sends to one person, records the answer.

The only process here that talks to Telegram over the Bot API rather than
MTProto, and the difference is the point. It holds no Telethon session,
takes no session lease, and therefore runs while collection runs. It also
carries a token that may end up on a machine the operator does not own,
which is why its database role can write nothing but the two alert
tables — see the `bot database role` migration.

**The poll is the correctness mechanism; the notification is the latency
optimisation.** Both loops exist and neither is redundant. ``NOTIFY`` is
not durable: a bot that was down when one fired has no way to learn about
it afterwards, and there is no replay. Every alert is delivered because
the poll comes round, and delivered *quickly* because the notification
usually arrives first. Removing the poll because "we have NOTIFY" would
remove the half that works.

The delivery ordering is claim → commit → send → mark, and it is spelled
out in ``db/alerts.py``. The residual failure is a duplicate message, not
a lost one, which is the right way round for something whose whole job is
to tell you when something happened.
"""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from itgraph.bot.render import (
    Carrier,
    RenderedAlert,
    digest,
    render_cascade,
    render_spike,
)
from itgraph.config import settings
from itgraph.db.alerts import (
    PendingAlert,
    alert_evidence,
    claim_undelivered,
    count_delivered_since,
    digest_is_due,
    mark_delivered,
    mark_failed,
)
from itgraph.db.models import AlertDelivery, AlertKind
from itgraph.db.session import Database
from itgraph.schedule import in_quiet_window

__all__ = ["BotStats", "Sender", "deliver_once", "run_bot"]

logger = logging.getLogger(__name__)

# How many alerts one pass will claim. Bounded so a burst — which the
# scoring change will bring — is delivered in several passes rather than
# as one wall of messages the operator scrolls past.
BATCH = 10


class Sender:
    """What the delivery loop needs from Telegram, and nothing more.

    A seam rather than a wrapper for its own sake: everything below is
    testable without aiogram, a network, or a token, and the one place
    that touches the Bot API is small enough to read in full.
    """

    async def send(self, text: str, *, alert_id: int | None = None) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class BotStats:
    """What the bot has done since it started."""

    sent: int = 0
    digested: int = 0
    failed: int = 0
    cycles: int = 0
    last_digest_at: datetime | None = None
    seen: set[int] = field(default_factory=set)

    def line(self) -> str:
        line = f"sent {self.sent}, digested {self.digested}"
        if self.failed:
            line += f", {self.failed} failed"
        return line


async def _render(
    database: Database, alert: PendingAlert, *, now: datetime
) -> RenderedAlert | None:
    """One alert as a message, or ``None`` if its post has vanished.

    Dispatched on the kind, because a kind is exactly a claim about what
    the message should say — the alternative is one wording that fits a
    cascade and describes a view spike as three sources reposting.
    Everything else about delivery is kind-blind and stays that way: the
    claim, the cap, quiet hours and the retry never look at this.
    """
    window = timedelta(hours=settings.alert_cascade_window_hours)
    async with database.session() as session:
        evidence = await alert_evidence(
            session,
            channel_id=alert.channel_id,
            msg_id=alert.msg_id,
            window=window,
        )
    if evidence is None:
        return None

    if alert.kind is not AlertKind.REPOST_CASCADE:
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
        )

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
    )


async def deliver_once(
    database: Database,
    sender: Sender,
    stats: BotStats,
    *,
    now: datetime | None = None,
) -> None:
    """One pass over the queue: send what is due, hold what is not.

    Three reasons an alert is held rather than sent, and none of them is
    a drop. Quiet hours mean the operator is asleep; the daily cap means
    enough has been said today; and both leave ``delivered_at`` null, so
    the digest picks the alert up and the reader is told how many there
    were.
    """
    moment = now or datetime.now(UTC)

    async with database.session() as session:
        claimed = await claim_undelivered(session, limit=BATCH)

    if not claimed:
        await _maybe_digest(database, sender, stats, now=moment)
        return

    if _is_quiet(moment):
        # Nothing goes out while the operator is asleep. Held, not
        # dropped: `delivered_at` stays null, so the digest collects it.
        logger.debug("%d alert(s) held — quiet hours", len(claimed))
        await _maybe_digest(database, sender, stats, now=moment)
        return

    async with database.session() as session:
        sent_today = await count_delivered_since(
            session,
            since=moment - timedelta(days=1),
            delivery=AlertDelivery.DIRECT,
        )

    for alert in claimed:
        # An alert *raised* overnight belongs to the digest even now that
        # the bot may speak. Otherwise the end of quiet hours would fire
        # the whole night at once, which is the burst the window exists
        # to prevent — and a six-hour-old cascade is a record rather than
        # news, so batching it is also the honest presentation.
        if _is_quiet(alert.raised_at):
            continue
        if sent_today >= settings.alert_daily_cap:
            # Enough has been said today. The rest stay outstanding and
            # reach the reader as a summary that says how many there were.
            logger.debug("daily cap reached; holding the rest for the digest")
            break
        rendered = await _render(database, alert, now=moment)
        if rendered is None:
            logger.warning(
                "alert %d refers to a post that is gone; skipping", alert.id
            )
            continue
        try:
            await sender.send(rendered.text, alert_id=alert.id)
        except Exception as exc:
            # Deliberately everything. A send failing for any reason must
            # leave the alert outstanding rather than take the loop down;
            # the alternative is a bot that dies on one malformed message
            # and stops reporting anything at all.
            logger.warning(
                "could not deliver alert %d", alert.id, exc_info=True
            )
            async with database.session() as session:
                await mark_failed(
                    session, alert.id, error=f"{type(exc).__name__}: {exc}"
                )
            stats.failed += 1
            continue

        async with database.session() as session:
            await mark_delivered(
                session,
                [alert.id],
                delivery=AlertDelivery.DIRECT,
                at=datetime.now(UTC),
            )
        stats.sent += 1
        sent_today += 1

    await _maybe_digest(database, sender, stats, now=moment)


def _is_quiet(moment: datetime) -> bool:
    """Whether the bot may not speak at this moment."""
    return in_quiet_window(
        moment,
        start=settings.alert_quiet_from_hour,
        end=settings.alert_quiet_to_hour,
        zone=settings.watch_timezone,
    )


async def _maybe_digest(
    database: Database, sender: Sender, stats: BotStats, *, now: datetime
) -> None:
    """Send the summary, if one is due and there is anything in it.

    A digest covering nothing is not sent: a daily message saying nothing
    happened trains the reader to ignore the channel, which is the one
    outcome that makes every other guarantee here worthless.
    """
    if not digest_is_due(
        now, hour=settings.alert_digest_hour, last=stats.last_digest_at
    ):
        return

    async with database.session() as session:
        held = await claim_undelivered(session, limit=BATCH * 10)

    if not held:
        stats.last_digest_at = now
        return

    rendered = [
        entry
        for entry in [
            await _render(database, alert, now=now) for alert in held
        ]
        if entry is not None
    ]
    try:
        await sender.send(digest(rendered, held=len(held)))
    except Exception:
        logger.warning("could not deliver the digest", exc_info=True)
        return

    async with database.session() as session:
        await mark_delivered(
            session,
            [alert.id for alert in held],
            delivery=AlertDelivery.DIGEST,
            at=datetime.now(UTC),
        )
    stats.digested += len(held)
    stats.last_digest_at = now


async def run_bot(
    database: Database,
    sender: Sender,
    *,
    stop: asyncio.Event | None = None,
    max_cycles: int | None = None,
) -> BotStats:
    """Deliver alerts until stopped.

    The poll interval is the only thing keeping this correct; a
    notification, when the listener is wired, only shortens the wait.
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
