"""The review file: what ``yt review-export`` writes and ``review-apply`` reads.

Pure: rendering and parsing, no database. A tab-separated file meant for a
text editor on the operator's own machine, carried over SSH — so the
editable columns come first and line up before the long title, and every
mistake in it is found at once, with its line number, before anything is
written.

The file holds YouTube API data (titles, handles), which may be kept at
most 30 days. Its header says when it was exported and when it expires,
and a file past that is refused rather than applied. Deleting it is the
operator's part; the header says that too.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)
from itgraph.yt_policy import RETENTION

__all__ = [
    "COLUMNS",
    "Decision",
    "ExportRow",
    "ParsedReview",
    "ReviewFileError",
    "RowProblem",
    "UnreadableReviewFile",
    "export_row",
    "parse_review",
    "render_review",
]

DECISIONS: Mapping[str, ChannelStatus] = {
    "seed": ChannelStatus.SEED,
    "maybe": ChannelStatus.MAYBE,
    "reject": ChannelStatus.REJECTED,
}
DECISION_OF = {status: word for word, status in DECISIONS.items()}

EDITABLE = ("decision", "kind", "lang", "reason", "note")
READ_ONLY = ("handle", "title", "subscribers", "declared", "link")
COLUMNS = ("channel_id", *EDITABLE, *READ_ONLY)

GUIDE = 'src/itgraph/README.md, "YouTube: разметка"'

_EXPORTED = "# exported: "
_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_FLATTEN = re.compile(r"[\t\r\n]+")


def _flat(value: str) -> str:
    """A value that cannot break a row: tabs and newlines become spaces."""
    return _FLATTEN.sub(" ", value).strip()


@dataclass(frozen=True, slots=True)
class ExportRow:
    """One channel as the file shows it: current review, then what
    YouTube said."""

    channel_id: str
    decision: str = ""
    kind: str = ""
    lang: str = ""
    reason: str = ""
    note: str = ""
    handle: str = ""
    title: str = ""
    subscribers: str = ""
    declared: str = ""
    link: str = ""


def render_review(rows: Iterable[ExportRow], *, exported_at: datetime) -> str:
    """The whole file: header, column line, one line per channel."""
    expires = exported_at + RETENTION
    kinds = " ".join(kind.value for kind in YtChannelKind)
    langs = " ".join(lang.value for lang in YtLang)
    reasons = " ".join(reason.value for reason in YtRejectReason)
    header = [
        "# itgraph yt review-export",
        f"{_EXPORTED}{exported_at.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}",
        (
            f"# expires:  {expires:%Y-%m-%d} — holds YouTube API data: "
            "delete it once applied, never commit it"
        ),
        "# decision: seed | maybe | reject | (empty: leave undecided)",
        f"# kind:     {kinds}   (seed: required)",
        f"# lang:     {langs}   (seed: required)",
        f"# reason:   {reasons}   (reject: required)",
        f"# guide:    {GUIDE}",
        "\t".join(COLUMNS),
    ]
    lines = [
        "\t".join(_flat(getattr(row, column)) for column in COLUMNS)
        for row in rows
    ]
    return "\n".join(header + lines) + "\n"


@dataclass(frozen=True, slots=True)
class Decision:
    """One row's decision, validated."""

    where: str
    channel_id: str
    status: ChannelStatus
    kind: YtChannelKind | None
    lang: YtLang | None
    reason: YtRejectReason | None
    note: str | None


@dataclass(frozen=True, slots=True)
class RowProblem:
    where: str
    problem: str

    def __str__(self) -> str:
        return f"{self.where}: {self.problem}"


class UnreadableReviewFile(ValueError):
    """The file as a whole cannot be applied: no header, or expired."""


class ReviewFileError(ValueError):
    """Rows that cannot be applied, every one of them, reported together.

    Nothing is written while any row is wrong — the rows without mistakes
    included. A file fixed in three places should take one round trip to
    find all three.
    """

    def __init__(self, problems: Sequence[RowProblem]) -> None:
        listed = "\n  ".join(str(problem) for problem in problems)
        super().__init__(
            f"{len(problems)} problem{'' if len(problems) == 1 else 's'} in "
            f"the review file, nothing written:\n  {listed}"
        )
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class ParsedReview:
    """A file read and checked as far as a file can be without a database.

    ``problems`` is not raised here: the caller adds what only the
    database knows — channels that do not exist — and reports all of
    them at once.
    """

    exported_at: datetime
    decisions: Sequence[Decision]
    skipped: int
    problems: Sequence[RowProblem]


def _choice[Value: StrEnum](
    text: str, choices: type[Value], what: str
) -> tuple[Value | None, str | None]:
    """A value from an enum, matched case-insensitively, or why not."""
    if not text:
        return None, None
    by_word = {choice.value: choice for choice in choices}
    found = by_word.get(text.lower())
    if found is None:
        return None, f"{what} {text!r} is not one of: {' '.join(by_word)}"
    return found, None


def _exported_at(lines: Sequence[str]) -> datetime:
    for line in lines:
        if line.startswith(_EXPORTED):
            try:
                return datetime.fromisoformat(
                    line.removeprefix(_EXPORTED).strip()
                )
            except ValueError as exc:
                raise UnreadableReviewFile(
                    "the export moment in the header cannot be read"
                ) from exc
    raise UnreadableReviewFile(
        "the header with the export moment is missing, so neither the "
        "file's age nor what changed since it was exported can be told; "
        "export a new file"
    )


def parse_review(text: str, *, now: datetime) -> ParsedReview:
    """Read a review file. Raises ``UnreadableReviewFile`` for the file as
    a whole; row problems come back in the result."""
    lines = text.splitlines()
    exported_at = _exported_at(lines)
    if now > exported_at + RETENTION:
        raise UnreadableReviewFile(
            f"exported {exported_at:%Y-%m-%d}, more than "
            f"{RETENTION.days} days ago: it holds YouTube API data that "
            "may not be kept that long. Delete it and export a new one"
        )

    body = [
        (number, line)
        for number, line in enumerate(lines, start=1)
        if line.strip() and not line.startswith("#")
    ]
    if not body or body[0][1].rstrip("\t") != "\t".join(COLUMNS):
        # The one way an editor breaks this file without anyone touching a
        # column: converting tabs to spaces on save. Said as itself,
        # because "changed" sends the reader looking for an edit they
        # never made.
        if body and body[0][1].split() == list(COLUMNS):
            raise UnreadableReviewFile(
                "the columns are separated by spaces, not tabs — the editor "
                "converted them on save. Turn that off for .tsv files and "
                "export a new file"
            )
        raise UnreadableReviewFile(
            "the column line is missing or changed; columns are separated "
            "by tabs. Export a new file rather than rearranging the columns"
        )

    decisions: list[Decision] = []
    problems: list[RowProblem] = []
    seen: dict[str, str] = {}
    skipped = 0
    for number, line in body[1:]:
        where = f"line {number}"
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) > len(COLUMNS):
            problems.append(
                RowProblem(
                    where,
                    f"{len(fields)} fields where there are {len(COLUMNS)} "
                    "columns — a tab inside a value?",
                )
            )
            continue
        fields += [""] * (len(COLUMNS) - len(fields))
        row = dict(zip(COLUMNS, fields, strict=True))

        channel_id = row["channel_id"]
        if not _CHANNEL_ID.match(channel_id):
            problems.append(
                RowProblem(where, f"{channel_id!r} is not a channel id")
            )
            continue
        if channel_id in seen:
            problems.append(
                RowProblem(
                    where,
                    f"{channel_id} is listed twice (also {seen[channel_id]})",
                )
            )
            continue
        seen[channel_id] = where

        decision = _row_decision(where, channel_id, row, problems)
        if decision is None:
            if not any(row[column] for column in EDITABLE):
                skipped += 1
            continue
        decisions.append(decision)

    return ParsedReview(
        exported_at=exported_at,
        decisions=decisions,
        skipped=skipped,
        problems=problems,
    )


def _row_decision(
    where: str,
    channel_id: str,
    row: Mapping[str, str],
    problems: list[RowProblem],
) -> Decision | None:
    """One row's decision, or ``None`` with its problems recorded.

    A row with no decision but something in the other editable columns is
    a problem rather than a skip: that is a decision forgotten, not one
    deferred, and applying the rest of the file would hide it.
    """
    found: list[str] = []
    word = row["decision"].lower()
    status = DECISIONS.get(word)
    if word and status is None:
        found.append(
            f"decision {row['decision']!r} is not one of: "
            f"{' '.join(DECISIONS)}"
        )
    kind, kind_error = _choice(row["kind"], YtChannelKind, "kind")
    lang, lang_error = _choice(row["lang"], YtLang, "lang")
    reason, reason_error = _choice(row["reason"], YtRejectReason, "reason")
    found += [e for e in (kind_error, lang_error, reason_error) if e]

    if not word:
        if any(row[column] for column in EDITABLE):
            found.append(
                "kind, lang, reason or note filled in with no decision — "
                "give a decision, or clear the row"
            )
    elif status is ChannelStatus.SEED:
        if not row["kind"]:
            found.append("a seed needs a kind")
        if not row["lang"]:
            found.append("a seed needs a lang")
    if status is ChannelStatus.REJECTED and not row["reason"]:
        found.append("a rejection needs a reason")
    if (
        status is not None
        and status is not ChannelStatus.REJECTED
        and row["reason"]
    ):
        found.append("a reason belongs only to a rejection")

    if found:
        problems.extend(RowProblem(where, problem) for problem in found)
        return None
    if status is None:
        return None
    return Decision(
        where=where,
        channel_id=channel_id,
        status=status,
        kind=kind,
        lang=lang,
        reason=reason,
        note=row["note"] or None,
    )


def export_row(
    channel: YtChannel, payload: Mapping[str, Any] | None
) -> ExportRow:
    """One record as the file shows it: its review, then what YouTube said.

    A channel without stored data — expired, or gone — still gets its row,
    with the id and the link and nothing YouTube said: the link is how a
    reviewer looks at it anyway.
    """
    rejected = channel.status is ChannelStatus.REJECTED
    review = ExportRow(
        channel_id=channel.channel_id,
        decision=DECISION_OF.get(channel.status, ""),
        kind=channel.kind.value if channel.kind else "",
        lang=channel.lang.value if channel.lang else "",
        reason=channel.reject_reason.value if channel.reject_reason else "",
        note=(channel.reject_note if rejected else channel.kind_note) or "",
        link=f"https://www.youtube.com/channel/{channel.channel_id}",
    )
    if payload is None:
        return review

    snippet: Mapping[str, Any] = payload.get("snippet") or {}
    statistics: Mapping[str, Any] = payload.get("statistics") or {}
    subscribers = (
        "hidden"
        if statistics.get("hiddenSubscriberCount")
        else str(statistics.get("subscriberCount", ""))
    )
    declared = f"{snippet.get('defaultLanguage') or '-'}/{snippet.get('country') or '-'}"
    return replace(
        review,
        handle=str(snippet.get("customUrl", "")),
        title=str(snippet.get("title", "")),
        subscribers=subscribers,
        declared=declared,
    )
