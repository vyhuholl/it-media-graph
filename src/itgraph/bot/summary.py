"""The day summary: the day each destination closes, in one message.

As each destination's quiet window begins, the bot sends it a recap of
every alert it was sent directly in the day that has just ended — the
window's complement. It rides the bot's own poll, as the digest does:
no job of its own, and at a 60 s poll the summary leaves within a
minute of the window's start. A bot that was down at the start sends
the day's summary at its next pass, however late in the day.

Which day a destination has been sent is a row (``bot_daily_summaries``,
through `db/summaries.py`), not memory: the summary's input — delivered
alerts — never empties, so memory would resend yesterday on every
restart. A day with nothing directly delivered closes in silence and
leaves no row; a summary interrupted part-way leaves its row open and
is resent whole, at-least-once, like every send here.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from itgraph.bot.destinations import Destination
from itgraph.bot.queues import Queue
from itgraph.bot.render import summary_parts
from itgraph.db.session import Database
from itgraph.db.summaries import close_summary, is_sent, open_summary

__all__ = ["DayWindow", "day_window", "maybe_summary"]

logger = logging.getLogger(__name__)


class SummarySender(Protocol):
    """What sending a summary needs of a sender, and no more.

    The delivery loop's ``Sender`` satisfies it; a protocol rather than
    that import keeps ``bot/app.py`` and this module from importing one
    another — the pass is called *by* the loop, not beside it.
    """

    async def send(
        self,
        text: str,
        *,
        destination: Destination,
        preview: bool = True,
    ) -> None: ...


class SummaryStats(Protocol):
    """What the pass counts, on the loop's ``BotStats``."""

    summaries: int


@dataclass(frozen=True, slots=True)
class DayWindow:
    """The day that has just ended for one destination.

    ``label`` is the date the day is named by — «Сводка за 18.11.2026»
    — and ``end`` is the moment its summary became due: the next
    ``quiet_from``, which is the quiet window's start.
    """

    label: date
    start: datetime
    end: datetime


def day_window(
    *, quiet_from: int, quiet_to: int, zone: str, now: datetime
) -> DayWindow | None:
    """The most recent day whose end has passed, or ``None`` with no
    quiet window.

    Day *D* is ``[D quiet_to, next quiet_from)`` in the destination's
    zone. A window inside one day (02:00–07:00) makes that the 19th's
    start and the 20th's end; a window crossing midnight (22:00–06:00)
    makes both the same date, its day running 06:00 to 22:00. Equal
    bounds are quiet hours switched off, which define no day and send
    no summary.

    The window is the *bot's* — the one whose held alerts the digest
    counts — and not the collector's, which merely coincide with it
    today.
    """
    if quiet_from == quiet_to:
        return None

    tz = ZoneInfo(zone)
    local = now.astimezone(tz)
    # The day ends at a quiet_from; the latest one at or before now is
    # the end of the day that has just ended. Recombined on the previous
    # date rather than subtracted, so a zone's short or long day stays
    # the zone's to place.
    end_date = local.date()
    end = datetime.combine(end_date, time(quiet_from), tzinfo=tz)
    if end > local:
        end = datetime.combine(
            end_date - timedelta(days=1), time(quiet_from), tzinfo=tz
        )

    # A window crossing midnight ends its day on the date it started;
    # one inside a day ends it on the next date.
    if quiet_from > quiet_to:
        label = end.date()
    else:
        label = end.date() - timedelta(days=1)
    start = datetime.combine(label, time(quiet_to), tzinfo=tz)
    return DayWindow(label=label, start=start, end=end)


async def maybe_summary(
    database: Database,
    queue: Queue[Any],
    destination: Destination,
    sender: SummarySender,
    stats: SummaryStats,
    *,
    now: datetime,
) -> None:
    """Close the destination's day, if it is due and anything was sent.

    Due is the marker's to say, not the clock's: a day whose summary
    went out is done whenever the bot looks, and a bot that starts again
    at 14:00 sends the day's summary then if it never went. Counted
    nowhere — neither the cap nor the digest hears of it — and held by
    no quiet window: it is the one message the window's start itself
    sends.
    """
    window = day_window(
        quiet_from=destination.quiet_from,
        quiet_to=destination.quiet_to,
        zone=destination.zone,
        now=now,
    )
    if window is None:
        return

    async with database.session() as session:
        if await is_sent(session, destination.key, window.label):
            return
        entries = await queue.summary_entries(
            database,
            destination,
            start=window.start,
            end=window.end,
        )
        if not entries:
            # A quiet day closes in silence, and leaves no marker: its
            # summary cannot be resent, because there is none of it.
            return
        await open_summary(session, destination.key, window.label)

    parts = summary_parts([entry.text for entry in entries], day=window.label)
    for number, part in enumerate(parts):
        try:
            await sender.send(
                part.text, destination=destination, preview=False
            )
        except Exception:
            # Everything, as for an alert: a summary that cannot be sent
            # waits — its marker stays open — and the next pass resends
            # the whole day rather than finish half of one.
            logger.warning(
                "could not deliver part %d of %d of %s's summary for %s",
                number + 1,
                len(parts),
                destination.key,
                window.label,
                exc_info=True,
            )
            return

    async with database.session() as session:
        await close_summary(
            session, destination.key, window.label, at=datetime.now(UTC)
        )
    stats.summaries += 1
