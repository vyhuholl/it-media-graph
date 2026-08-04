"""The aiogram binding: the one place that touches the Bot API.

Kept small on purpose. Everything worth testing — what a message says,
when it is held, how the queue is claimed — lives in modules that do not
import aiogram, so the untested surface here is a token, a chat id and
two callbacks.

Two guarantees are enforced at this boundary rather than deeper, because
this is where an outsider can first be seen:

* **One recipient.** Alerts go to the configured chat and to no other.
* **Nobody else is answered.** A message from any other chat is dropped
  before a handler runs. The bot's username is discoverable, so strangers
  will find it, and the inventory is the operator's own subscriptions.
"""

import logging
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from itgraph.bot.app import Sender
from itgraph.config import settings
from itgraph.db.alerts import failing_alerts, record_verdict
from itgraph.db.models import Alert, AlertVerdict
from itgraph.db.poll import queue_lag
from itgraph.db.session import Database

__all__ = ["BotSender", "build_dispatcher", "verdict_keyboard"]

logger = logging.getLogger(__name__)

USEFUL = "useful"
BORING = "boring"


def verdict_keyboard(alert_id: int) -> InlineKeyboardMarkup:
    """The two buttons under every alert.

    Two, not three: there is no mute here. Muting manages volume, volume
    arrives with the scoring change, and a suppression rule designed
    against one alert a day would look correct because nothing tested it.

    What these collect cannot be collected retroactively — a threshold
    argued about in three weeks wants the operator's verdict on the
    alerts that fired in the meantime, and those verdicts exist only if
    the buttons were there from the first message.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 по делу", callback_data=f"{USEFUL}:{alert_id}"
                ),
                InlineKeyboardButton(
                    text="👎 мимо", callback_data=f"{BORING}:{alert_id}"
                ),
            ]
        ]
    )


class BotSender(Sender):
    """Sends through the Bot API, to the one configured chat."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def send(self, text: str, *, alert_id: int | None = None) -> None:
        await self._bot.send_message(
            self._chat_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=False,
            reply_markup=(
                verdict_keyboard(alert_id) if alert_id is not None else None
            ),
        )


def build_dispatcher(database: Database, chat_id: int) -> Dispatcher:
    """The handlers, filtered to the one chat before any of them runs."""
    dispatcher = Dispatcher()

    # Applied as a filter rather than checked inside each handler: a
    # handler added later inherits it instead of having to remember.
    dispatcher.message.filter(F.chat.id == chat_id)
    dispatcher.callback_query.filter(F.message.chat.id == chat_id)

    @dispatcher.message(F.text == "/status")
    async def status(message: Message) -> None:
        """What the alerting is doing, so quiet and broken differ.

        This matters more here than anywhere else in the project: a
        healthy alert bot says nothing for days, and without a way to ask,
        "no alerts" and "the pass has not run since Tuesday" look
        identical.
        """
        async with database.session() as session:
            lag = await queue_lag(session, now=datetime.now(UTC))
            stuck = await failing_alerts(
                session, attempts=settings.alert_failure_report_after
            )
            raised, outstanding, newest = (
                await session.execute(
                    select(
                        func.count(Alert.id),
                        func.count(Alert.id).filter(
                            Alert.delivered_at.is_(None)
                        ),
                        func.max(Alert.raised_at),
                    )
                )
            ).one()

        lines = [
            f"оповещений всего: {raised}, не доставлено: {outstanding}",
            (
                f"последнее поднято: {newest:%Y-%m-%d %H:%M} UTC"
                if newest
                else "ни одного оповещения ещё не было"
            ),
            (
                f"каналов в очереди опроса: {lag.tracked}, "
                f"просрочено: {lag.overdue}"
            ),
        ]
        if stuck:
            lines.append(f"⚠️ застряло при отправке: {stuck}")
        await message.answer("\n".join(lines))

    @dispatcher.callback_query(F.data.regexp(rf"^({USEFUL}|{BORING}):\d+$"))
    async def verdict(query: CallbackQuery) -> None:
        assert query.data is not None
        kind, _, raw = query.data.partition(":")
        async with database.session() as session:
            await record_verdict(
                session,
                alert_id=int(raw),
                verdict=(
                    AlertVerdict.USEFUL
                    if kind == USEFUL
                    else AlertVerdict.BORING
                ),
                at=datetime.now(UTC),
            )
        await query.answer("записано")

    return dispatcher
