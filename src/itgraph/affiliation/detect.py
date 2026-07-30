"""Merging the four signals into one ranked list of candidate pairs.

The union of what the signals found, not the intersection. One signal is
enough to propose a pair, and that is a measurement rather than a
preference: on the corpus this was built for, mutual description
references and shared username tokens overlap in **zero** pairs, mutual
descriptions and mutual edges in zero, concentration and shared tokens in
6 of 19. A rule demanding two independent signals before proposing
anything would propose almost nothing.

So the score orders a reading list, it does not measure confidence.
Nothing here decides that two channels share an author — the ranking
exists to spend a human's attention well, and `db/channels.py` is the
only place a family is ever recorded.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from itgraph.affiliation.signals import (
    EdgeCounts,
    Pair,
    Thresholds,
    Weights,
    description_references,
    mutual_density,
    ordered,
    outgoing_concentration,
    shared_username_tokens,
)
from itgraph.db.models import AboutDirection

__all__ = [
    "Candidate",
    "Detection",
    "InvalidParameterError",
    "Inventory",
    "detect",
    "normalize_pair",
    "validate_parameters",
]


class InvalidParameterError(ValueError):
    """A threshold or weight outside the range it can mean anything in."""


@dataclass(frozen=True, slots=True)
class Inventory:
    """Everything detection reads, already in memory.

    504 channels is 127 000 pairs, so nothing here is asked about the
    pair space: each signal *emits* the pairs it found. The corpus this
    was built for — 15 651 edges, 500 usernames, 195 descriptions — fits
    in memory by a wide margin, which is why the signals are plain
    Python over rows fetched once rather than SQL, and why every one of
    them is testable without a database.
    """

    # Normalized username per channel; a channel without one is absent.
    usernames: Mapping[int, str]
    # Stored `about` text per channel; a channel without one is absent.
    descriptions: Mapping[int, str]
    # (source, destination) -> edge count, already filtered to the edge
    # kinds this run counts.
    edges: EdgeCounts
    # Every channel id the inventory holds.
    known_channels: frozenset[int]
    # A discussion chat -> the channel it belongs to. Already a recorded
    # relationship, so pairing the two would be noise.
    linked_to: Mapping[int, int]
    # Channel -> the family key it already has. Two channels already in
    # one family have nothing left to decide.
    family_of: Mapping[int, int]


@dataclass(slots=True)
class Candidate:
    """One proposed pair, its score, and the evidence behind it.

    Mutable, and deliberately so: it is filled in one signal at a time.
    Each signal owns a disjoint group of the evidence fields, so they
    accumulate without ever overwriting each other, and the score adds up
    as they arrive.
    """

    pair: Pair
    score: float = 0.0

    about_direction: AboutDirection | None = None
    shared_token: str | None = None
    shared_token_channels: int | None = None
    out_share: float | None = None
    out_share_edges: int | None = None
    out_share_src: int | None = None
    edges_a_to_b: int | None = None
    edges_b_to_a: int | None = None


@dataclass(frozen=True, slots=True)
class Detection:
    """A whole run: what it proposed, and what it could see.

    The coverage travels with the candidates because a short list means
    two different things depending on it. 302 of 504 seeds have no stored
    description, so the description signal's silence is mostly ignorance
    rather than evidence of no affiliation.
    """

    candidates: list[Candidate]
    channels_scored: int
    with_description: int
    refs_outside_inventory: int


def validate_parameters(
    thresholds: Thresholds, weights: Weights, edge_kinds: list[str]
) -> None:
    """Refuse a parameter that cannot mean anything, before any work.

    A share is a ratio and lives in ``[0, 1]``; a minimum below one
    admits everything; a negative weight would rank a signal's absence
    above its presence. Each is named in the error, because a run that
    fails silently on a typo is worse than one that never started.
    """
    if not 0.0 <= thresholds.max_share_min <= 1.0:
        raise InvalidParameterError(
            f"max_share_min must be between 0 and 1, "
            f"got {thresholds.max_share_min}"
        )
    for name in ("min_out_edges", "min_token_length", "min_mutual_edges"):
        value = getattr(thresholds, name)
        if value < 1:
            raise InvalidParameterError(
                f"{name} must be at least 1, got {value}"
            )
    if thresholds.max_token_channels < 2:
        raise InvalidParameterError(
            "max_token_channels must be at least 2 — a token on one channel "
            f"is shared with nobody, got {thresholds.max_token_channels}"
        )
    for name in ("about", "share", "token", "mutual"):
        value = getattr(weights, name)
        if value < 0:
            raise InvalidParameterError(
                f"weight for {name} must not be negative, got {value}"
            )
    if not edge_kinds:
        raise InvalidParameterError("at least one edge kind must be counted")


def detect(
    inventory: Inventory,
    *,
    thresholds: Thresholds,
    weights: Weights,
) -> Detection:
    """Every pair any signal proposes, scored and ranked.

    Signals contribute independently: a pair reached by one appears, a
    pair reached by three ranks above it. Each pair is normalized to
    ``(min, max)`` before merging, so two signals arriving from opposite
    directions produce one candidate rather than two mirror images.
    """
    merged: dict[Pair, Candidate] = {}

    def entry_for(pair: Pair) -> Candidate:
        return merged.setdefault(pair, Candidate(pair=pair))

    about = description_references(
        inventory.descriptions,
        channel_by_username={
            username: channel_id
            for channel_id, username in inventory.usernames.items()
        },
        known_channels=inventory.known_channels,
    )
    for about_signal in about.signals:
        entry = entry_for(about_signal.pair)
        entry.score += weights.about * about_signal.strength
        entry.about_direction = about_signal.direction

    for token_signal in shared_username_tokens(
        inventory.usernames, thresholds=thresholds
    ):
        entry = entry_for(token_signal.pair)
        entry.score += weights.token * token_signal.strength
        entry.shared_token = token_signal.token
        entry.shared_token_channels = token_signal.token_channels

    for share_signal in outgoing_concentration(
        inventory.edges, thresholds=thresholds
    ):
        entry = entry_for(share_signal.pair)
        entry.score += weights.share * share_signal.strength
        entry.out_share = share_signal.share
        entry.out_share_edges = share_signal.edges
        entry.out_share_src = share_signal.src

    for mutual_signal in mutual_density(
        inventory.edges, thresholds=thresholds
    ):
        entry = entry_for(mutual_signal.pair)
        entry.score += weights.mutual * mutual_signal.strength
        entry.edges_a_to_b = mutual_signal.edges_a_to_b
        entry.edges_b_to_a = mutual_signal.edges_b_to_a

    candidates = [
        candidate
        for pair, candidate in merged.items()
        if _worth_proposing(pair, inventory)
    ]
    # Ties break on the pair itself, so a run is reproducible rather than
    # dependent on which signal happened to insert first.
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.pair))
    return Detection(
        candidates=candidates,
        channels_scored=len(inventory.known_channels),
        with_description=len(inventory.descriptions),
        refs_outside_inventory=about.refs_outside_inventory,
    )


def _worth_proposing(pair: Pair, inventory: Inventory) -> bool:
    """Whether this pair is a question anyone still needs answering.

    A channel with itself is not a pair — the signals cannot produce one,
    but a caller assembling an ``Inventory`` by hand can, and the
    ordering helper would silently accept it.

    A discussion chat and its parent are already a recorded relationship
    (``linked_to``), so proposing them as an affiliation is noise the
    operator would have to dismiss once per run forever.

    Two channels already in one family have nothing left to decide.
    Pairs already *confirmed or rejected* are excluded too, but not here:
    that filter belongs to the read, because the row must stay
    inspectable after the decision rather than vanish from the table.
    """
    first, second = pair
    if first == second:
        return False
    if inventory.linked_to.get(first) == second:
        return False
    if inventory.linked_to.get(second) == first:
        return False
    family_first = inventory.family_of.get(first, first)
    family_second = inventory.family_of.get(second, second)
    return family_first != family_second


def normalize_pair(first: int, second: int) -> Pair:
    """The stored order of a pair — re-exported for callers outside."""
    return ordered(first, second)
