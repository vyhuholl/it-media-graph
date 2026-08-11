"""One channel's own numbers, as markdown to hand back to its author.

Exploratory tooling: not part of the package, not spec'd, not tested.
Run it, read the file, change the constants, run it again.

    uv sync --group data
    uv run notebooks/channel_report.py opensource_findings

Several usernames may be given; channels of one author are worth
reporting together, and the sheet columns already treat them as one
voice.

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
  incoming ones name their source only when that source is a seed.
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
wrong conclusion, and they cannot tell from the numbers alone.
"""

import sys
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

# One file per run, named after the first channel asked for.
OUT_TEMPLATE = "channel_report_{username}.md"

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


def count(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:,.0f}"


def ratio(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.2%}"


def number(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:,.2f}"


def signed(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:+.2f}"


# (column, label, formatter, whether a percentile is meaningful). Rank
# is suppressed for `subscribers` — it is Telegram's number, not this
# project's measurement — and for the raw medians, which are one
# snapshot each and rank post age as much as reach.
REACH = [
    ("subscribers", "Subscribers", count, False),
    ("median_views", "Median views", count, False),
    ("median_reactions", "Median reactions", count, False),
    ("median_forwards", "Median forwards", count, False),
    ("reaction_rate", "Reactions per view", ratio, True),
    ("forward_rate", "Forwards per view", ratio, True),
    ("comment_rate", "Comments per view", ratio, True),
]

STYLE_METRICS = [
    ("median_len", "Median post length, chars", count, True),
    ("p90_len", "90th-percentile length", count, True),
    ("sentence_len", "Median sentence length", number, True),
    ("text_share", "Posts carrying text", ratio, True),
    ("fwd_share", "Posts that are reposts", ratio, True),
    ("code_share", "Posts with a code block", ratio, True),
    ("latin_share", "Latin characters", ratio, True),
    ("link_density", "Links per post", number, True),
    ("emoji_density", "Emoji per post", number, True),
    ("depth", "Depth score (z)", signed, True),
    ("dryness", "Dryness score (z)", signed, True),
]

POSITION = [
    ("partners", "Distinct partners in the graph", count, True),
    ("in_sources", "Channels referencing it", count, True),
    ("out_targets", "Channels it references", count, True),
    ("out_variety", "Outgoing variety (entropy)", number, True),
    ("reciprocity", "Reciprocated links", ratio, True),
    ("inside_share", "Links staying inside its cluster", ratio, True),
]

# `stability` is deliberately not in that table. It does not measure the
# channel, it measures how much the *clustering* can be believed about
# it — a percentile of it would rank channels by how confident the
# method happens to be. Below this it is a coin toss and the cluster
# sentence has to say so; clusters.py uses the same threshold when it
# prints how many labels to read twice.
STABLE_ENOUGH = 0.8


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def joined(parts: list[str]) -> str:
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def ordinal(value: float) -> str:
    rank = round(value)
    if 10 <= rank % 100 <= 20:
        return f"{rank}th"
    return f"{rank}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(rank % 10, 'th') }"


def percentile(frame: pd.DataFrame, column: str, value: Any) -> str:
    """Where `value` sits among every collected channel, as an ordinal."""
    if column not in frame or value is None or pd.isna(value):
        return "—"
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return "—"
    return ordinal(float((series < value).mean() * 100))


def row_for(frame: pd.DataFrame, username: str) -> pd.Series | None:
    if "username" not in frame:
        return None
    hit = frame[frame["username"].astype(str).str.lower() == username]
    return None if hit.empty else hit.iloc[0]


def table(
    frame: pd.DataFrame,
    row: pd.Series | None,
    metrics: list[tuple[str, str, Any, bool]],
    ranked: bool,
) -> list[str]:
    """One markdown table; `ranked` false blanks the percentile column."""
    if row is None:
        return ["_No row in the source sheet._", ""]
    out = ["| | Value | Percentile |", "|---|---:|---:|"]
    for column, label, render, rankable in metrics:
        if column not in row.index:
            continue
        value = row[column]
        rank = percentile(frame, column, value) if rankable and ranked else "—"
        out.append(f"| {label} | {render(value)} | {rank} |")
    out.append("")
    return out


def escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def snippet(text: str | None) -> str:
    if not text:
        return "_no text_"
    flat = escape(" ".join(text.split()))
    return flat if len(flat) <= SNIPPET else f"{flat[:SNIPPET].rstrip()}…"


def day(value: datetime | None) -> str:
    return "—" if value is None else value.date().isoformat()


def report(conn: psycopg.Connection[Any], username: str) -> list[str]:
    """The whole section for one channel, or a refusal."""
    found = conn.execute(CHANNEL, {"username": username}).fetchone()
    if found is None:
        return [f"## @{username}", "", "_Not in the inventory._", ""]
    tg_id, title, handle, status, kind = found
    if status != "seed":
        return [
            f"## @{handle}",
            "",
            "_No collected history — nothing to report._",
            "",
        ]

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
        (f"`{kind}` · {posts} posts collected, {day(first)} → {day(last)}"),
        "",
        "### Reach and engagement",
        "",
    ]
    lines += table(scorecard, score_row, REACH, ranked)

    lines += ["### How it writes", ""]
    lines += table(style, style_row, STYLE_METRICS, ranked)

    lines += ["### Position in the graph", ""]
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
    lines += table(merged, position_row, POSITION, ranked)

    if cluster_row is not None:
        group = groups[groups["cluster"] == cluster_row["cluster"]]
        if not group.empty:
            one = group.iloc[0]
            lines += [
                (
                    f"Clustering by who links to whom puts it in a group of "
                    f"**{int(one['size'])} channels**, mostly "
                    f"`{one['top_kind']}`, whose recurring words are "
                    f"_{one['name']}_."
                ),
                "",
            ]
        settled = cluster_row.get("stability")
        if settled is not None and not pd.isna(settled):
            if settled < STABLE_ENOUGH:
                lines += [
                    (
                        f"That placement is not settled: across repeated "
                        f"runs of the clustering it kept the same companions "
                        f"only {settled:.0%} of the time, against a typical "
                        f"channel's ~77%. It sits between crowds rather than "
                        f"inside one, so read the group as a hint about the "
                        f"neighbourhood and not as membership."
                    ),
                    "",
                ]
            else:
                lines += [
                    (
                        f"The placement is a solid one — it kept the same "
                        f"companions in {settled:.0%} of repeated runs."
                    ),
                    "",
                ]
    if bridge_row is not None:
        lines += [
            (
                f"Its strongest pull outside that group is toward the "
                f"_{bridge_row['leans_to']}_ crowd, which carries "
                f"{bridge_row['leans_share']:.0%} of its links."
            ),
            "",
        ]

    lines += ["### Who reposts and mentions it", ""]
    incoming = conn.execute(INCOMING, args).fetchall()
    if incoming:
        lines += [
            "| Channel | Reposts | Mentions | Last seen |",
            "|---|---:|---:|---|",
        ]
        for src_title, src_handle, forwards, mentions, seen in incoming:
            name = escape(src_title or "")
            link = (
                f"[{name}](https://t.me/{src_handle})" if src_handle else name
            )
            lines.append(f"| {link} | {forwards} | {mentions} | {day(seen)} |")
        lines.append("")
    else:
        lines += ["_Nothing in the collected set references it._", ""]

    lines += ["### Posts that travelled", ""]
    travelled = conn.execute(TRAVELLED, args).fetchall()
    if travelled:
        ids = [row[0] for row in travelled]
        texts = dict(conn.execute(TEXTS, {"id": tg_id, "ids": ids}).fetchall())
        lines += [
            "| Post | Published | Carried by | Channels |",
            "|---|---|---:|---:|",
        ]
        for msg_id, published, families, sources in travelled:
            link = f"https://t.me/{handle}/{msg_id}"
            lines.append(
                f"| [{snippet(texts.get(msg_id))}]({link}) "
                f"| {day(published)} | {families} | {sources} |"
            )
        lines.append("")
    else:
        lines += ["_No reposts of individual posts recorded._", ""]

    outgoing = conn.execute(OUTGOING, args).fetchall()
    if outgoing:
        parts = [
            f"{plural(refs, edge_kind)} to "
            f"{plural(targets, 'distinct channel')}"
            for edge_kind, refs, targets in outgoing
        ]
        lines += [
            (
                f"Pointing outward, it made {joined(parts)}. Those targets "
                f"are not listed: most of them are channels the inventory "
                f"has not reviewed yet."
            ),
            "",
        ]

    return lines


def main() -> None:
    usernames = [name.lstrip("@").lower() for name in sys.argv[1:]]
    if not usernames:
        raise SystemExit("usage: channel_report.py <username> [...]")

    lines = [
        f"# Channel report — @{usernames[0]}",
        "",
        (
            "Generated from an IT-media Telegram graph: a hand-reviewed set "
            "of channels whose history has been collected, and the reposts "
            "and mentions between them."
        ),
        "",
    ]

    with psycopg.connect(DSN) as conn:
        for username in usernames:
            lines += report(conn, username)

    lines += [
        "---",
        "",
        "### Reading these numbers",
        "",
        (
            "* **Percentiles are against the collected inventory**, not "
            "against Telegram. It is an IT-media sample, so a percentile "
            "says where a channel sits among broadly comparable channels."
        ),
        (
            "* **Every post metric is a single snapshot**, read whenever the "
            "backfill walked the channel, on posts of every age. Rates "
            "(reactions per view and so on) largely cancel that out; the raw "
            "medians do not, which is why they carry no percentile."
        ),
        (
            "* **The post count is collection depth, not output.** It is how "
            "far back the walk went, and it differs between channels."
        ),
        (
            "* **Reposts between channels of the same author are excluded** "
            "everywhere above. Self-distribution is not travel."
        ),
        (
            "* **Counterparts are only those inside the collected set.** "
            "Reposts from channels outside it exist and are not counted "
            "here."
        ),
        "",
    ]

    out = DATA / OUT_TEMPLATE.format(username=usernames[0])
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(lines)} lines -> {out}")


if __name__ == "__main__":
    main()
