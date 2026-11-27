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
characters" and refused the whole digest when one ran longer. A day
summary goes the same way (``summary_parts``), and everything in it but
the header is escaped plain text.
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
    "SummaryLine",
    "TgWeeklyEntryView",
    "WeeklyEntryView",
    "age_phrase",
    "digest_parts",
    "families_phrase",
    "message_parts",
    "render_cascade",
    "render_spike",
    "render_tg_summary_entry",
    "render_tg_weekly_entry",
    "render_weekly_entry",
    "render_youtube",
    "render_yt_summary_entry",
    "summary_header",
    "summary_parts",
    "tg_weekly_empty",
    "tg_weekly_gone",
    "tg_weekly_header",
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


def families_phrase(count: int) -> str:
    """A count of families, with the noun as Russian declines it. Public
    because both platforms' rising cards carry the same evidence line,
    and two declensions of one number would read as two different
    things."""
    tail = count % 100
    if 11 <= tail <= 14:
        noun = "семей"
    elif count % 10 == 1:
        noun = "семья"
    elif 2 <= count % 10 <= 4:
        noun = "семьи"
    else:
        noun = "семей"
    return f"{count} {noun}"


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
    channel_title: str | None,
    channel_username: str | None,
    msg_id: int,
    published_at: datetime,
    now: datetime,
    families: int,
    carriers: Sequence[Carrier],
    text: str | None,
) -> str:
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

    return _fit("\n".join(lines))


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
) -> str:
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
    label = SPIKE_LABEL[kind]
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

    return _fit("\n".join(lines))


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
) -> str:
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

    return _fit("\n".join(lines))


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


# --- the day summary -------------------------------------------------------
#
# A recap of everything the destination was sent directly in the day that
# has just ended (bot/summary.py). Its entries are built from the same
# pieces the alerts' own headlines use, so the two can never disagree
# about what z 3.7 is called; everything but the header is escaped plain
# text, and the whole message goes without a link preview, as the weekly
# lists do.


@dataclass(frozen=True, slots=True)
class SummaryLine:
    """One alert of a day summary: its text, and what orders it.

    The reader re-reads the day as a timeline, so the order is the
    post's publication time, not the moment the alert was sent;
    ``raised_at`` and then the id break ties, so a post's spike precedes
    its cascade deterministically.
    """

    published_at: datetime
    raised_at: datetime
    alert_id: int
    text: str


def _clock_time(moment: datetime, *, zone: str) -> str:
    """A moment as the destination's clock reads it: "08:56"."""
    return moment.astimezone(ZoneInfo(zone)).strftime("%H:%M")


def render_tg_summary_entry(
    *,
    kind: AlertKind,
    z: float,
    observed: float | None,
    expected: float | None,
    channel_title: str | None,
    channel_username: str | None,
    msg_id: int,
    published_at: datetime,
    zone: str,
) -> str:
    """One directly delivered Telegram alert, as a summary entry: three
    lines and nothing else.

    The headline an alert already uses — the metric and how far past
    normal the post went, a comment alert its count and the usual, a
    cascade its families without its carrier list. The second line
    names the channel and the post's clock time: the day is already in
    the header, and the entry is a record of when the post happened,
    not of how old it was when the alert fired. The third is the link,
    where the channel has one.
    """
    if kind is AlertKind.REPOST_CASCADE:
        headline = f"🔁 {int(z)} независимых источника(ов) репостят"
    elif (
        kind is AlertKind.COMMENT_SPIKE
        and observed is not None
        and expected is not None
    ):
        headline = (
            f"{SPIKE_LABEL[kind]} — "
            f"{round(observed)}, {_usual(expected)} (z {z:.1f})"
        )
    else:
        headline = f"{SPIKE_LABEL[kind]} — {_magnitude(z)} (z {z:.1f})"
    who = f"{_who(channel_title, channel_username)} · "
    lines = [headline, f"{who}{_clock_time(published_at, zone=zone)}"]
    link = post_link(channel_username, msg_id)
    if link:
        lines.append(link)
    return "\n".join(lines)


def render_yt_summary_entry(
    *,
    kind: YtAlertKind,
    z: float,
    observed: int | None,
    expected: float | None,
    title: str | None,
    channel_title: str | None,
    started_at: datetime | None,
    video_id: str,
    zone: str,
) -> str:
    """One directly delivered YouTube alert, as a summary entry: three
    lines and nothing else.

    The alert's own headline without the «для канала» suffix — the
    summary's reader has learned the scale by now — and the second line
    names the video and its channel with the video's clock time.
    """
    label = YT_LABEL[kind]
    if (
        kind is YtAlertKind.COMMENT_SPIKE
        and observed is not None
        and expected is not None
    ):
        headline = f"{label} — {observed}, {_usual(expected)} (z {z:.1f})"
    else:
        headline = f"{label} — {_magnitude(z)} (z {z:.1f})"
    about = _escape(title or "видео без названия")
    if channel_title:
        about += f" — {_escape(channel_title)}"
    if started_at is not None:
        about += f" · {_clock_time(started_at, zone=zone)}"
    return "\n".join(
        [headline, about, f"https://www.youtube.com/watch?v={video_id}"]
    )


def summary_header(day: date, *, continued: bool = False) -> str:
    """The head of a day's summary: the day it covers, no count.

    Unlike the digest's header, which says how many alerts it withheld —
    the count of a recap is not information the way the count of
    withheld alerts is.
    """
    if continued:
        return f"<b>Сводка за {day:%d.%m.%Y} — продолжение:</b>"
    return f"<b>Сводка за {day:%d.%m.%Y}:</b>"


def summary_parts(
    entries: Sequence[str], *, day: date, limit: int = MESSAGE_LIMIT
) -> list[DigestPart]:
    """A day's directly delivered alerts under its dated header, as few
    messages as fit, each entry whole in one part."""
    return message_parts(
        entries,
        first=summary_header(day),
        later=summary_header(day, continued=True),
        limit=limit,
    )


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


# How much of a post's first line a Telegram list entry quotes: enough to
# recognise the post, short enough that ten entries stay a list.
FIRST_LINE = 120


def _posts(count: int) -> str:
    """A count of posts, in the case Russian puts it: "1 пост", "2 поста",
    "3 912 постов"."""
    if count % 10 == 1 and count % 100 != 11:
        noun = "пост"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        noun = "поста"
    else:
        noun = "постов"
    return f"{_count(count)} {noun}"


def _first_line(text: str | None) -> str:
    """A post's first line, spaces collapsed, cut at ``FIRST_LINE``."""
    lines = (text or "").strip().splitlines()
    line = " ".join(lines[0].split()) if lines else ""
    if len(line) <= FIRST_LINE:
        return _escape(line)
    return _escape(line[:FIRST_LINE].rstrip()) + "…"


@dataclass(frozen=True, slots=True)
class TgWeeklyEntryView:
    """What an entry of Telegram's weekly list shows, read at sending.

    ``observed`` and ``expected`` are the entry's own: the scored
    reading's count of its signal and what it was scored against, so the
    multiple is honest, as on YouTube's list.
    """

    channel_title: str | None
    channel_username: str | None
    msg_id: int
    published_at: datetime
    text: str | None
    kind: AlertKind
    z: float
    observed: float
    expected: float
    others: int


def tg_weekly_header(
    *,
    week_start: date,
    computed_at: datetime,
    posts_scored: int,
    continued: bool,
    now: datetime,
    zone: str,
) -> str:
    """The head of Telegram's weekly list: whose, which week, whose score.

    A list resumed after a failed part says it continues, as YouTube's
    does.
    """
    week = week_label(week_start)
    if continued:
        return f"📋 Telegram — продолжение списка за неделю {week}"
    clock = _clock(computed_at, now=now, zone=zone)
    return "\n".join(
        [
            f"📋 Telegram — выше нормы за неделю {week}",
            (
                "Лучший пост каждого канала относительно его обычного "
                f"уровня; оценено {_posts(posts_scored)}."
            ),
            f"Оценка itgraph на {clock} — рассчитана itgraph.",
        ]
    )


def tg_weekly_empty(header: str) -> str:
    """A list with no channel at the threshold: its header, and that.

    The header's count of posts scored tells a quiet week from a copy
    where nothing was scored at all.
    """
    return f"{header}\n\nВыше порога — ничего."


def tg_weekly_gone(header: str) -> str:
    """A list whose every post went, with its channel, before it was sent.

    Not "nothing at the threshold": something was, and the reader is told
    it can no longer be shown rather than told the week was quiet.
    """
    return (
        f"{header}\n\nПосты этого списка удалены вместе с их каналами, "
        "показать их нельзя."
    )


def render_tg_weekly_entry(
    number: int, view: TgWeeklyEntryView, *, now: datetime
) -> str:
    """One channel of Telegram's list: its best post, as an alert shows one.

    The channel, the post's age, a link where the channel has a username,
    the first line of the text, and the signal — a comment entry by its
    count and the usual, any other by how many times the usual — with its
    z. No count of views, reactions or forwards, as no Telegram alert
    has one. No fold note: the count of the channel's other posts at the
    threshold says the same, once for the week.
    """
    if view.channel_title:
        who = f"<b>{_escape(view.channel_title)}</b>"
        if view.channel_username:
            who += f" (@{view.channel_username})"
    else:
        who = f"@{view.channel_username}" if view.channel_username else "канал"
    lines = [
        f"{number}. {who} · опубликовано {age_phrase(now - view.published_at)}"
    ]

    link = post_link(view.channel_username, view.msg_id)
    if link:
        lines.append(link)

    first = _first_line(view.text)
    if first:
        lines.append(first)

    label = SPIKE_LABEL[view.kind]
    if view.kind is AlertKind.COMMENT_SPIKE:
        what = f"{label} — {round(view.observed)}, {_usual(view.expected)}"
    elif view.expected > 0:
        what = (
            f"{label} — {_times(view.observed / view.expected)} выше "
            "обычного для канала"
        )
    else:
        what = f"{label} — {_magnitude(view.z)} для канала"
    score = f"{what} (z {view.z:.1f})"
    if view.others:
        score += f"; ещё {_posts(view.others)} канала выше порога"
    lines.append(score)
    return _fit("\n".join(lines))


# --- the second wind ------------------------------------------------------
#
# A section after a weekly list's entries (`add-second-wind`): older posts
# and videos whose views sped up this week. Its heading travels in the
# first entry's text, so splitting a list into parts keeps the two
# together; the section numbers its entries from one.

SECOND_WIND_POSTS = (
    "🔁 Второе дыхание — старые посты, которые разогнались за неделю"
)
SECOND_WIND_VIDEOS = (
    "🔁 Второе дыхание — старые видео, которые разогнались за неделю"
)
SECOND_WIND_NONE = "🔁 Второе дыхание — ничего не разогналось."


def _faster(acceleration: float) -> str:
    """How much faster than the week before: "в 4 раза быстрее…"."""
    return f"{_times(acceleration)} быстрее прошлой недели"


def _of_norm(share: float, *, new: str, age: str) -> str:
    """A week at this pace against the channel's norm, as a reader says it."""
    if share >= 1:
        return f"за неделю {_times(share)} больше, чем {new} канала за {age}"
    return f"за неделю {round(share * 100)}% того, что {new} канала за {age}"


@dataclass(frozen=True, slots=True)
class TgRevivalView:
    """What a second wind entry of Telegram's list shows, read at sending."""

    channel_title: str | None
    channel_username: str | None
    msg_id: int
    published_at: datetime
    text: str | None
    acceleration: float | None
    share: float
    others: int


def render_tg_revival(
    number: int,
    view: TgRevivalView,
    *,
    now: datetime,
    heading: bool = False,
) -> str:
    """One channel of the second wind: its post that sped up most.

    What a list entry shows of the post, and instead of a score how much
    faster than the week before its views went, and how a week at that
    pace compares with a new post of the channel — both itgraph's
    numbers, and said so. No count, as the list's entries have none.
    """
    if view.channel_title:
        who = f"<b>{_escape(view.channel_title)}</b>"
        if view.channel_username:
            who += f" (@{view.channel_username})"
    else:
        who = f"@{view.channel_username}" if view.channel_username else "канал"
    lines = [SECOND_WIND_POSTS] if heading else []
    lines.append(
        f"{number}. {who} · опубликовано {age_phrase(now - view.published_at)}"
    )
    link = post_link(view.channel_username, view.msg_id)
    if link:
        lines.append(link)
    first = _first_line(view.text)
    if first:
        lines.append(first)

    sped = (
        "Пост снова начал набирать просмотры"
        if view.acceleration is None
        else f"Просмотры шли {_faster(view.acceleration)}"
    )
    what = (
        f"🔁 {sped} — "
        + _of_norm(view.share, new="обычный новый пост", age="8 часов")
        + " (оценка itgraph)"
    )
    if view.others:
        what += f"; ещё {_posts(view.others)} канала разогнались"
    lines.append(what)
    return _fit("\n".join(lines))


@dataclass(frozen=True, slots=True)
class YtRevivalView:
    """What a second wind entry of a YouTube list shows, read at sending.

    The views are YouTube's, at the two readings around the week, each
    with its moment; ``None`` where YouTube reported none.
    """

    video_id: str
    title: str | None
    channel_title: str | None
    started_at: datetime | None
    start_views: int | None
    start_at: datetime
    end_views: int | None
    end_at: datetime
    acceleration: float | None
    share: float
    others: int


def render_yt_revival(
    number: int,
    view: YtRevivalView,
    *,
    now: datetime,
    zone: str,
    heading: bool = False,
) -> str:
    """One channel of the second wind: its video that sped up most.

    As a list entry: YouTube named and the video linked, YouTube's counts
    with the times they were read — here the views at the start and the
    end of the week and the gain between — and itgraph's two multiples,
    said to be itgraph's.
    """
    about = f"{number}. <b>{_escape(view.title or 'видео без названия')}</b>"
    if view.channel_title:
        about += f" — {_escape(view.channel_title)}"
    if view.started_at is not None:
        about += f" · опубликовано {age_phrase(now - view.started_at)}"
    lines = [SECOND_WIND_VIDEOS] if heading else []
    lines += [about, f"https://www.youtube.com/watch?v={view.video_id}"]

    if view.start_views is not None and view.end_views is not None:
        start = _clock(view.start_at, now=now, zone=zone)
        end = _clock(view.end_at, now=now, zone=zone)
        gain = view.end_views - view.start_views
        lines.append(
            f"YouTube: {_count(view.start_views)} просмотров на {start} → "
            f"{_count(view.end_views)} на {end} (+{_count(gain)})"
        )

    sped = (
        "видео снова начало набирать просмотры"
        if view.acceleration is None
        else _faster(view.acceleration)
    )
    what = f"🔁 Оценка itgraph: {sped} — " + _of_norm(
        view.share, new="обычное новое видео", age="сутки"
    )
    if view.others:
        what += f"; ещё {view.others} видео канала разогнались"
    lines.append(what)
    return _fit("\n".join(lines))
