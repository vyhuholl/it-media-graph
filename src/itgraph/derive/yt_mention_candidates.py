"""Pure reading of the mention queue: which channel each reference names.

The mention graph (``derive/yt_references.py``) leaves a handle, a
legacy name or a video it cannot name without the network as a pending
target. With the lookups in date, each of those names a channel, names
none, or still awaits one. A channel id is its own answer, and a
reference to a known target names its channel already.

Evidence here is the seeds' *videos* only: a featured list has a queue
of its own (``derive/yt_featured.py``), with its own threshold, and
counting it again here would let one seed's shelf count twice. The unit
is the distinct seed, in any form, after lookups: a handle from one seed
and a video from another are one channel with two seeds — which is why
everything is looked up before anything is cut. A reference that turns
out to name its own channel is dropped: a seed's link to one of its own
videos older than anything stored looks foreign until it is looked up.

The count of distinct seeds is the one number worked out per channel
(``youtube-mention-graph``, "No Measure Over The Graph"), and it orders
and cuts the review queue and nothing else. Nothing here is stored, and
the report shows no data read from the API beyond channel ids.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from itgraph.derive.tg_youtube import Name, NameKind
from itgraph.derive.yt_references import Pending, PendingKind, References

__all__ = ["Naming", "Ranked", "name_channels", "ranking", "report"]

# A lookup's key as the lookups table holds it: its kind's value and the
# name. Plain strings, so nothing here imports the database's types.
LookupKey = tuple[str, str]

# The pending kinds a lookup answers. A channel id answers itself.
_LOOKED_UP = {
    PendingKind.HANDLE: NameKind.HANDLE,
    PendingKind.LEGACY: NameKind.LEGACY,
    PendingKind.VIDEO: NameKind.VIDEO,
}


@dataclass(slots=True)
class Naming:
    """The channels the seeds' videos name, and what could not be named.

    ``channels`` maps each named channel to the distinct seeds whose
    videos reference it. ``awaiting`` maps each reference still awaiting
    a lookup to its seeds; ``not_found`` counts those looked up as naming
    no channel; ``own`` counts references dropped because a lookup named
    the referencing seed itself.
    """

    channels: dict[str, set[str]] = field(default_factory=dict)
    awaiting: dict[Pending, frozenset[str]] = field(default_factory=dict)
    not_found: int = 0
    own: int = 0

    def to_look_up(self) -> list[Name]:
        """What awaits a lookup, most seeds first, then by kind and name."""
        return [
            Name(_LOOKED_UP[target.kind], target.value)
            for target in sorted(
                self.awaiting,
                key=lambda target: (
                    -len(self.awaiting[target]),
                    target.kind.value,
                    target.value,
                ),
            )
        ]


def name_channels(
    references: References, lookups: Mapping[LookupKey, str | None]
) -> Naming:
    """Every video-level reference, named through what is stored."""
    naming = Naming()

    def credit(channel: str, seeds: set[str]) -> None:
        for seed in seeds:
            if seed == channel:
                naming.own += 1
                continue
            naming.channels.setdefault(channel, set()).add(seed)

    for edge in references.edges:
        if edge.video_id is not None:
            credit(edge.target_channel, {edge.channel_id})

    for target, evidence in references.pending.items():
        seeds = set(evidence.linking)
        if not seeds:
            continue  # named by featured lists alone
        if target.kind is PendingKind.CHANNEL:
            credit(target.value, seeds)
            continue
        key = (_LOOKED_UP[target.kind].value, target.value)
        if key not in lookups:
            naming.awaiting[target] = frozenset(seeds)
        elif (channel := lookups[key]) is None:
            naming.not_found += 1
        else:
            credit(channel, seeds)
    return naming


@dataclass(frozen=True, slots=True)
class Ranked:
    """One channel outside the inventory, and the seeds referencing it."""

    channel_id: str
    seeds: frozenset[str]


def ranking(
    naming: Naming, inventory: frozenset[str], *, min_seeds: int = 2
) -> list[Ranked]:
    """The channels outside the inventory, most seeds first, then by id.

    A channel with a record in any status, gone included, is never
    ranked; nor one referenced by fewer seeds than ``min_seeds``.
    """
    rows = [
        Ranked(channel_id=channel, seeds=frozenset(seeds))
        for channel, seeds in naming.channels.items()
        if channel not in inventory and len(seeds) >= min_seeds
    ]
    rows.sort(key=lambda row: (-len(row.seeds), row.channel_id))
    return rows


def _seeds(count: int) -> str:
    return f"{count} seed" + ("" if count == 1 else "s")


def report(
    references: References,
    naming: Naming,
    ranked: Sequence[Ranked],
    *,
    top: int,
) -> list[str]:
    """The queue as lines a person reads. Nothing in it is API data."""
    lines = [
        (
            f"read {references.videos} videos of seeds: "
            f"{len(naming.channels)} channels named, "
            f"{naming.own} references to their own channel dropped"
        ),
        (
            "ranked outside the inventory, most seeds first "
            f"({len(ranked)} in all):"
        ),
    ]
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
    lines.append(
        f"awaiting a lookup: {len(naming.awaiting)} references"
        + (" — run `itgraph yt mention-lookup`" if naming.awaiting else "")
    )
    lines.append(f"looked up, no channel: {naming.not_found} references")
    return lines
