"""An alert, as a message a person reads.

Pure: rows in, text out. No aiogram, no session, no clock — which is what
makes the wording testable, and the wording is most of what this feature
actually is. An alert that arrives correctly and reads badly has failed.

**Evidence is read here, not stored on the alert.** Which channels
carried a post is a query at rendering time, so a digest read in the
morning shows fresher numbers than the moment the alert was raised. That
is intended rather than tolerated: the question a reader has is how far
this went, not how far it had gone when a scheduled job noticed. Do not
"fix" it by copying the reposter list onto the alert row — that would put
a derived measure into an observation table, which is the trade ``Edge``
already refuses.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from itgraph.db.models import AlertKind

__all__ = [
    "Carrier",
    "RenderedAlert",
    "age_phrase",
    "digest",
    "render_cascade",
    "render_spike",
]

# How much of a post to quote. Long enough to recognise it, short enough
# that a message about a post is not a copy of it.
EXCERPT = 200

# What each spike is about, in the words a reader thinks in. Per kind
# rather than one "необычная активность", because the four mean genuinely
# different things — reach, approval, an endorsement strong enough to
# republish, and an argument — and a reader deciding whether to open the
# post is deciding on exactly that difference.
SPIKE_LABEL = {
    AlertKind.VIEWS_SPIKE: "👁 Просмотры",
    AlertKind.REACTION_SPIKE: "❤️ Реакции",
    AlertKind.FORWARD_SPIKE: "🔁 Пересылки",
    AlertKind.COMMENT_SPIKE: "💬 Комментарии",
}


@dataclass(frozen=True, slots=True)
class Carrier:
    """One channel seen carrying the post, as the message names it."""

    title: str | None
    username: str | None

    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.title or "неизвестный канал"


@dataclass(frozen=True, slots=True)
class RenderedAlert:
    """A message and the id it answers for."""

    alert_id: int
    text: str


def post_link(username: str | None, msg_id: int) -> str | None:
    """A link to the post, or ``None`` for a channel without a handle.

    A channel with no username is unreachable by link for anyone who was
    not let in, so no link is better than a broken one.
    """
    return f"https://t.me/{username}/{msg_id}" if username else None


def age_phrase(age: timedelta) -> str:
    """A duration as a person reads it. Public because the status
    handler wants the same wording, and two formatters drifting apart
    would make the same interval read two ways in one conversation."""
    minutes = max(int(age.total_seconds()) // 60, 0)
    if minutes < 60:
        return f"{minutes} мин назад"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {rest:02d} мин назад"
    return f"{hours // 24} дн назад"


def _excerpt(text: str | None) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT:
        return collapsed
    return collapsed[:EXCERPT].rstrip() + "…"


def render_cascade(
    *,
    alert_id: int,
    channel_title: str | None,
    channel_username: str | None,
    msg_id: int,
    published_at: datetime,
    now: datetime,
    families: int,
    carriers: Sequence[Carrier],
    text: str | None,
) -> RenderedAlert:
    """One post that is travelling, as one message.

    **One message per post, never one per carrier.** Three channels
    picking up the same post is one event; sending it three times would
    turn the most interesting case into the most annoying one.

    The post's age is stated because it is the number a reader will
    otherwise assume wrongly. A cascade takes hours to form — the median
    second family arrives at 4h37m — so an alert is never about something
    that happened a minute ago, and a reader who expects minutes
    concludes the system is broken.
    """
    who = channel_title or (
        f"@{channel_username}" if channel_username else "канал"
    )
    lines = [
        f"🔁 <b>{families}</b> независимых источника(ов) репостят",
        f"{who} · {age_phrase(now - published_at)}",
    ]

    link = post_link(channel_username, msg_id)
    if link:
        lines.append(link)

    excerpt = _excerpt(text)
    if excerpt:
        lines.append("")
        lines.append(excerpt)

    if carriers:
        lines.append("")
        lines.append("Кто перенёс: " + ", ".join(c.label() for c in carriers))

    return RenderedAlert(alert_id=alert_id, text="\n".join(lines))


def _magnitude(z: float) -> str:
    """How far past normal, in words.

    Words rather than the multiple, because the multiple is not on the
    alert and cannot honestly be recovered from it: a z is a distance in
    units of the spread of the baseline run that produced it, and
    reconstructing "×3.1" at render time would mean joining a run that
    may since have been replaced. The z itself is stated too, for
    whoever is calibrating rather than reading.
    """
    if z < 4:
        return "заметно выше обычного"
    if z < 6:
        return "намного выше обычного"
    return "исключительно много"


def render_spike(
    *,
    alert_id: int,
    kind: AlertKind,
    channel_title: str | None,
    channel_username: str | None,
    msg_id: int,
    published_at: datetime,
    now: datetime,
    z: float,
    text: str | None,
) -> RenderedAlert:
    """One post doing unusually well on one metric, as one message.

    No carrier list: a spike is a fact about the post's own audience, and
    who else republished it is the cascade alert's subject. Putting both
    in every message would make the two kinds indistinguishable at a
    glance, which is the only thing the kinds are for.

    The age is stated for the same reason it is on a cascade, and matters
    more here: a spike alert can arrive fifteen minutes after publication,
    so a reader who has learned to expect hours would misread how early
    this is.
    """
    who = channel_title or (
        f"@{channel_username}" if channel_username else "канал"
    )
    label = SPIKE_LABEL.get(kind, "📈 Активность")
    lines = [
        f"{label} — {_magnitude(z)} (z {z:.1f})",
        f"{who} · {age_phrase(now - published_at)}",
    ]

    link = post_link(channel_username, msg_id)
    if link:
        lines.append(link)

    excerpt = _excerpt(text)
    if excerpt:
        lines.append("")
        lines.append(excerpt)

    return RenderedAlert(alert_id=alert_id, text="\n".join(lines))


def digest(rendered: Sequence[RenderedAlert], *, held: int) -> str:
    """Everything held by the cap or by quiet hours, as one message.

    States how many it covers, always. What separates a digest from a
    silent drop is that the reader can tell the difference, and a count
    is the cheapest way to tell them.
    """
    header = f"📋 За прошедший период — {held} оповещение(й)"
    if not rendered:
        return header
    body = "\n\n———\n\n".join(entry.text for entry in rendered)
    return f"{header}\n\n{body}"
