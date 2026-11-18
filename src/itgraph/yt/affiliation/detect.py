"""Merging the three signals into one ranked list of candidate pairs.

The union of what the signals found, not the intersection. One signal is
enough to propose a pair, and that is a measurement rather than a
preference: on the corpus this was written for, the rarity-capped title
tokens, the description references and the corroborated groups are three
nearly disjoint lists — the groups carry their own token, a reference
between strangers shares no token, and a shared token needs no reference.

So the score orders a reading list, it does not measure confidence.
Nothing here decides that two channels share an owner — the ranking
exists to spend a human's attention well, and `db/yt_channels.py` is the
only place a family is ever recorded.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from itgraph.yt.affiliation.signals import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    Pair,
    Thresholds,
    Weights,
    YtRefDirection,
    corroborated_token_groups,
    ordered,
    shared_title_tokens,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DEFAULT_WEIGHTS",
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

    887 payloads is 394 000 pairs, so nothing here is asked about the
    pair space: each signal *emits* the pairs it found. The corpus fits
    in memory by a wide margin, which is why the signals are plain
    Python over rows fetched once rather than SQL, and why every one of
    them is testable without a database.

    Titles, descriptions and handles come from ``yt_raw_channels.payload``
    and nowhere else — what YouTube said lives only in the raw layer, so
    a channel whose payload is gone is simply absent from these
    mappings, and still pairable by a description that names its id.
    """

    # Stored `snippet.title` per channel; a channel without one is absent.
    titles: Mapping[str, str]
    # Stored `snippet.description` per channel; absent likewise.
    descriptions: Mapping[str, str]
    # handle (normalized, no ``@``) -> the channels carrying it in
    # `snippet.customUrl`. More than one is an ambiguity, not a choice.
    handles: Mapping[str, frozenset[str]]
    # Every channel id the inventory holds.
    known_channels: frozenset[str]
    # Channel -> the family key it already has. Two channels already in
    # one family have nothing left to decide.
    family_of: Mapping[str, str]


@dataclass(slots=True)
class Candidate:
    """One proposed pair, its score, and the evidence behind it.

    Mutable, and deliberately so: it is filled in one signal at a time.
    Each signal owns a disjoint group of the evidence fields, so they
    accumulate without ever overwriting each other, and the score adds
    up as they arrive.
    """

    pair: Pair
    score: float = 0.0

    ref_direction: YtRefDirection | None = None
    shared_token: str | None = None
    shared_token_channels: int | None = None
    corroborated_token: str | None = None
    corroborated_token_channels: int | None = None


@dataclass(frozen=True, slots=True)
class Detection:
    """A whole run: what it proposed, and what it could see.

    The coverage travels with the candidates because a short list means
    two different things depending on it. 92 of 887 seeds have no stored
    description, so the reference signal's silence about them is mostly
    ignorance rather than evidence of no affiliation — and a channel
    whose payload is gone has neither a title nor a description, while
    still being nameable by an id link.
    """

    candidates: list[Candidate]
    channels_scored: int
    with_payload: int
    with_description: int
    refs_outside_inventory: int


def validate_parameters(thresholds: Thresholds, weights: Weights) -> None:
    """Refuse a parameter that cannot mean anything, before any work.

    A minimum below one admits everything; a cap below two can hold no
    pair; a negative weight would rank a signal's absence above its
    presence. Each is named in the error, because a run that fails
    silently on a typo is worse than one that never started.
    """
    if thresholds.min_token_length < 1:
        raise InvalidParameterError(
            f"min_token_length must be at least 1, "
            f"got {thresholds.min_token_length}"
        )
    for name in ("max_token_channels", "max_corroborated_channels"):
        value = getattr(thresholds, name)
        if value < 2:
            raise InvalidParameterError(
                f"{name} must be at least 2 — a token on one channel is "
                f"shared with nobody, got {value}"
            )
    for name in ("reference", "group", "token"):
        value = getattr(weights, name)
        if value < 0:
            raise InvalidParameterError(
                f"weight for {name} must not be negative, got {value}"
            )


def detect(
    inventory: Inventory,
    *,
    thresholds: Thresholds,
    weights: Weights,
) -> Detection:
    """Every pair any signal proposes, scored and ranked.

    Signals contribute independently: a pair reached by one appears, a
    pair reached by three ranks above it. Each pair is normalized to its
    stored order before merging, so two signals arriving from opposite
    directions produce one candidate rather than two mirror images.
    """
    merged: dict[Pair, Candidate] = {}

    def entry_for(pair: Pair) -> Candidate:
        return merged.setdefault(pair, Candidate(pair=pair))

    # First, because what it corroborates is what the rarity signal must
    # not read a second time — one shared token named by a reference is
    # one observation, counted once.
    group_signals, description = corroborated_token_groups(
        inventory.titles,
        inventory.descriptions,
        handles=inventory.handles,
        known_channels=inventory.known_channels,
        thresholds=thresholds,
    )
    for group_signal in group_signals:
        entry = entry_for(group_signal.pair)
        entry.score += weights.group * group_signal.strength
        entry.corroborated_token = group_signal.token
        entry.corroborated_token_channels = group_signal.token_channels

    corroborated = frozenset(signal.token for signal in group_signals)
    for reference_signal in description.signals:
        entry = entry_for(reference_signal.pair)
        entry.score += weights.reference * reference_signal.strength
        entry.ref_direction = reference_signal.direction

    for token_signal in shared_title_tokens(
        inventory.titles, thresholds=thresholds, excluding=corroborated
    ):
        entry = entry_for(token_signal.pair)
        entry.score += weights.token * token_signal.strength
        entry.shared_token = token_signal.token
        entry.shared_token_channels = token_signal.token_channels

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
        with_payload=len(inventory.titles),
        with_description=len(inventory.descriptions),
        refs_outside_inventory=description.refs_outside_inventory,
    )


def _worth_proposing(pair: Pair, inventory: Inventory) -> bool:
    """Whether this pair is a question anyone still needs answering.

    A channel with itself is not a pair — the signals cannot produce
    one, but a caller assembling an ``Inventory`` by hand can, and the
    ordering helper would silently accept it.

    Two channels already in one family have nothing left to decide.
    Pairs already *confirmed or rejected* are excluded too, but not here:
    that filter belongs to the read, because the row must stay
    inspectable after the decision rather than vanish from the table.
    """
    first, second = pair
    if first == second:
        return False
    family_first = inventory.family_of.get(first, first)
    family_second = inventory.family_of.get(second, second)
    return family_first != family_second


def normalize_pair(first: str, second: str) -> Pair:
    """The stored order of a pair — re-exported for callers outside."""
    return ordered(first, second)
