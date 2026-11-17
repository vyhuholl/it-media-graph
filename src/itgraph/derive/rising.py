"""Which Telegram channels and names are rising, and in what order.

Rising means newly referred to by several seed families at once: at least
``min_families`` distinct families of seeds that are not chats referred to
it — by forward or by mention — within the trailing window. A family is
the key ``tg_channel_families`` gives a seed, or the seed itself. The
candidate's own family does not count: an author naming their own new
channel is not evidence that anyone else noticed it.

Two kinds of evidence. A reference to a channel the inventory holds is an
edge, with the publication time of the message that made it. A mention of
a name it does not hold is a pending source, with the first and the last
time that seed mentioned it — so a source is in the window when its last
time is, and before it when its first time is. A source without times was
stored before they existed and is no evidence either way until the next
derivation fills it.

Pure: plain rows and a moment in, the ranking out. Nothing it returns is
ever stored; ``itgraph rising`` prints it and the poll loop resolves its
head. The report prints channels with a username and nothing else by
name: a pending name, or a channel known only by id, is counted.
"""

import enum
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from itgraph.db.affiliation import family_of
from itgraph.db.models import CollectionCommand

__all__ = [
    "RESOLVING_COMMANDS",
    "ChannelTarget",
    "LeftOut",
    "NameSource",
    "PendingName",
    "Ranking",
    "Reference",
    "Rising",
    "RisingRows",
    "rank_rising",
    "report",
]

# The commands that resolve, in the order the report names them.
RESOLVING_COMMANDS = (
    CollectionCommand.RESOLVE,
    CollectionCommand.ADD,
    CollectionCommand.WATCH,
)


class LeftOut(enum.StrEnum):
    """Why a rising candidate is not in the ranking."""

    CHAT = "chat"
    NO_USERNAME = "no username"
    NOT_CHANNEL = "not a channel"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChannelTarget:
    """A channel in ``candidate`` or ``maybe``, and where its identity stands.

    ``attempts`` and ``not_channel`` are the resolution's: a channel never
    resolved and never tried awaits an identity; one tried and not
    resolved found a user, a bot or nothing.
    """

    tg_id: int
    username: str | None
    title: str | None
    is_chat: bool
    resolved: bool
    attempts: int = 0
    not_channel: bool = False


@dataclass(frozen=True, slots=True)
class Reference:
    """One edge from a seed to a channel target, by its message's time."""

    src: int
    dst: int
    at: datetime


@dataclass(frozen=True, slots=True)
class PendingName:
    """A pending username no channel answers to yet, and its attempts."""

    username: str
    attempts: int = 0
    not_channel: bool = False


@dataclass(frozen=True, slots=True)
class NameSource:
    """A seed mentioning a pending name: its first and last time."""

    username: str
    src: int
    first: datetime | None
    last: datetime | None


@dataclass(frozen=True, slots=True)
class RisingRows:
    """Everything the ranking reads, as the loader read it.

    ``seeds`` are the seeds that are not chats; ``families`` the keys of
    the channels confirmed in a family, seeds and targets alike.
    """

    seeds: frozenset[int]
    families: Mapping[int, int]
    channels: Mapping[int, ChannelTarget]
    references: Sequence[Reference]
    names: Mapping[str, PendingName]
    sources: Sequence[NameSource]


@dataclass(frozen=True, slots=True)
class Rising:
    """One rising candidate: a channel by id, or a pending name.

    ``username`` is the channel's, or the pending name itself — which is
    never printed, since it may be a person's. ``first_in_window`` is a
    channel's first reference inside the window; a name's sources keep no
    such time.
    """

    tg_id: int | None
    username: str | None
    title: str | None
    families: int
    families_before: int
    first_in_window: datetime | None

    @property
    def is_name(self) -> bool:
        return self.tg_id is None


@dataclass(slots=True)
class Ranking:
    """The ranking, what awaits an identity, and what was left out.

    ``ranked`` holds the channels with a username, in rank order;
    ``awaiting`` the channels never resolved and the names never tried,
    in rank order too — the loop takes the first.
    """

    ranked: list[Rising] = field(default_factory=list)
    awaiting: list[Rising] = field(default_factory=list)
    left_out: Counter[LeftOut] = field(default_factory=Counter)


@dataclass(slots=True)
class _Evidence:
    """The families behind one target, gathered before it is judged."""

    within: set[int] = field(default_factory=set)
    before: set[int] = field(default_factory=set)
    first_in_window: datetime | None = None

    def saw(self, family: int, at: datetime, start: datetime) -> None:
        if at < start:
            self.before.add(family)
            return
        self.within.add(family)
        if self.first_in_window is None or at < self.first_in_window:
            self.first_in_window = at


def rank_rising(
    rows: RisingRows,
    moment: datetime,
    *,
    window_days: int,
    min_families: int,
) -> Ranking:
    """Rank what is rising as of ``moment``.

    Most families in the window first; a tie to the one fewer families
    referred to before the window, as the newer; then a fixed order —
    channels by id before names by name — so the same rows always give
    the same ranking. References after ``moment`` are not read.
    """
    start = moment - timedelta(days=window_days)
    rising: list[
        tuple[tuple[int, int, int, int, str], Rising, _Standing | LeftOut]
    ] = []

    for tg_id, evidence in _channel_evidence(rows, start, moment).items():
        if len(evidence.within) < min_families:
            continue
        target = rows.channels[tg_id]
        candidate = Rising(
            tg_id=tg_id,
            username=target.username,
            title=target.title,
            families=len(evidence.within),
            families_before=len(evidence.before),
            first_in_window=evidence.first_in_window,
        )
        key = (-candidate.families, candidate.families_before, 0, tg_id, "")
        rising.append((key, candidate, _channel_standing(target)))

    for name, evidence in _name_evidence(rows, start, moment).items():
        if len(evidence.within) < min_families:
            continue
        candidate = Rising(
            tg_id=None,
            username=name,
            title=None,
            families=len(evidence.within),
            families_before=len(evidence.before),
            first_in_window=None,
        )
        key = (-candidate.families, candidate.families_before, 1, 0, name)
        rising.append((key, candidate, _name_standing(rows.names[name])))

    ranking = Ranking()
    for _, candidate, standing in sorted(rising, key=lambda item: item[0]):
        if standing is _Standing.RANKED:
            ranking.ranked.append(candidate)
        elif standing is _Standing.AWAITING:
            ranking.awaiting.append(candidate)
        else:
            ranking.left_out[standing] += 1
    return ranking


class _Standing(enum.Enum):
    """Where a rising candidate stands when it is not left out."""

    RANKED = enum.auto()
    AWAITING = enum.auto()


def _channel_standing(target: ChannelTarget) -> _Standing | LeftOut:
    if target.is_chat:
        return LeftOut.CHAT
    if target.username:
        return _Standing.RANKED
    if target.resolved:
        return LeftOut.NO_USERNAME
    if target.attempts:
        return LeftOut.NOT_CHANNEL if target.not_channel else LeftOut.FAILED
    return _Standing.AWAITING


def _name_standing(name: PendingName) -> _Standing | LeftOut:
    if name.attempts:
        return LeftOut.NOT_CHANNEL if name.not_channel else LeftOut.FAILED
    return _Standing.AWAITING


def _channel_evidence(
    rows: RisingRows, start: datetime, moment: datetime
) -> dict[int, _Evidence]:
    gathered: dict[int, _Evidence] = {}
    for reference in rows.references:
        if reference.src not in rows.seeds or reference.at > moment:
            continue
        if reference.dst not in rows.channels:
            continue
        family = family_of(rows.families, reference.src)
        # A target outside any family has no key in `families`, so only a
        # confirmed family of its own can exclude a seed.
        if family == rows.families.get(reference.dst):
            continue
        evidence = gathered.setdefault(reference.dst, _Evidence())
        evidence.saw(family, reference.at, start)
    return gathered


def _name_evidence(
    rows: RisingRows, start: datetime, moment: datetime
) -> dict[str, _Evidence]:
    """A name's families: in the window by a source's last time, before it
    by its first. A source without times is no evidence yet."""
    gathered: dict[str, _Evidence] = {}
    for source in rows.sources:
        first, last = source.first, source.last
        if first is None or last is None or first > moment:
            continue
        if source.src not in rows.seeds or source.username not in rows.names:
            continue
        family = family_of(rows.families, source.src)
        evidence = gathered.setdefault(source.username, _Evidence())
        if first < start:
            evidence.before.add(family)
        if last >= start:
            evidence.within.add(family)
    return gathered


def report(
    ranking: Ranking,
    journal: Mapping[CollectionCommand, int],
    *,
    window_days: int,
    min_families: int,
    cap: int,
) -> list[str]:
    """The lines ``itgraph rising`` prints.

    The ranked channels with their usernames and titles; then counts
    alone — what awaits a resolution, what was left out and why, and the
    journal's last 24 hours against the cap. Never a pending name, and
    never the id of a channel awaiting one.
    """
    lines = [
        (
            f"rising: referred to by {min_families}+ seed families "
            f"in the last {window_days} days"
        )
    ]
    if not ranking.ranked:
        lines.append("  no channel with a username is rising")
    for rank, item in enumerate(ranking.ranked, start=1):
        first = (
            f"{item.first_in_window:%Y-%m-%d %H:%M} UTC"
            if item.first_in_window is not None
            else "unknown"
        )
        lines.append(
            f"{rank:>3}. @{item.username}  {item.title or '(no title)'}"
            f" — {item.families} families in the window, "
            f"{item.families_before} before; first in the window {first}"
        )
    lines.append(f"awaiting a resolution: {len(ranking.awaiting)}")
    left_out = ", ".join(
        f"{ranking.left_out[reason]} {reason}" for reason in LeftOut
    )
    lines.append(f"left out: {left_out}")
    spent = sum(journal.get(command, 0) for command in RESOLVING_COMMANDS)
    by_command = ", ".join(
        f"{command} {journal[command]}"
        for command in RESOLVING_COMMANDS
        if journal.get(command)
    )
    lines.append(
        f"resolutions in the last 24 hours: {spent} of {cap}"
        + (f" — {by_command}" if by_command else "")
    )
    return lines
