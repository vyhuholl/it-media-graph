"""What `/status` says, per platform: the alerting of the chat it is asked in.

This matters more here than anywhere else in the project: a healthy alert
bot says nothing for days, and without a way to ask, "no alerts" and "the
pass has not run since Tuesday" look identical.

Each chat is told about its own platform. The Telegram chat about the
detection of Telegram's posts; the YouTube chat about YouTube's queue and
its poll loop — and, while YouTube has not accepted the derived-metrics
use case, that nothing is scored there, so that silence in that chat
reads as what it is.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph import yt_policy
from itgraph.bot.destinations import Destination
from itgraph.bot.render import age_phrase
from itgraph.config import settings
from itgraph.db import yt_alerts
from itgraph.db.alerts import failing_alerts, outstanding_alerts
from itgraph.db.models import Alert, YtAlertRoute, YtPassOutcome, YtWatchPass
from itgraph.db.poll import queue_lag

__all__ = ["telegram_status", "youtube_status"]


def _clock(moment: datetime, zone: str) -> str:
    return f"{moment.astimezone(ZoneInfo(zone)):%Y-%m-%d %H:%M %Z}"


def _quiet_line(destination: Destination, now: datetime) -> str | None:
    """Quiet hours are the benign reason a queue is not moving, and saying
    so is what stops "waiting" from reading as "broken"."""
    release = destination.quiet_until(now)
    if release is None:
        return None
    return f"🌙 тихие часы до {release:%H:%M} — доставка приостановлена"


async def telegram_status(
    session: AsyncSession, *, now: datetime, destination: Destination
) -> str:
    """Telegram's alerting: what was raised, what waits, why."""
    lag = await queue_lag(session, now=now)
    stuck = await failing_alerts(
        session, attempts=settings.alert_failure_report_after
    )
    waiting = await outstanding_alerts(session)
    raised, folded, newest = (
        await session.execute(
            select(
                func.count(Alert.id),
                func.count(Alert.id).filter(Alert.folded_into.is_not(None)),
                func.max(Alert.raised_at),
            )
        )
    ).one()

    # Folds are counted apart because they are alerts nobody was sent on
    # their own: without the figure, "raised" overstates how many messages
    # there were, and the difference reads as a loss.
    lines = [
        (
            f"оповещений всего: {raised} (из них свёрнуто в серии: "
            f"{folded}), не доставлено: {waiting.total}"
        ),
        (
            f"последнее поднято: {_clock(newest, destination.zone)}"
            if newest
            else "ни одного оповещения ещё не было"
        ),
        f"каналов в очереди опроса: {lag.tracked}, просрочено: {lag.overdue}",
    ]

    quiet = _quiet_line(destination, now)
    if quiet:
        lines.append(quiet)

    waited = waiting.oldest_wait(now)
    if waiting.total:
        line = f"ждут отправки: {waiting.total}"
        if waiting.held:
            line += f", из них до дайджеста: {waiting.held}"
        if waited is not None:
            line += f", самое старое {age_phrase(waited)}"
        if waiting.never_attempted:
            line += f", попыток не было у {waiting.never_attempted}"
        lines.append(line)

    # Nothing has tried, nothing is holding them back, and they have been
    # there far longer than a poll interval: that is a delivery loop which
    # is not running, and it is invisible in every other signal — the
    # service is `active` and this command answers. Held alerts wait for
    # the digest by design, so they are not counted against it.
    unheld = waiting.total - waiting.held
    unheld_since = waiting.oldest_unheld_raised_at
    if (
        quiet is None
        and unheld
        and waiting.never_attempted == unheld
        and unheld_since is not None
        and now - unheld_since
        > timedelta(seconds=settings.alert_poll_seconds * 10)
    ):
        lines.append(
            "⚠️ ни одной попытки отправки — похоже, доставка не работает"
        )

    if stuck:
        lines.append(f"⚠️ застряло при отправке: {stuck}")
    return "\n".join(lines)


async def youtube_status(
    session: AsyncSession,
    *,
    now: datetime,
    destinations: Mapping[YtAlertRoute, Destination],
) -> str:
    """YouTube's alerting: the queue per topic, and whether the loop runs."""
    totals, waiting = await yt_alerts.route_counts(
        session, failing_after=settings.alert_failure_report_after
    )
    last_pass = await session.scalar(
        select(func.max(YtWatchPass.finished_at)).where(
            YtWatchPass.outcome == YtPassOutcome.COMPLETE
        )
    )
    zone = next(iter(destinations.values())).zone if destinations else "UTC"

    lines = []
    if yt_policy.DERIVED_METRICS_ACCEPTED_ON is None:
        lines.append(
            "оценок YouTube нет до одобрения: оповещениям неоткуда взяться"
        )
    lines.append(
        f"оповещений YouTube всего: {totals['raised']} (свёрнуто в серии: "
        f"{totals['folded']}, исключено по языку: {totals['excluded']})"
    )
    for route, counts in waiting.items():
        line = f"{route.value.upper()}: не доставлено {counts.undelivered}"
        if counts.held:
            line += f", из них до дайджеста {counts.held}"
        if counts.failing:
            line += f", ⚠️ застряло при отправке {counts.failing}"
        lines.append(line)
    lines.append(
        f"последний полный проход опроса YouTube: {_clock(last_pass, zone)}"
        if last_pass
        else "полного прохода опроса YouTube ещё не было"
    )
    for destination in destinations.values():
        quiet = _quiet_line(destination, now)
        if quiet:
            lines.append(quiet)
            break
    return "\n".join(lines)
