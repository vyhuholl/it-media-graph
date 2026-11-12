"""The aiogram binding: the one place that touches the Bot API.

Kept small on purpose. Everything worth testing — what a message says,
when it is held, how the queue is claimed, what `/status` reports — lives
in modules that do not import aiogram, so the untested surface here is a
token, the configured chats and a few callbacks.

Three guarantees are enforced at this boundary rather than deeper,
because this is where an outsider can first be seen:

* **Only configured places.** Alerts go to the destinations the settings
  name — a chat, and a topic within it or none — and to no other.
* **Nobody else is answered.** A message from a chat that is not
  configured is dropped before a handler runs. The bot's username is
  discoverable, so strangers will find it, and the inventory is the
  operator's own subscriptions.
* **A chat the bot cannot reach stops it at start,** not at the first
  alert, hours or days later.
"""

import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from itgraph.bot.app import Sender
from itgraph.bot.destinations import Destination, Platform
from itgraph.bot.status import telegram_status, youtube_status
from itgraph.config import settings
from itgraph.db.models import YtAlertRoute
from itgraph.db.session import Database

__all__ = [
    "BotSender",
    "ChatUnreachableError",
    "build_bot",
    "build_dispatcher",
    "check_chats",
]

logger = logging.getLogger(__name__)

# What the feedback buttons sent, under messages from before they were
# removed. Matched only to retire them.
OLD_BUTTON = r"^(useful|boring):\d+$"


def build_bot(token: str) -> Bot:
    """The Bot API client, at the configured address or Telegram's own."""
    url = settings.telegram_bot_api_url
    if url is None:
        return Bot(token=token)
    server = TelegramAPIServer.from_base(str(url).rstrip("/"))
    return Bot(token=token, session=AiohttpSession(api=server))


class BotSender(Sender):
    """Sends through the Bot API, to the destination it is handed."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, text: str, *, destination: Destination, preview: bool = True
    ) -> None:
        # No buttons: feedback was asked of every alert from the first
        # one, pressed once in three months, and removed. The preview
        # through `link_preview_options`, which replaced
        # `disable_web_page_preview` in Bot API 7.0.
        await self._bot.send_message(
            destination.chat_id,
            text,
            message_thread_id=destination.topic_id,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=not preview),
        )


class ChatUnreachableError(RuntimeError):
    """A configured chat the bot cannot deliver to, named by its setting."""

    def __init__(self, setting: str, why: str) -> None:
        super().__init__(f"{setting}: {why}")
        self.setting = setting


async def check_chats(bot: Bot, destinations: Sequence[Destination]) -> None:
    """Ask Telegram about every configured chat before delivering anything.

    A chat the bot is not in, or a chat named with topics that is not a
    forum, would otherwise surface at the first alert, which may be days
    away. Refused by the setting's name,
    which is what an operator changes. Whether a topic exists the Bot API
    cannot say without sending to it.
    """
    chats: dict[int, tuple[str, bool]] = {}
    for destination in destinations:
        setting, wants_topics = chats.get(
            destination.chat_id, (destination.setting, False)
        )
        chats[destination.chat_id] = (
            setting,
            wants_topics or destination.topic_id is not None,
        )

    for chat_id, (setting, wants_topics) in chats.items():
        try:
            chat = await bot.get_chat(chat_id)
        except TelegramAPIError as exc:
            raise ChatUnreachableError(
                setting, f"Telegram does not give the bot this chat ({exc})"
            ) from exc
        if wants_topics and not chat.is_forum:
            raise ChatUnreachableError(
                setting, "topics are configured, and the chat is not a forum"
            )


def build_dispatcher(
    database: Database, destinations: Sequence[Destination]
) -> Dispatcher:
    """The handlers, filtered to the configured chats before any runs."""
    dispatcher = Dispatcher()
    platforms: Mapping[int, Platform] = {
        d.chat_id: d.platform for d in destinations
    }
    telegram = next(
        (d for d in destinations if d.platform is Platform.TELEGRAM), None
    )
    youtube: dict[YtAlertRoute, Destination] = {
        d.route: d
        for d in destinations
        if d.platform is Platform.YOUTUBE and d.route is not None
    }
    chats = frozenset(platforms)

    # Applied as a filter rather than checked inside each handler: a
    # handler added later inherits it instead of having to remember. Any
    # topic of a configured chat counts, General included.
    dispatcher.message.filter(F.chat.id.in_(chats))
    dispatcher.callback_query.filter(F.message.chat.id.in_(chats))

    @dispatcher.message(Command("status"))
    async def status(message: Message) -> None:
        """What the alerting of this chat's platform is doing.

        ``Command`` rather than an exact text match, and the difference
        only shows up in a group. Telegram requires a command there to
        name the bot — ``/status@name`` — and an equality test against
        ``"/status"`` rejects exactly the form a group has to send.

        The other half of working in a group is a BotFather setting
        rather than code: with privacy mode on — the default — a bot is
        only handed commands addressed to it, which is precisely the
        ``/status@name`` form this accepts.

        Answered with ``Message.answer``, which keeps the topic the
        command came from.
        """
        now = datetime.now(UTC)
        async with database.session() as session:
            if platforms[message.chat.id] is Platform.YOUTUBE:
                text = await youtube_status(
                    session, now=now, destinations=youtube
                )
            elif telegram is not None:
                text = await telegram_status(
                    session, now=now, destination=telegram
                )
            else:  # pragma: no cover - a configured chat has a platform
                return
        await message.answer(text)

    @dispatcher.callback_query(F.data.regexp(OLD_BUTTON))
    async def retired_button(query: CallbackQuery) -> None:
        """A press on a button from before feedback was removed.

        Recorded nowhere. Answered, so the client stops showing the press
        as loading, and the message's buttons taken off, which the Bot
        API allows at any age for the bot's own messages.
        """
        await query.answer("отзывы больше не собираются")
        if query.message is None or query.bot is None:
            return
        with suppress(TelegramBadRequest):
            await query.bot.edit_message_reply_markup(
                chat_id=query.message.chat.id,
                message_id=query.message.message_id,
                reply_markup=None,
            )

    return dispatcher
