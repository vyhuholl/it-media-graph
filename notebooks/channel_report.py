"""One channel's own numbers, as markdown to hand back to its author.

Exploratory tooling: not part of the package, not spec'd, not tested.
Run it, read the file, change the constants, run it again.

    uv sync --group data
    uv run notebooks/channel_report.py opensource_findings
    uv run notebooks/channel_report.py opensource_findings --lang ru

Several usernames may be given; channels of one author are worth
reporting together, and the sheet columns already treat them as one
voice. `--lang` picks the language of the report itself and nothing
else — the same numbers, the same order, a separate file per language,
so a channel can be answered in whichever one its author writes in.

Everything here is a slice of numbers the other scripts in this
directory already computed — the three xlsx files are the input, and a
stale one produces a stale report. Only the counterpart lists at the end
come from the database, because no existing sheet holds them per
channel.

This is a separate script rather than a filter over those sheets because
what may be said to a channel's author is a narrower thing than what may
be looked at locally, and the difference is not visible in a
spreadsheet. Three boundaries, enforced here rather than left to whoever
copies rows out:

* **Only seed counterparts are named.** Most of what this channel points
  at is a `candidate` — the review queue, which is the discovery
  pipeline's working state and belongs to nobody outside it. Outgoing
  references are therefore reported as counts and never as a list, while
  incoming ones name their source only when that source is a seed. The
  author's own channels are the one exception, and a narrow one: a
  confirmed family member is named whatever its status, because it is
  the recipient's own channel being handed back to them. Nothing about
  *how* the pair was found travels with it — not the score, not the
  evidence columns, not the reviewer's note.
* **Nothing about the operator's own judgement.** The alerts section
  reports what was raised and never `alert_feedback`: which alerts the
  operator called useful is the labelled data this project trains its
  thresholds on, and it says more about them than about the channel.
  `band`, `delivered_at` and the delivery columns are settings and
  plumbing, and are left out for the same reason.
* **No cluster roster.** A cluster is dozens of seeds, and printing its
  members publishes a chunk of the inventory. The cluster's size, keyword
  label and dominant kind describe the neighbourhood without naming it.
* **No review columns.** `discovered_via`, `reject_reason`,
  `reject_note` and `kind_note` are not read here at all, and the
  channel's own status appears only as a precondition: a non-seed
  argument is refused rather than reported on, since a channel with no
  collected history would produce a page of blanks and its presence in
  the inventory is itself the thing not being published.

The caveats printed at the foot of the report are not decoration. Post
metrics are one snapshot taken at whatever age the backfill found each
post, and the post count is the depth of that walk rather than the
channel's output — a reader who takes either at face value will draw a
wrong conclusion, and they cannot tell from the numbers alone. They are
translated with the same care as the tables: a report whose caveats read
as boilerplate in one language and as prose in the other is a report
whose caveats get skipped.
"""

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from _db import dsn

DSN = dsn()
DATA = Path(__file__).resolve().parent.parent / "data"
SCORECARD = DATA / "channel_scorecard.xlsx"
STYLE = DATA / "channel_style.xlsx"
CLUSTERS = DATA / "clusters.xlsx"

LANGS = ("en", "ru")

# One file per run and per language, named after the first channel asked
# for. The language is in the name rather than overwriting: answering the
# same author in both is a normal thing to want, and a run that silently
# replaced the other one would be found out only after sending.
OUT_TEMPLATE = "channel_report_{username}.{lang}.md"

# Below this many collected posts a percentile is misleading enough to
# suppress: the sheets already blank their rate columns under ten posts,
# and a rank computed from four of them ranks noise.
MIN_POSTS_FOR_RATES = 10

# How much of a post's text goes into the "posts that travelled" table.
SNIPPET = 70

CHANNEL = """
    SELECT tg_id, title, username, status::text, kind::text
    FROM channels
    WHERE lower(username) = lower(%(username)s)
"""

# Everything by the same author. A channel absent from the view is a
# family of one, so the channel's own id is always added by the caller.
FAMILY = """
    SELECT channel_id
    FROM channel_families
    WHERE family_key = (
        SELECT family_key FROM channel_families WHERE channel_id = %(id)s
    )
"""

COLLECTED = """
    SELECT
        COUNT(*),
        MIN((payload->>'date')::timestamptz),
        MAX((payload->>'date')::timestamptz)
    FROM raw_messages
    WHERE channel_id = %(id)s AND payload->>'_' = 'Message'
"""

# Named, because every source here is a seed: a public channel whose
# repost the author already sees under their own post.
INCOMING = """
    SELECT
        s.title,
        s.username,
        COUNT(*) FILTER (WHERE e.kind::text = 'forward') AS forwards,
        COUNT(*) FILTER (WHERE e.kind::text = 'mention') AS mentions,
        MAX(e.published_at) AS last_seen
    FROM edges e
    JOIN channels s ON s.tg_id = e.src_channel_id
    WHERE e.dst_channel_id = %(id)s
      AND s.status = 'seed'
      AND NOT (s.tg_id = ANY(%(family)s))
    GROUP BY s.title, s.username
    ORDER BY forwards DESC, mentions DESC, s.title
"""

# Counts only. The targets are mostly candidates, and naming them is
# what this script exists to not do.
OUTGOING = """
    SELECT
        e.kind::text,
        COUNT(*) AS refs,
        COUNT(DISTINCT e.dst_channel_id) AS targets
    FROM edges e
    WHERE e.src_channel_id = %(id)s
      AND NOT (e.dst_channel_id = ANY(%(family)s))
    GROUP BY 1
    ORDER BY 1
"""

# Ranked by distinct *families* that carried the post, as in
# cited_posts.py: one author reposting themselves across their own
# channels is distribution, not travel.
TRAVELLED = """
    SELECT
        e.dst_msg_id,
        MIN(e.dst_published_at) AS published_at,
        COUNT(DISTINCT COALESCE(f.family_key, s.tg_id)) AS families,
        COUNT(DISTINCT s.tg_id) AS sources
    FROM edges e
    JOIN channels s ON s.tg_id = e.src_channel_id
    LEFT JOIN channel_families f ON f.channel_id = s.tg_id
    WHERE e.dst_channel_id = %(id)s
      AND e.kind::text = 'forward'
      AND e.dst_msg_id IS NOT NULL
      AND s.status = 'seed'
      AND NOT (s.tg_id = ANY(%(family)s))
    GROUP BY e.dst_msg_id
    ORDER BY families DESC, sources DESC, published_at DESC
"""

TEXTS = """
    SELECT msg_id, payload->>'message'
    FROM raw_messages
    WHERE channel_id = %(id)s AND msg_id = ANY(%(ids)s)
"""

# The author's other channels. `decision` is not read: the view already
# holds only confirmed pairs, and the score, the evidence columns and the
# reviewer's note are how the pair was found rather than what it is.
AFFILIATED = """
    SELECT
        c.title,
        c.username,
        c.is_chat,
        (
            SELECT COUNT(*)
            FROM raw_messages r
            WHERE r.channel_id = c.tg_id AND r.payload->>'_' = 'Message'
        ) AS posts
    FROM channel_families f
    JOIN channels c ON c.tg_id = f.channel_id
    WHERE f.family_key = (
        SELECT family_key FROM channel_families WHERE channel_id = %(id)s
    )
      AND c.tg_id <> %(id)s
    ORDER BY posts DESC, c.title
"""

# What the live watcher has actually seen of this channel. The alerts
# section states this before its own table: an empty table means one
# thing over a year of watching and quite another over three days.
WATCHED = """
    SELECT
        COUNT(*),
        COUNT(DISTINCT msg_id),
        MIN(observed_at),
        MAX(observed_at)
    FROM message_metrics
    WHERE channel_id = %(id)s
"""

# Deliberately four columns. `band` is which configured threshold tier
# was crossed and `delivered_at` / `delivery` / `attempts` are the bot's
# plumbing — operator settings, not facts about the channel. The
# operator's own verdict in `alert_feedback` is not joined at all: what
# they thought of an alert is the labelled data this project trains on,
# and it is nobody else's business what got called a false alarm.
ALERTS = """
    SELECT a.kind::text, a.msg_id, a.value, a.raised_at
    FROM alerts a
    WHERE a.channel_id = %(id)s
    ORDER BY a.raised_at DESC
"""

# Every translated string is keyed by what it says, with the languages
# side by side. Two blocks per language would read more tidily and would
# also be where a translation silently goes missing; here a gap is a hole
# in the middle of a line, and `untranslated()` refuses to run with one.
LABELS: dict[str, dict[str, str]] = {
    "subscribers": {"en": "Subscribers", "ru": "Подписчики"},
    "median_views": {"en": "Median views", "ru": "Медиана просмотров"},
    "median_reactions": {
        "en": "Median reactions",
        "ru": "Медиана реакций",
    },
    "median_forwards": {
        "en": "Median forwards",
        "ru": "Медиана пересылок",
    },
    "reaction_rate": {
        "en": "Reactions per view",
        "ru": "Реакций на просмотр",
    },
    "forward_rate": {
        "en": "Forwards per view",
        "ru": "Пересылок на просмотр",
    },
    "comment_rate": {
        "en": "Comments per view",
        "ru": "Комментариев на просмотр",
    },
    "median_len": {
        "en": "Median post length, chars",
        "ru": "Медианная длина поста, знаков",
    },
    "p90_len": {
        "en": "90th-percentile length",
        "ru": "Длина по 90-му перцентилю",
    },
    "sentence_len": {
        "en": "Median sentence length",
        "ru": "Медианная длина предложения",
    },
    "text_share": {
        "en": "Posts carrying text",
        "ru": "Постов с текстом",
    },
    "fwd_share": {
        "en": "Posts that are reposts",
        "ru": "Постов-репостов",
    },
    "code_share": {
        "en": "Posts with a code block",
        "ru": "Постов с блоком кода",
    },
    "latin_share": {"en": "Latin characters", "ru": "Латиницы в тексте"},
    "link_density": {"en": "Links per post", "ru": "Ссылок на пост"},
    "emoji_density": {"en": "Emoji per post", "ru": "Эмодзи на пост"},
    "depth": {"en": "Depth score (z)", "ru": "Оценка глубины (z)"},
    "dryness": {"en": "Dryness score (z)", "ru": "Оценка занудства (z)"},
    "partners": {
        "en": "Distinct partners in the graph",
        "ru": "Разных партнёров в графе",
    },
    "in_sources": {
        "en": "Channels referencing it",
        "ru": "Ссылаются на него (каналов)",
    },
    "out_targets": {
        "en": "Channels it references",
        "ru": "Ссылается сам (каналов)",
    },
    "out_variety": {
        "en": "Outgoing variety (entropy)",
        "ru": "Разнообразие исходящих (энтропия)",
    },
    "reciprocity": {
        "en": "Reciprocated links",
        "ru": "Взаимных связей",
    },
    "inside_share": {
        "en": "Links staying inside its cluster",
        "ru": "Связей внутри своего кластера",
    },
    # What an alert was raised about. The four spikes are named by the
    # counter that moved rather than by their enum, which says `_spike`
    # three times to a reader who is looking at a column of them.
    "repost_cascade": {"en": "repost cascade", "ru": "каскад репостов"},
    "views_spike": {"en": "views", "ru": "просмотры"},
    "reaction_spike": {"en": "reactions", "ru": "реакции"},
    "forward_spike": {"en": "forwards", "ru": "пересылки"},
    "comment_spike": {"en": "comments", "ru": "комментарии"},
}

TEXT: dict[str, dict[str, str]] = {
    "title": {
        "en": "# Channel report — @{username}",
        "ru": "# Отчёт по каналу — @{username}",
    },
    "intro": {
        "en": (
            "Generated from an IT-media Telegram graph: a hand-reviewed "
            "set of channels whose history has been collected, and the "
            "reposts and mentions between them."
        ),
        "ru": (
            "Посчитано по графу айтишного телеграма: размеченный вручную "
            "набор каналов, чья история выкачана, и репосты с "
            "упоминаниями между ними."
        ),
    },
    "collected": {
        "en": "`{kind}` · {posts} collected, {first} → {last}",
        "ru": "`{kind}` · собрано {posts}, {first} → {last}",
    },
    "affiliated": {
        "en": "Same author, by confirmed affiliation:",
        "ru": "Тот же автор — подтверждённая аффилиация:",
    },
    "aff_chat": {"en": "discussion chat", "ru": "чат обсуждений"},
    "aff_posts": {"en": "{posts} collected", "ru": "собрано {posts}"},
    "aff_none": {
        "en": "no history collected",
        "ru": "история не собиралась",
    },
    "section_reach": {
        "en": "### Reach and engagement",
        "ru": "### Охват и вовлечённость",
    },
    "section_style": {
        "en": "### How it writes",
        "ru": "### Как канал пишет",
    },
    "section_position": {
        "en": "### Position in the graph",
        "ru": "### Положение в графе",
    },
    "section_incoming": {
        "en": "### Who reposts and mentions it",
        "ru": "### Кто его репостит и упоминает",
    },
    "section_travelled": {
        "en": "### Posts that travelled",
        "ru": "### Посты, которые разошлись",
    },
    "section_alerts": {
        "en": "### Flagged live by the alert bot",
        "ru": "### Что поймал бот оповещений",
    },
    "alerts_intro": {
        "en": (
            "A bot watches the collected channels as they publish and "
            "flags a post whose views, reactions, forwards or comments "
            "run far above what a post of that age normally reaches on "
            "the same channel — a z-score against the channel's own "
            "baseline, so neither the channel's size nor the post's age "
            "is what triggers it."
        ),
        "ru": (
            "Бот смотрит за собранными каналами по мере публикации и "
            "отмечает пост, у которого просмотры, реакции, пересылки "
            "или комментарии заметно выше того, что набирает пост "
            "такого же возраста на этом же канале, — это z-оценка "
            "против собственной нормы канала, так что срабатывает не "
            "размер канала и не возраст поста."
        ),
    },
    "alerts_window": {
        "en": (
            "On this channel it has been watching since {first}: "
            "{readings} of {posts}."
        ),
        "ru": "За этим каналом он следит с {first}: {readings} на {posts}.",
    },
    "alerts_unwatched": {
        "en": (
            "It has not watched this channel yet, so there is nothing "
            "here either way."
        ),
        "ru": (
            "За этим каналом он ещё не следил, так что здесь пусто по "
            "любому счёту."
        ),
    },
    "header_alerts": {
        "en": "| Post | Flagged | Signal | z |",
        "ru": "| Пост | Когда | Сигнал | z |",
    },
    "no_alerts": {
        "en": (
            "_Nothing crossed the threshold in that window. Over a few "
            "days of watching that is a statement about the window, not "
            "about the channel._"
        ),
        "ru": (
            "_Ничего не перешло порог в этом окне. За несколько дней "
            "наблюдения это утверждение про окно, а не про канал._"
        ),
    },
    "header_metric": {
        "en": "| | Value | Percentile |",
        "ru": "| | Значение | Перцентиль |",
    },
    "header_incoming": {
        "en": "| Channel | Reposts | Mentions | Last seen |",
        "ru": "| Канал | Репостов | Упоминаний | Последний раз |",
    },
    "header_travelled": {
        "en": "| Post | Published | Carried by | Channels |",
        "ru": "| Пост | Опубликован | Семей | Каналов |",
    },
    "no_row": {
        "en": "_No row in the source sheet._",
        "ru": "_Строки нет в исходной таблице._",
    },
    "not_in_inventory": {
        "en": "_Not in the inventory._",
        "ru": "_Канала нет в инвентаре._",
    },
    "no_history": {
        "en": "_No collected history — nothing to report._",
        "ru": "_Историю не собирали — отчитываться нечем._",
    },
    "no_incoming": {
        "en": "_Nothing in the collected set references it._",
        "ru": "_В собранном наборе на него никто не ссылается._",
    },
    "no_travelled": {
        "en": "_No reposts of individual posts recorded._",
        "ru": "_Репостов отдельных постов не зафиксировано._",
    },
    "no_text": {"en": "_no text_", "ru": "_без текста_"},
    "cluster": {
        "en": (
            "Clustering by who links to whom puts it in a group of "
            "**{size} channels**, mostly `{kind}`, whose recurring words "
            "are _{name}_."
        ),
        "ru": (
            "Кластеризация по связям кладёт его в группу из **{size} "
            "каналов**, преимущественно `{kind}`, с повторяющимися "
            "словами _{name}_."
        ),
    },
    "unsettled": {
        "en": (
            "That placement is not settled: across repeated runs of the "
            "clustering it kept the same companions only {share} of the "
            "time, against a typical channel's ~77%. It sits between "
            "crowds rather than inside one, so read the group as a hint "
            "about the neighbourhood and not as membership."
        ),
        "ru": (
            "Положение неустойчивое: в повторных прогонах кластеризации "
            "канал оставался с теми же соседями лишь в {share} случаев "
            "против ~77% у обычного канала. Он сидит между тусовками, а "
            "не внутри одной, — читайте группу как подсказку про "
            "окрестность, а не как принадлежность."
        ),
    },
    "settled": {
        "en": (
            "The placement is a solid one — it kept the same companions "
            "in {share} of repeated runs."
        ),
        "ru": (
            "Положение устойчивое — те же соседи в {share} повторных прогонов."
        ),
    },
    "bridge": {
        "en": (
            "Its strongest pull outside that group is toward the "
            "_{leans_to}_ crowd, which carries {share} of its links."
        ),
        "ru": (
            "Сильнее всего наружу его тянет к тусовке _{leans_to}_: на "
            "неё приходится {share} его связей."
        ),
    },
    "outgoing": {
        "en": (
            "Pointing outward, it made {parts}. Those targets are not "
            "listed: most of them are channels the inventory has not "
            "reviewed yet."
        ),
        "ru": (
            "Наружу: {parts}. Список не приводится: большинство этих "
            "каналов инвентарь ещё не разбирал."
        ),
    },
    "footer": {
        "en": "### Reading these numbers",
        "ru": "### Как это читать",
    },
    "footer_percentiles": {
        "en": (
            "* **Percentiles are against the collected inventory**, not "
            "against Telegram. It is an IT-media sample, so a percentile "
            "says where a channel sits among broadly comparable channels."
        ),
        "ru": (
            "* **Перцентили считаются по собранному инвентарю**, а не по "
            "всему телеграму. Это выборка айтишных каналов, так что "
            "перцентиль говорит, где канал стоит среди примерно "
            "сопоставимых."
        ),
    },
    "footer_snapshot": {
        "en": (
            "* **Every post metric is a single snapshot**, read whenever "
            "the backfill walked the channel, on posts of every age. "
            "Rates (reactions per view and so on) largely cancel that "
            "out; the raw medians do not, which is why they carry no "
            "percentile."
        ),
        "ru": (
            "* **Любая метрика поста — один снимок**, снятый тогда, "
            "когда сбор дошёл до канала, по постам всех возрастов. "
            "Относительные колонки (реакции на просмотр и прочие) это "
            "почти вычитают, сырые медианы — нет, поэтому у них нет "
            "перцентиля."
        ),
    },
    "footer_depth": {
        "en": (
            "* **The post count is collection depth, not output.** It is "
            "how far back the walk went, and it differs between channels."
        ),
        "ru": (
            "* **Число постов — это глубина сбора, а не то, сколько "
            "канал пишет.** Оно говорит, как далеко зашёл обход, и у "
            "разных каналов оно разное."
        ),
    },
    "footer_family": {
        "en": (
            "* **Reposts between channels of the same author are "
            "excluded** everywhere above. Self-distribution is not "
            "travel."
        ),
        "ru": (
            "* **Репосты между каналами одного автора исключены** везде "
            "выше. Самораспространение — не путь поста."
        ),
    },
    "footer_scope": {
        "en": (
            "* **Counterparts are only those inside the collected set.** "
            "Reposts from channels outside it exist and are not counted "
            "here."
        ),
        "ru": (
            "* **Контрагенты — только каналы внутри собранного "
            "набора.** Репосты снаружи существуют и здесь не посчитаны."
        ),
    },
}

# English needs two forms, Russian three, and the selector below picks
# by index — so the tuples are not interchangeable and the language owns
# its own arity. The Russian channel forms are dative: they are only ever
# used after "к" in the outgoing sentence.
NOUNS: dict[str, dict[str, tuple[str, ...]]] = {
    "forward": {
        "en": ("forward", "forwards"),
        "ru": ("репост", "репоста", "репостов"),
    },
    "mention": {
        "en": ("mention", "mentions"),
        "ru": ("упоминание", "упоминания", "упоминаний"),
    },
    # Singular drops "разным": one target is distinct by arithmetic, and
    # saying so out loud reads as a translation artefact.
    "channel": {
        "en": ("distinct channel", "distinct channels"),
        "ru": ("каналу", "разным каналам", "разным каналам"),
    },
    "post": {
        "en": ("post", "posts"),
        "ru": ("пост", "поста", "постов"),
    },
    "reading": {
        "en": ("reading", "readings"),
        "ru": ("замер", "замера", "замеров"),
    },
    # What a cascade alert counts: families of affiliated channels that
    # carried the post, not channels.
    "family": {
        "en": ("family", "families"),
        "ru": ("семья", "семьи", "семей"),
    },
}

CONJUNCTION = {"en": " and ", "ru": " и "}


def untranslated() -> list[str]:
    """Every string this script would fail to say in some language."""
    return [
        f"{key}.{lang}"
        for source in (LABELS, TEXT, NOUNS)
        for key, forms in source.items()
        for lang in LANGS
        if lang not in forms
    ]


def say(key: str, lang: str, **fields: Any) -> str:
    return TEXT[key][lang].format(**fields)


def digits(text: str, lang: str) -> str:
    """English number formatting, moved to the target language.

    Every formatter below writes `12,645.25` and this is the one place
    that turns it into `12 645,25`. A non-breaking space, so a table cell
    cannot be wrapped in the middle of a number.
    """
    if lang != "ru":
        return text
    return text.replace(",", "\u00a0").replace(".", ",")


def count(value: float | None, lang: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    return digits(f"{value:,.0f}", lang)


def ratio(value: float | None, lang: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    return digits(f"{value:.2%}", lang)


def number(value: float | None, lang: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    return digits(f"{value:,.2f}", lang)


def signed(value: float | None, lang: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    return digits(f"{value:+.2f}", lang)


# (column, formatter, whether a percentile is meaningful); the label
# comes from LABELS. Rank is suppressed for `subscribers` — it is
# Telegram's number, not this project's measurement — and for the raw
# medians, which are one snapshot each and rank post age as much as
# reach.
REACH = [
    ("subscribers", count, False),
    ("median_views", count, False),
    ("median_reactions", count, False),
    ("median_forwards", count, False),
    ("reaction_rate", ratio, True),
    ("forward_rate", ratio, True),
    ("comment_rate", ratio, True),
]

STYLE_METRICS = [
    ("median_len", count, True),
    ("p90_len", count, True),
    ("sentence_len", number, True),
    ("text_share", ratio, True),
    ("fwd_share", ratio, True),
    ("code_share", ratio, True),
    ("latin_share", ratio, True),
    ("link_density", number, True),
    ("emoji_density", number, True),
    ("depth", signed, True),
    ("dryness", signed, True),
]

POSITION = [
    ("partners", count, True),
    ("in_sources", count, True),
    ("out_targets", count, True),
    ("out_variety", number, True),
    ("reciprocity", ratio, True),
    ("inside_share", ratio, True),
]

# `stability` is deliberately not in that table. It does not measure the
# channel, it measures how much the *clustering* can be believed about
# it — a percentile of it would rank channels by how confident the
# method happens to be. Below this it is a coin toss and the cluster
# sentence has to say so; clusters.py uses the same threshold when it
# prints how many labels to read twice.
STABLE_ENOUGH = 0.8


def plural(n: int, noun: str, lang: str) -> str:
    forms = NOUNS[noun][lang]
    if lang == "ru":
        if n % 10 == 1 and n % 100 != 11:
            form = forms[0]
        elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            form = forms[1]
        else:
            form = forms[2]
    else:
        form = forms[0] if n == 1 else forms[1]
    return f"{digits(f'{n:,}', lang)} {form}"


def joined(parts: list[str], lang: str) -> str:
    if len(parts) < 2:
        return "".join(parts)
    return CONJUNCTION[lang].join([", ".join(parts[:-1]), parts[-1]])


def ordinal(value: float, lang: str) -> str:
    rank = round(value)
    if lang == "ru":
        return f"{rank}-й"
    if 10 <= rank % 100 <= 20:
        return f"{rank}th"
    return f"{rank}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(rank % 10, 'th') }"


def percentile(frame: pd.DataFrame, column: str, value: Any, lang: str) -> str:
    """Where `value` sits among every collected channel, as an ordinal."""
    if column not in frame or value is None or pd.isna(value):
        return "—"
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return "—"
    return ordinal(float((series < value).mean() * 100), lang)


def row_for(frame: pd.DataFrame, username: str) -> pd.Series | None:
    if "username" not in frame:
        return None
    hit = frame[frame["username"].astype(str).str.lower() == username]
    return None if hit.empty else hit.iloc[0]


def table(
    frame: pd.DataFrame,
    row: pd.Series | None,
    metrics: list[tuple[str, Any, bool]],
    ranked: bool,
    lang: str,
) -> list[str]:
    """One markdown table; `ranked` false blanks the percentile column."""
    if row is None:
        return [say("no_row", lang), ""]
    out = [say("header_metric", lang), "|---|---:|---:|"]
    for column, render, rankable in metrics:
        if column not in row.index:
            continue
        value = row[column]
        rank = (
            percentile(frame, column, value, lang)
            if rankable and ranked
            else "—"
        )
        label = LABELS[column][lang]
        out.append(f"| {label} | {render(value, lang)} | {rank} |")
    out.append("")
    return out


def escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def snippet(text: str | None, lang: str) -> str:
    if not text:
        return say("no_text", lang)
    flat = escape(" ".join(text.split()))
    return flat if len(flat) <= SNIPPET else f"{flat[:SNIPPET].rstrip()}…"


def day(value: datetime | None) -> str:
    return "—" if value is None else value.date().isoformat()


def link_to(title: str | None, handle: str | None) -> str:
    name = escape(title or handle or "")
    return f"[{name}](https://t.me/{handle})" if handle else name


def affiliated(
    conn: psycopg.Connection[Any], tg_id: int, lang: str
) -> list[str]:
    """The author's other channels, with how much of each was collected.

    The one place a non-seed channel may be named, and the exception is
    narrow on purpose: these are the author's own channels, being handed
    to that author, and the affiliation was confirmed by hand from public
    signals — a description that names the other, a shared username
    token, traffic that concentrates on one target. A family member with
    no collected history says so rather than appearing beside the ones
    that have numbers, because an empty row reads as a channel that does
    nothing rather than as one nobody walked.
    """
    members = conn.execute(AFFILIATED, {"id": tg_id}).fetchall()
    if not members:
        return []

    lines = [say("affiliated", lang), ""]
    for title, handle, is_chat, posts in members:
        note = (
            say("aff_posts", lang, posts=plural(posts, "post", lang))
            if posts
            else say("aff_none", lang)
        )
        if is_chat:
            note = f"{say('aff_chat', lang)}, {note}"
        lines.append(f"* {link_to(title, handle)} — {note}")
    lines.append("")
    return lines


def alert_value(kind: str, value: float, lang: str) -> str:
    """A cascade counts families; a spike is a z-score."""
    if kind == "repost_cascade":
        return plural(round(value), "family", lang)
    return digits(f"{value:+.1f}", lang)


def alerts(
    conn: psycopg.Connection[Any], tg_id: int, handle: str, lang: str
) -> list[str]:
    """What the live watcher flagged, under what it had a chance to see.

    The window comes first and is not optional. An empty table under a
    heading about spikes reads as "this channel never spikes", and over
    a watch that started days ago that reading is simply wrong — so the
    denominator is stated in prose above the table, in both the empty
    case and the full one.
    """
    lines = [say("section_alerts", lang), "", say("alerts_intro", lang), ""]

    readings, watched, first, _ = conn.execute(
        WATCHED, {"id": tg_id}
    ).fetchone()
    if not readings:
        return lines + [say("alerts_unwatched", lang), ""]

    lines += [
        say(
            "alerts_window",
            lang,
            first=day(first),
            readings=plural(readings, "reading", lang),
            posts=plural(watched, "post", lang),
        ),
        "",
    ]

    raised = conn.execute(ALERTS, {"id": tg_id}).fetchall()
    if not raised:
        return lines + [say("no_alerts", lang), ""]

    ids = [row[1] for row in raised]
    texts = dict(conn.execute(TEXTS, {"id": tg_id, "ids": ids}).fetchall())
    lines += [say("header_alerts", lang), "|---|---|---|---:|"]
    for kind, msg_id, value, raised_at in raised:
        link = f"https://t.me/{handle}/{msg_id}"
        lines.append(
            f"| [{snippet(texts.get(msg_id), lang)}]({link}) "
            f"| {day(raised_at)} | {LABELS[kind][lang]} "
            f"| {alert_value(kind, value, lang)} |"
        )
    lines.append("")
    return lines


def report(
    conn: psycopg.Connection[Any], username: str, lang: str
) -> list[str]:
    """The whole section for one channel, or a refusal."""
    found = conn.execute(CHANNEL, {"username": username}).fetchone()
    if found is None:
        return [f"## @{username}", "", say("not_in_inventory", lang), ""]
    tg_id, title, handle, status, kind = found
    if status != "seed":
        return [f"## @{handle}", "", say("no_history", lang), ""]

    family = [tg_id] + [row[0] for row in conn.execute(FAMILY, {"id": tg_id})]
    args = {"id": tg_id, "family": family}

    posts, first, last = conn.execute(COLLECTED, {"id": tg_id}).fetchone()
    ranked = posts >= MIN_POSTS_FOR_RATES

    scorecard = pd.ExcelFile(SCORECARD).parse("channels")
    style = pd.ExcelFile(STYLE).parse("channels")
    clustered = pd.ExcelFile(CLUSTERS).parse("channels")
    bridges = pd.ExcelFile(CLUSTERS).parse("bridges")
    groups = pd.ExcelFile(CLUSTERS).parse("clusters")

    key = handle.lower()
    score_row = row_for(scorecard, key)
    style_row = row_for(style, key)
    cluster_row = row_for(clustered, key)
    bridge_row = row_for(bridges, key)

    lines = [
        f"## {title} — @{handle}",
        "",
        say(
            "collected",
            lang,
            kind=kind,
            posts=plural(posts, "post", lang),
            first=day(first),
            last=day(last),
        ),
        "",
    ]
    lines += affiliated(conn, tg_id, lang)
    lines += [say("section_reach", lang), ""]
    lines += table(scorecard, score_row, REACH, ranked, lang)

    lines += [say("section_style", lang), ""]
    lines += table(style, style_row, STYLE_METRICS, ranked, lang)

    lines += [say("section_position", lang), ""]
    merged = clustered.merge(
        scorecard[
            [
                "username",
                "in_sources",
                "out_targets",
                "out_variety",
                "reciprocity",
            ]
        ],
        on="username",
        how="left",
    )
    position_row = row_for(merged, key)
    lines += table(merged, position_row, POSITION, ranked, lang)

    if cluster_row is not None:
        group = groups[groups["cluster"] == cluster_row["cluster"]]
        if not group.empty:
            one = group.iloc[0]
            lines += [
                say(
                    "cluster",
                    lang,
                    size=int(one["size"]),
                    kind=one["top_kind"],
                    name=one["name"],
                ),
                "",
            ]
        settled = cluster_row.get("stability")
        if settled is not None and not pd.isna(settled):
            key_name = "settled" if settled >= STABLE_ENOUGH else "unsettled"
            lines += [say(key_name, lang, share=f"{settled:.0%}"), ""]
    if bridge_row is not None:
        lines += [
            say(
                "bridge",
                lang,
                leans_to=bridge_row["leans_to"],
                share=f"{bridge_row['leans_share']:.0%}",
            ),
            "",
        ]

    lines += [say("section_incoming", lang), ""]
    incoming = conn.execute(INCOMING, args).fetchall()
    if incoming:
        lines += [say("header_incoming", lang), "|---|---:|---:|---|"]
        for src_title, src_handle, forwards, mentions, seen in incoming:
            lines.append(
                f"| {link_to(src_title, src_handle)} "
                f"| {forwards} | {mentions} | {day(seen)} |"
            )
        lines.append("")
    else:
        lines += [say("no_incoming", lang), ""]

    lines += [say("section_travelled", lang), ""]
    travelled = conn.execute(TRAVELLED, args).fetchall()
    if travelled:
        ids = [row[0] for row in travelled]
        texts = dict(conn.execute(TEXTS, {"id": tg_id, "ids": ids}).fetchall())
        lines += [say("header_travelled", lang), "|---|---|---:|---:|"]
        for msg_id, published, families, sources in travelled:
            link = f"https://t.me/{handle}/{msg_id}"
            lines.append(
                f"| [{snippet(texts.get(msg_id), lang)}]({link}) "
                f"| {day(published)} | {families} | {sources} |"
            )
        lines.append("")
    else:
        lines += [say("no_travelled", lang), ""]

    outgoing = conn.execute(OUTGOING, args).fetchall()
    if outgoing:
        parts = [
            f"{plural(refs, edge_kind, lang)} "
            f"{'к' if lang == 'ru' else 'to'} "
            f"{plural(targets, 'channel', lang)}"
            for edge_kind, refs, targets in outgoing
        ]
        lines += [say("outgoing", lang, parts=joined(parts, lang)), ""]

    lines += alerts(conn, tg_id, handle, lang)

    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One channel's numbers, as markdown for its author."
    )
    parser.add_argument("usernames", nargs="+", help="@handle, or handle")
    parser.add_argument(
        "--lang",
        choices=LANGS,
        default="en",
        help="language of the report itself (default: en)",
    )
    args = parser.parse_args()

    missing = untranslated()
    if missing:
        raise SystemExit(f"untranslated: {', '.join(missing)}")

    lang = args.lang
    usernames = [name.lstrip("@").lower() for name in args.usernames]

    lines = [
        say("title", lang, username=usernames[0]),
        "",
        say("intro", lang),
        "",
    ]

    with psycopg.connect(DSN) as conn:
        for username in usernames:
            lines += report(conn, username, lang)

    lines += [
        "---",
        "",
        say("footer", lang),
        "",
        say("footer_percentiles", lang),
        say("footer_snapshot", lang),
        say("footer_depth", lang),
        say("footer_family", lang),
        say("footer_scope", lang),
        "",
    ]

    out = DATA / OUT_TEMPLATE.format(username=usernames[0], lang=lang)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(lines)} lines -> {out}")


if __name__ == "__main__":
    main()
