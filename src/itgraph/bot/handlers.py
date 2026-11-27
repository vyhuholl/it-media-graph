"""The aiogram binding: the one place that touches the Bot API.

Kept small on purpose. Everything worth testing — what a message says,
when it is held, how the queue is claimed, what `/status` reports — lives
in modules that do not import aiogram, so the untested surface here is a
token, the configured chats and a few callbacks.

Three guarantees are enforced at this boundary rather than deeper,
because this is where an outsider can first be seen:

* **Only configured places.** Alerts go to the destinations the settings
  name — topics of the one chat — and to no other.
* **Nobody else is answered.** A message from a chat that is not
  configured is dropped before a handler runs. The bot's username is
  discoverable, so strangers will find it, and the inventory is the
  operator's own subscriptions.
* **A chat the bot cannot reach stops it at start,** not at the first
  alert, hours or days later.

A press on a rising offer's card is taken in the one chat alone — both
platforms' buttons live there, and a card's `rv:` or `yv:` data answers
only its own handler — and what a press records names nobody: the
handlers read the button's data and the message, never who pressed.
"""

import logging
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from itgraph.bot.app import Sender
from itgraph.bot.destinations import Destination, Platform
from itgraph.bot.review import (
    Decision,
    Keyboard,
    Press,
    PressKind,
    decision_line,
    first_step,
    kinds,
    parse,
    reasons,
)
from itgraph.bot.status import telegram_status, youtube_status
from itgraph.bot.yt_review import (
    Decision as YtDecision,
)
from itgraph.bot.yt_review import (
    Press as YtPress,
)
from itgraph.bot.yt_review import (
    PressKind as YtPressKind,
)
from itgraph.bot.yt_review import (
    decision_line as yt_decision_line,
)
from itgraph.bot.yt_review import (
    first_step as yt_first_step,
)
from itgraph.bot.yt_review import (
    kinds as yt_kinds,
)
from itgraph.bot.yt_review import (
    languages as yt_languages,
)
from itgraph.bot.yt_review import (
    parse as yt_parse,
)
from itgraph.bot.yt_review import (
    reasons as yt_reasons,
)
from itgraph.config import settings
from itgraph.db.models import YtAlertRoute, YtChannelKind, YtLang
from itgraph.db.offers import offer_state, record_decision
from itgraph.db.session import Database
from itgraph.db.yt_offers import (
    offer_state as yt_offer_state,
)
from itgraph.db.yt_offers import (
    record_decision as yt_record_decision,
)

__all__ = [
    "BotSender",
    "ChatUnreachableError",
    "build_bot",
    "build_dispatcher",
    "check_chats",
]

logger = logging.getLogger(__name__)

# What a rising offer's buttons send; `bot/review.py` and
# `bot/yt_review.py` read the rest.
REVIEW_BUTTON = r"^rv:"
YT_REVIEW_BUTTON = r"^yv:"

# What a press is answered with when it decides nothing.
DECIDED = "уже размечено"
UNKNOWN = "нет такого предложения"


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
        self,
        text: str,
        *,
        destination: Destination,
        preview: bool = True,
        preview_url: str | None = None,
        keyboard: Keyboard | None = None,
    ) -> None:
        # No buttons under an alert: feedback was asked of every alert
        # from the first one, pressed once in three months, and removed.
        # A rising offer's card carries the buttons that review it. The
        # preview through `link_preview_options`, which replaced
        # `disable_web_page_preview` in Bot API 7.0.
        await self._bot.send_message(
            destination.chat_id,
            text,
            message_thread_id=destination.topic_id,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                is_disabled=not preview, url=preview_url
            ),
            reply_markup=markup(keyboard) if keyboard is not None else None,
        )


def markup(keyboard: Keyboard) -> InlineKeyboardMarkup:
    """A keyboard of `bot/review.py`, as Telegram takes it."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=data)
                for label, data in row
            ]
            for row in keyboard
        ]
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
    telegram = next(
        (d for d in destinations if d.platform is Platform.TELEGRAM), None
    )
    youtube: dict[YtAlertRoute, Destination] = {
        d.route: d
        for d in destinations
        if d.platform is Platform.YOUTUBE and d.route is not None
    }
    # The YouTube chat the `yv:` buttons are taken in; both topics are
    # the one chat.
    youtube_chat = next(
        (d.chat_id for d in destinations if d.platform is Platform.YOUTUBE),
        None,
    )

    # Applied as a filter rather than checked inside each handler: a
    # handler added later inherits it instead of having to remember. Any
    # topic of a configured chat counts, General included.
    dispatcher.message.filter(F.chat.id.in_({d.chat_id for d in destinations}))
    dispatcher.callback_query.filter(
        F.message.chat.id.in_({d.chat_id for d in destinations})
    )

    @dispatcher.message(Command("status"))
    async def status(message: Message) -> None:
        """What the alerting of both platforms is doing, in one answer.

        The one chat no longer implies a platform, so the answer covers
        both — Telegram's state, then YouTube's — whatever topic it was
        asked in, which is also the common question in a one-chat world.
        Routing by the topic asked in was rejected: it makes General
        special and buries the cross-platform answer.

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
        parts: list[str] = []
        async with database.session() as session:
            if telegram is not None:
                parts.append(
                    await telegram_status(
                        session, now=now, destination=telegram
                    )
                )
            if youtube:
                parts.append(
                    await youtube_status(
                        session, now=now, destinations=youtube
                    )
                )
        await message.answer("\n\n".join(parts))

    @dispatcher.callback_query(F.data.regexp(REVIEW_BUTTON))
    async def review(query: CallbackQuery) -> None:
        """A press on a rising offer's card, in Telegram's chat alone.

        Seed and reject show their second step and back the first, writing
        nothing; a deciding press records the decision once, and the card
        then says it and loses its buttons. Who pressed is never read.
        """
        message = query.message
        press = parse(query.data)
        bot = query.bot
        if (
            press is None
            or bot is None
            or telegram is None
            or not isinstance(message, Message)
            or message.chat.id != telegram.chat_id
        ):
            await query.answer()
            return

        decision = press.decision
        async with database.session() as session:
            state = await offer_state(session, press.offer_id)
            recorded = (
                state is not None
                and state.sent
                and state.decision is None
                and decision is not None
                and await record_decision(
                    session,
                    press.offer_id,
                    status=decision.status,
                    kind=decision.kind,
                    reason=decision.reason,
                )
            )
            if decision is not None and not recorded:
                state = await offer_state(session, press.offer_id)

        if state is None or not state.sent:
            await query.answer(UNKNOWN)
            return
        if recorded and decision is not None:
            await query.answer()
            await _show_decision(bot, message, decision_line(decision))
            return
        if state.decision is not None:
            await query.answer(DECIDED)
            await _show_decision(
                bot,
                message,
                decision_line(
                    Decision(
                        state.decision, kind=state.kind, reason=state.reason
                    )
                ),
            )
            return
        await query.answer()
        with suppress(TelegramBadRequest):
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup(_step(press)),
            )

    @dispatcher.callback_query(F.data.regexp(YT_REVIEW_BUTTON))
    async def yt_review(query: CallbackQuery) -> None:
        """A press on a YouTube rising offer's card, in the YouTube chat
        alone.

        Seed opens the kinds and — only while the offer's language is
        unknown or neither of the two a topic takes — the languages,
        which carry the chosen kind; a payload's `defaultLanguage` of
        `en` or `ru` fills the language silently. Reject opens the
        reasons; maybe is one press. A deciding press records the
        decision once, and the card then says it and loses its buttons.
        Who pressed is never read.
        """
        message = query.message
        press = yt_parse(query.data)
        bot = query.bot
        if (
            press is None
            or bot is None
            or youtube_chat is None
            or not isinstance(message, Message)
            or message.chat.id != youtube_chat
        ):
            await query.answer()
            return

        decision: YtDecision | None = None
        async with database.session() as session:
            state = await yt_offer_state(session, press.offer_id)
            recorded = False
            if state is not None and state.sent and state.decision is None:
                decision = press.decision(state.known_language)
                if decision is not None:
                    recorded = await yt_record_decision(
                        session,
                        press.offer_id,
                        status=decision.status,
                        kind=decision.kind,
                        language=decision.language,
                        reason=decision.reason,
                    )
            if decision is not None and not recorded:
                state = await yt_offer_state(session, press.offer_id)

        if state is None or not state.sent:
            await query.answer(UNKNOWN)
            return
        if recorded and decision is not None:
            await query.answer()
            await _show_decision(bot, message, yt_decision_line(decision))
            return
        if state.decision is not None:
            await query.answer(DECIDED)
            await _show_decision(
                bot,
                message,
                yt_decision_line(
                    YtDecision(
                        state.decision,
                        kind=state.kind,
                        language=state.language,
                        reason=state.reason,
                    )
                ),
            )
            return
        await query.answer()
        with suppress(TelegramBadRequest):
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup(_yt_step(press, state.known_language)),
            )

    return dispatcher


def _step(press: Press) -> Keyboard:
    """The buttons a press that decides nothing shows."""
    if press.kind is PressKind.SEED:
        return kinds(press.offer_id)
    if press.kind is PressKind.REJECT:
        return reasons(press.offer_id)
    return first_step(press.offer_id)


def _yt_step(press: YtPress, known_language: YtLang | None) -> Keyboard:
    """The buttons a YouTube press that decides nothing shows."""
    if press.kind is YtPressKind.SEED:
        return yt_kinds(press.offer_id)
    if press.kind is YtPressKind.REJECT:
        return yt_reasons(press.offer_id)
    # The offer's language is unknown or other than the two a topic
    # takes: ask it, carrying the kind just chosen.
    if (
        press.kind is YtPressKind.KIND
        and known_language is None
        and isinstance(press.value, YtChannelKind)
    ):
        return yt_languages(press.offer_id, press.value)
    return yt_first_step(press.offer_id)


async def _show_decision(bot: Bot, message: Message, line: str) -> None:
    """The card with its decision said, and no buttons left.

    Its text as Telegram holds it, so the card is not read again; a card
    that already says it — the other of two presses — only loses its
    buttons.
    """
    text = message.html_text
    with suppress(TelegramBadRequest):
        if text.endswith(line):
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=None,
            )
            return
        await bot.edit_message_text(
            text=f"{text}\n\n{line}",
            chat_id=message.chat.id,
            message_id=message.message_id,
            parse_mode="HTML",
            link_preview_options=message.link_preview_options,
            reply_markup=None,
        )
