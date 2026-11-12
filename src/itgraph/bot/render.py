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

**Everything taken from a post, a channel or a video is escaped.** The
messages are HTML, and a title with ``<`` or ``&`` in it would otherwise
be refused by Telegram — on every retry, for as long as the alert lived.

**Every message fits in one Telegram message,** and a digest that does
not is sent in parts (``digest_parts``): Telegram takes "1-4096
characters" and refused the whole digest when one ran longer.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from itgraph.db.models import AlertKind, YtAlertKind, YtAlertRoute, YtMetric
from itgraph.derive.yt_videos import Counters

__all__ = [
    "MESSAGE_LIMIT",
    "Carrier",
    "DigestPart",
    "RenderedAlert",
    "WeeklyEntryView",
    "age_phrase",
    "digest_parts",
    "message_parts",
    "render_cascade",
    "render_spike",
    "render_weekly_entry",
    "render_youtube",
    "units",
    "week_label",
    "weekly_empty",
    "weekly_gone",
    "weekly_header",
]

# How much of a post to quote. Long enough to recognise it, short enough
# that a message about a post is not a copy of it.
EXCERPT = 200

# Telegram's limit on one message's text, and what one alert may take of
# it: the rest is the room a digest's header and separator need, so that
# any alert fits in a part of its own.
MESSAGE_LIMIT = 4096
ENTRY_LIMIT = 3800

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

# YouTube's three signals, in the same words.
YT_LABEL = {
    YtAlertKind.VIEWS_SPIKE: "👁 Просмотры",
    YtAlertKind.LIKE_SPIKE: "👍 Лайки",
    YtAlertKind.COMMENT_SPIKE: "💬 Комментарии",
}

SEPARATOR = "\n\n———\n\n"


def units(text: str) -> int:
    """A text's length as Telegram counts it: in UTF-16 code units.

    An emoji is two of them, and Python's ``len`` says one. Counted with
    the markup in, which Telegram does not count, so this errs long.
    """
    return len(text.encode("utf-16-le")) // 2


def _fit(text: str, limit: int = ENTRY_LIMIT) -> str:
    """The text, cut at a line boundary if it would not fit.

    Lines are dropped from the end, where the excerpt and the list of
    carriers are; the markup is all in the first lines, so a cut never
    leaves a tag open.
    """
    if units(text) <= limit:
        return text
    kept: list[str] = []
    for line in text.split("\n"):
        if units("\n".join([*kept, line, "…"])) > limit:
            break
        kept.append(line)
    if not kept:
        return text[: limit // 2] + "…"
    return "\n".join([*kept, "…"])


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


@dataclass(frozen=True, slots=True)
class Carrier:
    """One channel seen carrying the post, as the message names it."""

    title: str | None
    username: str | None

    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        return _escape(self.title or "неизвестный канал")


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
        return _escape(collapsed)
    return _escape(collapsed[:EXCERPT].rstrip()) + "…"


def _who(title: str | None, username: str | None) -> str:
    if title:
        return _escape(title)
    return f"@{username}" if username else "канал"


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
    who = _who(channel_title, channel_username)
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

    return RenderedAlert(alert_id=alert_id, text=_fit("\n".join(lines)))


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


def _usual(expected: float) -> str:
    """What a channel's posts usually have, in words a reader counts in."""
    if expected < 1:
        return "обычно меньше одного"
    return f"обычно около {round(expected)}"


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
    folded: int = 0,
    observed: float | None = None,
    expected: float | None = None,
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

    ``folded`` is how many other posts of the channel were folded into
    this one. Stated because it changes what the message means — not
    "this post interests people" but "this channel's whole audience
    grew" — and counted rather than listed, because a list of links is
    the series folding exists to replace. Nothing is said when it is
    zero: a single post standing out is the ordinary claim.

    A comment spike says its count and what the channel's posts usually
    have at that age, where it has them: a count is a number a reader
    reads, unlike the multiple of a spread. Without them — an alert raised
    before they were kept — it reads like the other kinds.
    """
    label = SPIKE_LABEL.get(kind, "📈 Активность")
    if (
        kind is AlertKind.COMMENT_SPIKE
        and observed is not None
        and expected is not None
    ):
        headline = (
            f"{label} — {round(observed)}, {_usual(expected)} (z {z:.1f})"
        )
    else:
        headline = f"{label} — {_magnitude(z)} (z {z:.1f})"
    who = _who(channel_title, channel_username)
    lines = [headline, f"{who} · {age_phrase(now - published_at)}"]

    link = post_link(channel_username, msg_id)
    if link:
        lines.append(link)

    excerpt = _excerpt(text)
    if excerpt:
        lines.append("")
        lines.append(excerpt)

    if folded:
        lines.append("")
        lines.append(
            f"📶 Вместе с ним выше нормы ещё {folded} пост(ов) канала — "
            "похоже, вырос охват всего канала (реклама, событие или "
            "накрутка), а не интерес к этому посту."
        )

    return RenderedAlert(alert_id=alert_id, text=_fit("\n".join(lines)))


def _clock(moment: datetime, *, now: datetime, zone: str) -> str:
    """A moment as a reader's clock reads it, with the date when it is
    not today's: "14:05 MSK", or "12.11 14:05 MSK"."""
    local = moment.astimezone(ZoneInfo(zone))
    today = now.astimezone(ZoneInfo(zone)).date()
    shape = "%H:%M %Z" if local.date() == today else "%d.%m %H:%M %Z"
    return local.strftime(shape)


def _count(value: int) -> str:
    """12400 as "12 400", the way a Russian reader groups digits."""
    return f"{value:,}".replace(",", " ")


def render_youtube(
    *,
    alert_id: int,
    kind: YtAlertKind,
    route: YtAlertRoute,
    video_id: str,
    title: str | None,
    channel_title: str | None,
    started_at: datetime | None,
    counters: Counters,
    counted_at: datetime | None,
    z: float,
    scored_at: datetime,
    now: datetime,
    zone: str,
    observed: int | None = None,
    expected: float | None = None,
    folded: int = 0,
) -> RenderedAlert:
    """One video doing unusually well, as one message or digest entry.

    What the site promises an alert holds:

    * **YouTube named as the source on this very message**, not once for
      a whole digest, by its name and a link to the video on YouTube.
      Telegram's preview of that link shows YouTube's own card.
    * **YouTube's counts with the moment they were read.** The newest
      stored, and dated, because a message stays in the chat. A counter
      YouTube did not report is left out, never written as zero.
    * **itgraph's score, said to be itgraph's,** with its own moment.

    The channel's title is left out when its data is not stored; the rest
    of the message stands without it.
    """
    lines = [f"▶️ YouTube · {route.value.upper()}"]

    about = f"<b>{_escape(title or 'видео без названия')}</b>"
    if channel_title:
        about += f" — {_escape(channel_title)}"
    if started_at is not None:
        about += f" · опубликовано {age_phrase(now - started_at)}"
    lines.append(about)
    lines.append(f"https://www.youtube.com/watch?v={video_id}")

    counts = [
        f"{name} {_count(value)}"
        for name, value in (
            ("просмотры", counters.views),
            ("лайки", counters.likes),
            ("комментарии", counters.comments),
        )
        if value is not None
    ]
    if counts and counted_at is not None:
        lines.append(
            f"Данные YouTube на {_clock(counted_at, now=now, zone=zone)}: "
            + " · ".join(counts)
        )

    label = YT_LABEL[kind]
    if (
        kind is YtAlertKind.COMMENT_SPIKE
        and observed is not None
        and expected is not None
    ):
        what = f"{label} — {observed}, {_usual(expected)}"
    else:
        what = f"{label} — {_magnitude(z)} для канала"
    lines.append(
        f"📈 Оценка itgraph на {_clock(scored_at, now=now, zone=zone)} "
        f"(рассчитана itgraph, не метрика YouTube): {what} (z {z:.1f})"
    )

    if folded:
        lines.append(
            f"📶 Вместе с ним выше нормы ещё {folded} видео канала — "
            "похоже, вырос охват всего канала, а не интерес к этому видео."
        )

    return RenderedAlert(alert_id=alert_id, text=_fit("\n".join(lines)))


@dataclass(frozen=True, slots=True)
class DigestPart:
    """One message of a digest, and which entries it carries."""

    text: str
    entries: tuple[int, ...]


def digest_parts(
    entries: Sequence[str], *, held: int, limit: int = MESSAGE_LIMIT
) -> list[DigestPart]:
    """Everything held by the cap or by quiet hours, as few messages as fit.

    States how many it covers, always, in the first part. What separates
    a digest from a silent drop is that the reader can tell the
    difference, and a count is the cheapest way to tell them.

    In parts because one message takes 4,096 characters, and a digest of
    a busy night did not fit: Telegram refused it, and the alerts in it
    waited until someone noticed. Each entry is whole in one part; an
    entry never needs more than one, since a rendered alert is bounded
    below the limit with room for a header.
    """
    return message_parts(
        entries,
        first=f"📋 За прошедший период — {held} оповещение(й)",
        later="📋 Продолжение",
        limit=limit,
    )


def message_parts(
    entries: Sequence[str],
    *,
    first: str,
    later: str,
    limit: int = MESSAGE_LIMIT,
) -> list[DigestPart]:
    """Entries under a header, as few messages as fit, each entry whole.

    ``first`` heads the first part and ``later`` the rest; when there is
    more than one, each header's first line says which part it is. With
    no entries, the one part is the header alone.
    """
    # Room for " (12/34)", whatever the numbers turn out to be.
    suffix = 12

    groups: list[list[int]] = [[]]
    size = units(first) + suffix
    for index, entry in enumerate(entries):
        cost = units(SEPARATOR if groups[-1] else "\n\n") + units(entry)
        if groups[-1] and size + cost > limit:
            groups.append([])
            size = units(later) + suffix
            cost = units("\n\n") + units(entry)
        groups[-1].append(index)
        size += cost

    count = len(groups)
    parts: list[DigestPart] = []
    for number, group in enumerate(groups, start=1):
        header = first if number == 1 else later
        if count > 1:
            top, newline, rest = header.partition("\n")
            header = f"{top} ({number}/{count}){newline}{rest}"
        body = SEPARATOR.join(entries[index] for index in group)
        text = f"{header}\n\n{body}" if body else header
        parts.append(DigestPart(text=text, entries=tuple(group)))
    return parts


# The month in the genitive, as a date is read in Russian: "16 ноября".
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def week_label(week_start: date) -> str:
    """A week as a reader names it: "16–22 ноября", "30 ноября – 6 декабря"."""
    last = week_start + timedelta(days=6)
    if week_start.month == last.month:
        return f"{week_start.day}–{last.day} {MONTHS[last.month - 1]}"
    return (
        f"{week_start.day} {MONTHS[week_start.month - 1]} – "
        f"{last.day} {MONTHS[last.month - 1]}"
    )


def _times(multiple: float) -> str:
    """How many times the usual, in words: "в 4,1 раза", "в 12 раз"."""
    if multiple < 10:
        rounded = round(multiple, 1)
        if rounded != int(rounded):
            return f"в {rounded:.1f}".replace(".", ",") + " раза"
        whole = int(rounded)
    else:
        whole = round(multiple)
    few = whole % 10 in (2, 3, 4) and whole % 100 not in (12, 13, 14)
    return f"в {whole} {'раза' if few else 'раз'}"


@dataclass(frozen=True, slots=True)
class WeeklyEntryView:
    """What an entry of a weekly list shows, read at sending.

    ``observed`` is the scored reading's count of the entry's signal, and
    ``expected`` what that reading was scored against: the multiple is
    honest here, unlike on an alert, because the list keeps the
    expectation its score came from.
    """

    video_id: str
    title: str | None
    channel_title: str | None
    short: bool
    started_at: datetime | None
    counters: Counters
    counted_at: datetime | None
    metric: YtMetric
    z: float
    observed: int | None
    expected: float
    others: int


def weekly_header(
    *,
    route: YtAlertRoute,
    week_start: date,
    computed_at: datetime,
    videos_scored: int,
    continued: bool,
    now: datetime,
    zone: str,
) -> str:
    """The head of a weekly list: whose, which week, whose score.

    A list resumed after a failed part says it continues, so the reader
    does not take the second half for a list of its own.
    """
    week = week_label(week_start)
    if continued:
        return f"📋 YouTube · {route.value.upper()} — продолжение списка за неделю {week}"
    clock = _clock(computed_at, now=now, zone=zone)
    return "\n".join(
        [
            f"📋 YouTube · {route.value.upper()} — выше нормы за неделю {week}",
            (
                "Лучшее видео каждого канала относительно его обычного "
                f"уровня; оценено {videos_scored} видео."
            ),
            (
                f"Оценка itgraph на {clock} — рассчитана itgraph, "
                "не метрика YouTube."
            ),
        ]
    )


def weekly_empty(header: str) -> str:
    """A list with nothing above the floor: its header, and that."""
    return f"{header}\n\nВыше нормы — ничего."


def weekly_gone(header: str) -> str:
    """A list whose every video YouTube stopped returning before it went.

    Not "nothing above the floor": something was, and the reader is told
    it can no longer be shown rather than told the week was quiet.
    """
    return (
        f"{header}\n\nВидео этого списка больше не доступны на YouTube, "
        "показать их нельзя."
    )


def render_weekly_entry(
    number: int, view: WeeklyEntryView, *, now: datetime, zone: str
) -> str:
    """One channel of a weekly list: its best video, as an alert shows one.

    What the site promises an alert holds, the entry holds on its own:
    YouTube named and the video linked, the counts with the time they were
    read — a counter YouTube did not report left out — and the score said
    to be itgraph's.
    """
    about = f"{number}. <b>{_escape(view.title or 'видео без названия')}</b>"
    if view.channel_title:
        about += f" — {_escape(view.channel_title)}"
    if view.short:
        about += " · Shorts"
    if view.started_at is not None:
        about += f" · опубликовано {age_phrase(now - view.started_at)}"
    lines = [about, f"https://www.youtube.com/watch?v={view.video_id}"]

    counts = [
        f"{name} {_count(value)}"
        for name, value in (
            ("просмотры", view.counters.views),
            ("лайки", view.counters.likes),
            ("комментарии", view.counters.comments),
        )
        if value is not None
    ]
    if counts and view.counted_at is not None:
        lines.append(
            f"YouTube на {_clock(view.counted_at, now=now, zone=zone)}: "
            + " · ".join(counts)
        )

    label = YT_LABEL[view.metric.alert_kind()]
    if view.metric is YtMetric.COMMENTS and view.observed is not None:
        what = f"{label} — {view.observed}, {_usual(view.expected)}"
    elif view.observed is not None and view.expected > 0:
        what = (
            f"{label} — {_times(view.observed / view.expected)} выше "
            "обычного для канала"
        )
    else:
        what = f"{label} — {_magnitude(view.z)} для канала"
    score = f"📈 Оценка itgraph: {what} (z {view.z:.1f})"
    if view.others:
        score += f"; ещё {view.others} видео канала выше нормы"
    lines.append(score)
    return _fit("\n".join(lines))
