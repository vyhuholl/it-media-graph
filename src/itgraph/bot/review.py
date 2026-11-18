"""Reviewing a rising offer in the bot: the card, its buttons, a press.

Pure, so the wording and the choices are testable without aiogram:
`bot/handlers.py` turns the keyboards here into Telegram's and the data a
button carries back into a press.

A card names the channel by its username with a link, its title, the
families that referred to it in the window and before it, the first
reference in the window, and up to three seed posts that referred to it.
Everything from a channel or a post is escaped.

A press is `rv:<offer id>:<press>` — at most 64 bytes, as the Bot API
requires. Seed and reject open a second step, the seven kinds or the
seven reasons `itgraph mark` takes, with back to the three; maybe is one
press. A decision is what `itgraph mark` would record with the same
choices. Every label is one to three words with a leading emoji and no
trailing punctuation, as `CLAUDE.md` asks of a button.
"""

import enum
import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from itgraph.bot.render import MONTHS, families_phrase
from itgraph.db.models import ChannelKind, ChannelStatus, RejectReason
from itgraph.db.offers import SeedPost

__all__ = [
    "BACK",
    "KIND_LABELS",
    "MAYBE",
    "REASON_LABELS",
    "REJECT",
    "SEED",
    "CardView",
    "Decision",
    "Keyboard",
    "Press",
    "PressKind",
    "SeedPost",
    "decision_line",
    "first_step",
    "kinds",
    "parse",
    "reasons",
    "render_card",
]

PREFIX = "rv"
# What the Bot API allows in a button's data.
DATA_LIMIT = 64

SEED = "🌱 В сиды"
MAYBE = "🤔 Может быть"
REJECT = "🚫 Отклонить"
BACK = "↩️ Назад"

KIND_LABELS: dict[ChannelKind, str] = {
    ChannelKind.PERSONAL: "👤 Личный",
    ChannelKind.AGGREGATOR: "📰 Агрегатор",
    ChannelKind.COMPANY: "🏢 Компания",
    ChannelKind.VACANCIES: "💼 Вакансии",
    ChannelKind.MEDIA: "📺 Медиа",
    ChannelKind.COMMUNITY: "👥 Сообщество",
    ChannelKind.EVENT: "📅 Событие",
}

REASON_LABELS: dict[RejectReason, str] = {
    RejectReason.NOT_IT: "🙅 Не про IT",
    RejectReason.ADJACENT: "↔️ Смежное",
    RejectReason.CRYPTO: "🪙 Крипта",
    RejectReason.INFOBIZ: "💸 Инфобиз",
    RejectReason.ADS: "📢 Реклама",
    RejectReason.CONTENT_FARM: "🏭 Контент-ферма",
    RejectReason.OTHER_SCENE: "🌐 Другая тусовка",
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


@dataclass(frozen=True, slots=True)
class Decision:
    """A review, as `itgraph mark` would record it."""

    status: ChannelStatus
    kind: ChannelKind | None = None
    reason: RejectReason | None = None


@dataclass(frozen=True, slots=True)
class Press:
    """One press: on which offer, and what it asks."""

    offer_id: int
    kind: PressKind
    value: ChannelKind | RejectReason | None = None

    @property
    def decision(self) -> Decision | None:
        """What it decides; ``None`` for a press that only redraws."""
        if self.kind is PressKind.MAYBE:
            return Decision(ChannelStatus.MAYBE)
        if self.kind is PressKind.KIND and isinstance(self.value, ChannelKind):
            return Decision(ChannelStatus.SEED, kind=self.value)
        if self.kind is PressKind.REASON and isinstance(
            self.value, RejectReason
        ):
            return Decision(ChannelStatus.REJECTED, reason=self.value)
        return None


def _data(offer_id: int, kind: PressKind, value: str | None = None) -> str:
    parts = [PREFIX, str(offer_id), kind.value]
    if value is not None:
        parts.append(value)
    return ":".join(parts)


def parse(data: str | None) -> Press | None:
    """A button's data as a press, or ``None`` for anything else."""
    if not data or len(data.encode()) > DATA_LIMIT:
        return None
    parts = data.split(":")
    if len(parts) not in (3, 4) or parts[0] != PREFIX:
        return None
    if not parts[1].isdigit():
        return None
    try:
        kind = PressKind(parts[2])
    except ValueError:
        return None
    offer_id = int(parts[1])
    takes_value = kind in (PressKind.KIND, PressKind.REASON)
    if takes_value != (len(parts) == 4):
        return None
    if kind is PressKind.KIND:
        try:
            return Press(offer_id, kind, ChannelKind(parts[3]))
        except ValueError:
            return None
    if kind is PressKind.REASON:
        try:
            return Press(offer_id, kind, RejectReason(parts[3]))
        except ValueError:
            return None
    return Press(offer_id, kind)


def first_step(offer_id: int) -> Keyboard:
    """The three buttons a card carries."""
    return (
        (
            (SEED, _data(offer_id, PressKind.SEED)),
            (MAYBE, _data(offer_id, PressKind.MAYBE)),
            (REJECT, _data(offer_id, PressKind.REJECT)),
        ),
    )


def _second_step(offer_id: int, buttons: Sequence[Button]) -> Keyboard:
    """Two to a row, and back on a row of its own."""
    rows = [tuple(buttons[i : i + 2]) for i in range(0, len(buttons), 2)]
    rows.append(((BACK, _data(offer_id, PressKind.BACK)),))
    return tuple(rows)


def kinds(offer_id: int) -> Keyboard:
    """The kinds a seed is marked with."""
    return _second_step(
        offer_id,
        [
            (label, _data(offer_id, PressKind.KIND, kind.value))
            for kind, label in KIND_LABELS.items()
        ],
    )


def reasons(offer_id: int) -> Keyboard:
    """The reasons a rejection is marked with."""
    return _second_step(
        offer_id,
        [
            (label, _data(offer_id, PressKind.REASON, reason.value))
            for reason, label in REASON_LABELS.items()
        ],
    )


def _name(label: str) -> str:
    """A label without its emoji, as a sentence carries it."""
    words = label.split(" ", 1)[1]
    return words[0].lower() + words[1:]


def decision_line(decision: Decision) -> str:
    """The line a card gains once decided."""
    if decision.status is ChannelStatus.SEED and decision.kind is not None:
        return f"✅ В сиды · {_name(KIND_LABELS[decision.kind])}"
    if (
        decision.status is ChannelStatus.REJECTED
        and decision.reason is not None
    ):
        return f"🚫 Отклонён · {_name(REASON_LABELS[decision.reason])}"
    return MAYBE


@dataclass(frozen=True, slots=True)
class CardView:
    """What a card shows, read at sending."""

    username: str
    title: str | None
    families: int
    families_before: int
    first_in_window: datetime | None
    posts: Sequence[SeedPost]


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def channel_link(username: str) -> str:
    """The channel's own link: what the card previews."""
    return f"https://t.me/{username}"


def _seed(post: SeedPost) -> str:
    if post.username:
        name = _escape(f"@{post.username}")
        url = html.escape(f"https://t.me/{post.username}/{post.msg_id}")
        return f'<a href="{url}">{name}</a>'
    return _escape(post.title or "сид без имени")


def render_card(view: CardView, *, window_days: int, zone: str) -> str:
    """A card's text, in Telegram's HTML."""
    url = html.escape(channel_link(view.username))
    lines = [f'📈 Растёт: <a href="{url}">{_escape("@" + view.username)}</a>']
    if view.title:
        lines.append(f"<b>{_escape(view.title)}</b>")
    before = (
        f"{view.families_before} раньше"
        if view.families_before
        else "раньше ни одной"
    )
    evidence = (
        f"{families_phrase(view.families)} за {window_days} дней, {before}"
    )
    if view.first_in_window is not None:
        first = view.first_in_window.astimezone(ZoneInfo(zone))
        evidence += f" · впервые в окне {first.day} {MONTHS[first.month - 1]}"
    lines.append(evidence)
    if view.posts:
        lines.append("Ссылались: " + ", ".join(map(_seed, view.posts)))
    return "\n".join(lines)
