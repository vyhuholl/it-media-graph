"""Pure reading of the seeds' featured lists, and the queue they make.

A list is ``channelSections.list`` trimmed to each section's channel
ids. Every section's ids are the featured channels, whatever its type,
which is not read (see ``yt/sections.py`` for why). Ids are kept in the
order YouTube gave them, each once, and an id of a shape no channel has
is dropped rather than trusted.

The queue counts, per channel, the distinct seeds featuring it — the one
number ``youtube-mention-graph`` allows per target — and orders the
channels outside the inventory by it, then by id. Featuring a channel on
one's own page is a deliberate recommendation, so one seed is enough by
default. Nothing returned here is ever stored, and the report shows no
data read from the API beyond channel ids.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from itgraph.yt_entries import CHANNEL_ID

__all__ = ["Ranked", "evidence", "featured_ids", "ranking", "report"]


def featured_ids(payload: Mapping[str, Any]) -> list[str]:
    """The channel ids a seed's sections list, in order, each once."""
    seen: dict[str, None] = {}
    items = payload.get("items")
    for item in items if isinstance(items, list) else ():
        details = (
            item.get("contentDetails") if isinstance(item, dict) else None
        )
        channels = (
            details.get("channels") if isinstance(details, dict) else None
        )
        for channel in channels if isinstance(channels, list) else ():
            if isinstance(channel, str) and CHANNEL_ID.match(channel):
                seen.setdefault(channel)
    return list(seen)


def evidence(lists: Mapping[str, Sequence[str]]) -> dict[str, frozenset[str]]:
    """Each featured channel, and the distinct seeds featuring it.

    A seed's own channel in its list is no evidence of anything.
    """
    seeds: dict[str, set[str]] = {}
    for seed, channels in lists.items():
        for channel in channels:
            if channel != seed:
                seeds.setdefault(channel, set()).add(seed)
    return {channel: frozenset(by) for channel, by in seeds.items()}


@dataclass(frozen=True, slots=True)
class Ranked:
    """One featured channel outside the inventory, and who features it."""

    channel_id: str
    seeds: frozenset[str]


def ranking(
    featured: Mapping[str, frozenset[str]],
    inventory: frozenset[str],
    *,
    min_seeds: int = 1,
) -> list[Ranked]:
    """The channels outside the inventory, most seeds first, then by id.

    A channel with a record in any status, gone included, is never
    ranked; nor one featured by fewer seeds than ``min_seeds``.
    """
    rows = [
        Ranked(channel_id=channel, seeds=seeds)
        for channel, seeds in featured.items()
        if channel not in inventory and len(seeds) >= min_seeds
    ]
    rows.sort(key=lambda row: (-len(row.seeds), row.channel_id))
    return rows


def _seeds(count: int) -> str:
    return f"{count} seed" + ("" if count == 1 else "s")


def report(
    *,
    lists: int,
    missing: int,
    ranked: Sequence[Ranked],
    top: int,
) -> list[str]:
    """The queue as lines a person reads: totals, the top, the rest."""
    lines = [f"read {lists} featured lists of seeds"]
    if missing:
        lines.append(
            f"{missing} seeds have no featured list in date — run "
            "`itgraph yt refresh`"
        )
    lines.append(
        f"featured channels outside the inventory, most seeds first "
        f"({len(ranked)} in all):"
    )
    shown = ranked[:top]
    for position, row in enumerate(shown, start=1):
        lines.append(
            f"  {position:>4}  {row.channel_id}  "
            f"{_seeds(len(row.seeds)):<9}  {' '.join(sorted(row.seeds))}"
        )
    if not shown:
        lines.append("  none")
    rest = ranked[len(shown) :]
    if rest:
        by_count = Counter(len(row.seeds) for row in rest)
        spread = ", ".join(
            f"{by_count[count]} with {_seeds(count)}"
            for count in sorted(by_count, reverse=True)
        )
        lines.append(f"  … and {len(rest)} more: {spread}")
    return lines
