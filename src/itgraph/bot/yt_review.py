"""Reviewing a rising YouTube offer in the bot: the card, its buttons,
a press.

Telegram's review with YouTube's own values — the five kinds, the seven
reasons, and a language, which a seed needs and the operator chose to
decide by hand. Pure, so the wording and the choices are testable
without aiogram: `bot/handlers.py` turns the keyboards here into
Telegram's and the data a button carries back into a press.

A card links the channel — by its handle when a stored payload carries
one, by its id otherwise — and forces the preview to that link, which
Telegram renders with the channel's own page: the recognition a
day-one card needs, before any payload exists to give a title. A chart
offer's card links the video that charted the channel too. Everything
from a channel or a video is escaped.

A press is `yv:<offer id>:<press>` — at most 64 bytes, as the Bot API
requires. Seed opens the kinds, and then — only while the offer's
language is unknown or neither `en` nor `ru` — the languages, which
carry the chosen kind back to the handler, nothing else remembering it;
reject opens the reasons; maybe is one press. A decision is what
`itgraph yt mark` would record with the same choices. Every label is
one to three words with a leading emoji and no trailing punctuation, as
`CLAUDE.md` asks of a button.
"""

import enum
import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from itgraph.bot.render import MONTHS, families_phrase
from itgraph.db.models import (
    ChannelStatus,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)

__all__ = [
    "BACK",
    "KIND_LABELS",
    "LANGUAGE_LABELS",
    "MAYBE",
    "REASON_LABELS",
    "REJECT",
    "SEED",
    "ChannelView",
    "ChartCardView",
    "Decision",
    "Keyboard",
    "LinkCardView",
    "Press",
    "PressKind",
    "channel_link",
    "decision_line",
    "first_step",
    "kinds",
    "languages",
    "parse",
    "read_channel",
    "reasons",
    "render_chart_card",
    "render_link_card",
    "video_link",
]

PREFIX = "yv"
# What the Bot API allows in a button's data.
DATA_LIMIT = 64

SEED = "🌱 В сиды"
MAYBE = "🤔 Может быть"
REJECT = "🚫 Отклонить"
BACK = "↩️ Назад"

KIND_LABELS: dict[YtChannelKind, str] = {
    YtChannelKind.PERSONAL: "👤 Личный",
    YtChannelKind.COMPANY: "🏢 Компания",
    YtChannelKind.MEDIA: "📺 Медиа",
    YtChannelKind.EVENT: "🎤 Событие",
    YtChannelKind.EDUCATION: "🎓 Обучение",
}

REASON_LABELS: dict[YtRejectReason, str] = {
    YtRejectReason.NOT_IT: "🙅 Не про IT",
    YtRejectReason.ADJACENT: "↔️ Смежное",
    YtRejectReason.CRYPTO: "🪙 Крипта",
    YtRejectReason.INFOBIZ: "💸 Инфобиз",
    YtRejectReason.ADS: "📢 Реклама",
    YtRejectReason.CONTENT_FARM: "🏭 Контент-ферма",
    YtRejectReason.CONSUMER_TECH: "📱 Гаджеты",
}

LANGUAGE_LABELS: dict[YtLang, str] = {
    YtLang.EN: "🇬🇧 EN",
    YtLang.RU: "🇷🇺 RU",
    YtLang.OTHER: "🌐 Другой",
}

# A button: its label, and the data it carries back.
type Button = tuple[str, str]
type Keyboard = tuple[tuple[Button, ...], ...]


class PressKind(enum.StrEnum):
    """What a button asks for."""

    SEED = "seed"
    REJECT = "reject"
    BACK = "back"
    MAYBE = "maybe"
    KIND = "kind"
    REASON = "reason"
    LANG = "lang"


@dataclass(frozen=True, slots=True)
class Decision:
    """A review, as `itgraph yt mark` would record it."""

    status: ChannelStatus
    kind: YtChannelKind | None = None
    language: YtLang | None = None
    reason: YtRejectReason | None = None


@dataclass(frozen=True, slots=True)
class Press:
    """One press: on which offer, and what it asks."""

    offer_id: int
    kind: PressKind
    value: (
        YtChannelKind | YtRejectReason | tuple[YtChannelKind, YtLang] | None
    ) = None

    def decision(
        self, known_language: YtLang | None = None
    ) -> Decision | None:
        """What it decides; ``None`` for a press that only redraws.

        A kind press decides only when the offer's language is known —
        a payload's ``defaultLanguage`` of ``en`` or ``ru`` fills it
        silently — and otherwise only opens the languages.
        """
        if self.kind is PressKind.MAYBE:
            return Decision(ChannelStatus.MAYBE)
        if (
            self.kind is PressKind.KIND
            and isinstance(self.value, YtChannelKind)
            and known_language is not None
        ):
            return Decision(
                ChannelStatus.SEED, kind=self.value, language=known_language
            )
        if self.kind is PressKind.REASON and isinstance(
            self.value, YtRejectReason
        ):
            return Decision(ChannelStatus.REJECTED, reason=self.value)
        if self.kind is PressKind.LANG and isinstance(self.value, tuple):
            kind, language = self.value
            return Decision(ChannelStatus.SEED, kind=kind, language=language)
        return None


def _data(offer_id: int, kind: PressKind, *values: str) -> str:
    return ":".join((PREFIX, str(offer_id), kind.value, *values))


def parse(data: str | None) -> Press | None:
    """A button's data as a press, or ``None`` for anything else."""
    if not data or len(data.encode()) > DATA_LIMIT:
        return None
    parts = data.split(":")
    if len(parts) not in (3, 4, 5) or parts[0] != PREFIX:
        return None
    if not parts[1].isdigit():
        return None
    try:
        kind = PressKind(parts[2])
    except ValueError:
        return None
    offer_id = int(parts[1])
    plain = kind in (
        PressKind.SEED,
        PressKind.REJECT,
        PressKind.BACK,
        PressKind.MAYBE,
    )
    if plain != (len(parts) == 3):
        return None
    try:
        if plain:
            return Press(offer_id, kind)
        if kind is PressKind.KIND:
            if len(parts) != 4:
                return None
            return Press(offer_id, kind, YtChannelKind(parts[3]))
        if kind is PressKind.REASON:
            if len(parts) != 4:
                return None
            return Press(offer_id, kind, YtRejectReason(parts[3]))
        if len(parts) != 5:
            return None
        return Press(
            offer_id,
            kind,
            (YtChannelKind(parts[3]), YtLang(parts[4])),
        )
    except ValueError:
        return None


def first_step(offer_id: int) -> Keyboard:
    """The three buttons a card carries."""
    return (
        (
            (SEED, _data(offer_id, PressKind.SEED)),
            (MAYBE, _data(offer_id, PressKind.MAYBE)),
            (REJECT, _data(offer_id, PressKind.REJECT)),
        ),
    )


def _second_step(
    offer_id: int, kind: PressKind, buttons: Sequence[Button]
) -> Keyboard:
    """Two to a row, and back on a row of its own."""
    rows = [tuple(buttons[i : i + 2]) for i in range(0, len(buttons), 2)]
    rows.append(((BACK, _data(offer_id, PressKind.BACK)),))
    return tuple(rows)


def kinds(offer_id: int) -> Keyboard:
    """The kinds a seed is marked with."""
    return _second_step(
        offer_id,
        PressKind.KIND,
        [
            (label, _data(offer_id, PressKind.KIND, kind.value))
            for kind, label in KIND_LABELS.items()
        ],
    )


def reasons(offer_id: int) -> Keyboard:
    """The reasons a rejection is marked with."""
    return _second_step(
        offer_id,
        PressKind.REASON,
        [
            (label, _data(offer_id, PressKind.REASON, reason.value))
            for reason, label in REASON_LABELS.items()
        ],
    )


def languages(offer_id: int, kind: YtChannelKind) -> Keyboard:
    """The languages a seed of an unknown language is marked with.

    Each carries the chosen kind, so that nothing has to remember the
    press before: a callback's data is the only state a button has.
    """
    row = tuple(
        (label, _data(offer_id, PressKind.LANG, kind.value, language.value))
        for language, label in LANGUAGE_LABELS.items()
    )
    return (row, ((BACK, _data(offer_id, PressKind.BACK)),))


def _name(label: str) -> str:
    """A label without its emoji, as a sentence carries it."""
    words = label.split(" ", 1)[1]
    return words[0].lower() + words[1:]


def _language_name(language: YtLang) -> str:
    """A language as the decision line carries it: its own word, not
    lowered — «EN» reads as a tag, «Другой» as a word."""
    return LANGUAGE_LABELS[language].split(" ", 1)[1]


def decision_line(decision: Decision) -> str:
    """The line a card gains once decided."""
    if (
        decision.status is ChannelStatus.SEED
        and decision.kind is not None
        and decision.language is not None
    ):
        return (
            f"✅ В сиды · {_name(KIND_LABELS[decision.kind])}"
            f" · {_language_name(decision.language)}"
        )
    if (
        decision.status is ChannelStatus.REJECTED
        and decision.reason is not None
    ):
        return f"🚫 Отклонён · {_name(REASON_LABELS[decision.reason])}"
    return MAYBE


# --- the card ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelView:
    """What a stored payload says about a channel, if one is stored.

    A day-one card has none of it: the record was created without data,
    and the refresh fetches the payload at its next run.
    """

    handle: str | None = None
    title: str | None = None
    # The payload's `defaultLanguage`, as YouTube spelled it.
    language: str | None = None


def read_channel(payload: Mapping[str, Any] | None) -> ChannelView:
    """A channel's stored payload as the card reads it."""
    if not isinstance(payload, Mapping):
        return ChannelView()
    snippet = payload.get("snippet")
    if not isinstance(snippet, Mapping):
        return ChannelView()
    handle = snippet.get("customUrl")
    return ChannelView(
        handle=handle.removeprefix("@") if isinstance(handle, str) else None,
        title=(
            snippet.get("title")
            if isinstance(snippet.get("title"), str)
            else None
        ),
        language=(
            snippet.get("defaultLanguage")
            if isinstance(snippet.get("defaultLanguage"), str)
            else None
        ),
    )


def channel_link(view: ChannelView, channel_id: str) -> str:
    """The channel's own link: by its handle when a stored payload
    carries one, else by its id. What the card previews."""
    if view.handle:
        return f"https://www.youtube.com/@{view.handle}"
    return f"https://www.youtube.com/channel/{channel_id}"


def video_link(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _named(view: ChannelView, channel_id: str) -> str:
    """The channel's link with its title as the text, or its id when no
    payload holds a title."""
    name = _escape(view.title) if view.title else _escape(channel_id)
    url = html.escape(channel_link(view, channel_id))
    return f'<a href="{url}">{name}</a>'


def _first_in(moment: datetime | None, zone: str) -> str:
    if moment is None:
        return ""
    local = moment.astimezone(ZoneInfo(zone))
    return f" · впервые в окне {local.day} {MONTHS[local.month - 1]}"


@dataclass(frozen=True, slots=True)
class LinkCardView:
    """What a link offer's card shows, read at sending."""

    channel_id: str
    channel: ChannelView
    families: int
    families_before: int
    first_in_window: datetime | None


@dataclass(frozen=True, slots=True)
class ChartCardView:
    """What a chart offer's card shows, read at sending."""

    channel_id: str
    channel: ChannelView
    region: str
    position: int
    # The video that charted the channel, if the day's reading still
    # holds it.
    video_id: str | None


def render_link_card(
    view: LinkCardView, *, window_days: int, zone: str
) -> str:
    """A link offer's card, in Telegram's HTML."""
    before = (
        f"{view.families_before} раньше"
        if view.families_before
        else "раньше ни одной"
    )
    evidence = (
        f"{families_phrase(view.families)} за {window_days} дней, {before}"
        f"{_first_in(view.first_in_window, zone)}"
    )
    lines = [
        f"📈 Растёт: {_named(view.channel, view.channel_id)}",
        evidence,
    ]
    # The language is said only when it is neither of the two a topic
    # takes: those the topic itself says.
    if view.channel.language not in ("en", "ru"):
        lines.append(f"Язык: {view.channel.language or 'не определён'}")
    return "\n".join(lines)


def render_chart_card(view: ChartCardView) -> str:
    """A chart offer's card, in Telegram's HTML."""
    head = (
        f"📈 Чарт {_escape(view.region)}, позиция {view.position}: "
        f"{_named(view.channel, view.channel_id)}"
    )
    lines = [head]
    if view.video_id is not None:
        url = html.escape(video_link(view.video_id))
        lines.append(f'🎬 <a href="{url}">{_escape(view.video_id)}</a>')
    return "\n".join(lines)
