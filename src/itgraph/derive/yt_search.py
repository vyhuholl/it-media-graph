"""Pure parts of search: the operator's queries, and the queue results make.

Queries come one a line. Blank lines and lines beginning with ``#`` are
skipped, and nothing else is a comment: "C# tutorial" is a query. A query
is trimmed and its whitespace collapsed, letter case kept — YouTube
matches case-insensitively, and the operator's spelling is the record —
and one given twice is asked once.

The queue takes the channels in the results in date that have no record,
round-robin across runs in the order they were made: each run's first
result, then each run's second, and so on, a channel already taken
skipped. No count orders or cuts it: the order is YouTube's own, as
YouTube returned it, and taking the runs in turn lets no query crowd out
the others. "How many queries returned a channel" would be a new number
over API results, and is not worked out. Nothing here is stored.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "Ranked",
    "Run",
    "RunLine",
    "normalize_query",
    "ranking",
    "read_queries",
    "report",
]


def normalize_query(text: str) -> str:
    """A query as it is stored: trimmed, whitespace collapsed, case kept."""
    return " ".join(text.split())


def read_queries(lines: Iterable[str]) -> list[str]:
    """Queries from lines, in order, each once; blanks and ``#`` lines out."""
    seen: dict[str, None] = {}
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        if query := normalize_query(line):
            seen.setdefault(query)
    return list(seen)


@dataclass(frozen=True, slots=True)
class Run:
    """What the queue needs of one run: its query and channels in order."""

    query: str
    channels: Sequence[str]


@dataclass(frozen=True, slots=True)
class Ranked:
    """One channel outside the inventory, and the query that found it first."""

    channel_id: str
    query: str


def ranking(runs: Sequence[Run], inventory: frozenset[str]) -> list[Ranked]:
    """Channels new to the inventory, each run's results taken in turn."""
    taken: dict[str, str] = {}
    depth = max((len(run.channels) for run in runs), default=0)
    for position in range(depth):
        for run in runs:
            if position >= len(run.channels):
                continue
            channel = run.channels[position]
            if channel not in inventory and channel not in taken:
                taken[channel] = run.query
    return [Ranked(channel, query) for channel, query in taken.items()]


@dataclass(frozen=True, slots=True)
class RunLine:
    """One run as the report lists it."""

    query: str
    kind: str
    asked: str
    results: int
    new: int


def report(
    runs: Sequence[RunLine], ranked: Sequence[Ranked], *, top: int
) -> list[str]:
    """The queue as lines a person reads: runs, the top, the rest.

    Channel ids and the operator's queries — no title or handle.
    """
    lines = [f"searches in date: {len(runs)}"]
    lines.extend(
        f"  {run.asked}  {run.kind:<7}  {run.results:>3} results, "
        f"{run.new:>3} new  {run.query}"
        for run in runs
    )
    lines.append(
        f"channels outside the inventory, in YouTube's order across "
        f"queries ({len(ranked)} in all):"
    )
    shown = ranked[:top]
    lines.extend(
        f"  {position:>4}  {row.channel_id}  {row.query}"
        for position, row in enumerate(shown, start=1)
    )
    if not shown:
        lines.append("  none")
    if len(ranked) > len(shown):
        lines.append(f"  … and {len(ranked) - len(shown)} more")
    return lines
