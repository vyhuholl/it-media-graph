"""The four signals that suggest two channels share an author.

Pure functions over plain mappings — no session, no engine, no network.
Every input is data the project already holds: the inventory's usernames,
the derived edges, and the descriptions stored by the metadata pass. What
comes back is a proposal with the evidence behind it, never a decision.

Each signal answers with a strength in ``[0, 1]``; the caller weights and
sums them. The normalization is what lets four measurements of different
kinds — a link, a substring, a ratio, a count — be compared at all, and
each one's formula is documented where it is computed rather than here.

The signals were measured against the corpus this was written for (504
seeds, 15 651 edges, 195 descriptions) before any of them was
implemented, and two behave differently from the obvious expectation:

* **Mutual description references barely exist** — one pair. 31 of the 37
  one-way references point at a channel with no stored description at
  all, so mutuality is usually *unknowable* rather than absent, and
  requiring it would measure metadata coverage instead of affiliation.
* **A shared username token is usually a shared subject.** The tokens
  carried by several channels are words like ``channel``, ``tech``,
  ``news`` and ``data``. Rarity, not length, is what separates an author
  from a topic — which is why the document-frequency cap exists and why
  it is the parameter worth tuning.

They also barely corroborate each other: mutual-about and shared-token
overlap in zero pairs, mutual-about and mutual-edges in zero,
concentration and shared-token in 6 of 19. So the combined score is not a
consensus measure — it interleaves four nearly disjoint lists, and the
weights decide the merge order. Nothing here may therefore require two
signals before proposing a pair.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from itgraph.db.models import AboutDirection
from itgraph.derive.references import extract_text_references

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DEFAULT_WEIGHTS",
    "AboutResult",
    "AboutSignal",
    "MutualSignal",
    "ShareSignal",
    "Thresholds",
    "TokenSignal",
    "Weights",
    "description_references",
    "mutual_density",
    "outgoing_concentration",
    "shared_username_tokens",
]

# A pair, always stored with the smaller id first. Sorting by id rather
# than by whichever signal found it is what makes two signals reaching
# the same two channels from opposite directions produce one candidate.
Pair = tuple[int, int]

# (source channel, destination channel) -> how many edges.
EdgeCounts = Mapping[Pair, int]


@dataclass(frozen=True, slots=True)
class Thresholds:
    """What each signal requires before it says anything.

    Defaults come from measuring the corpus, not from intuition, and each
    is noted with what it yields there. They are a starting point for
    tuning — every one is settable per run.
    """

    # A share taken over a handful of references means nothing; this is
    # the denominator floor that makes the ratio worth reading.
    min_out_edges: int = 20
    # 19 candidates at this floor, 38 at 0.5.
    max_share_min: float = 0.7
    # Length alone cannot tell `podcast` from a company name — it is the
    # cap below that does the work.
    min_token_length: int = 4
    # 44 pairs at 3, 216 with no cap at all.
    max_token_channels: int = 3
    # 45 pairs at 5, 62 at 3.
    min_mutual_edges: int = 5


@dataclass(frozen=True, slots=True)
class Weights:
    """How much each signal contributes to the combined score.

    Since the signals hardly ever fire on the same pair, these mostly
    decide which of four lists is read first rather than calibrating one
    measure. Treated as a calibration they would be false precision;
    treated as a reading order they are exactly the knob the operator
    wants.
    """

    about: float = 1.0
    share: float = 0.8
    token: float = 0.6
    mutual: float = 0.5


# The defaults, as instances. `slots=True` turns the class attributes
# into slot descriptors, so `Thresholds.min_out_edges` is *not* the
# default — it is a descriptor object, and passing it anywhere as a value
# fails quietly rather than loudly. These are the one place a default is
# named, and what the CLI's option defaults read from.
DEFAULT_THRESHOLDS = Thresholds()
DEFAULT_WEIGHTS = Weights()


@dataclass(frozen=True, slots=True)
class AboutSignal:
    """Two channels whose descriptions name each other, or one that does."""

    pair: Pair
    strength: float
    direction: AboutDirection


@dataclass(frozen=True, slots=True)
class AboutResult:
    """What the description pass found, and what it could not place.

    ``refs_outside_inventory`` counts handles that parsed cleanly and
    named no channel the inventory holds — an author's uncollected
    channel, a personal account, a bot. It is reported rather than
    dropped silently because it is the larger number: 134 against 37 on
    the corpus this was built for, and every one of them is a lead for
    the inventory rather than a failure of the signal.
    """

    signals: list[AboutSignal]
    refs_outside_inventory: int


@dataclass(frozen=True, slots=True)
class TokenSignal:
    """Two usernames sharing a token, and how rare that token is."""

    pair: Pair
    strength: float
    token: str
    token_channels: int


@dataclass(frozen=True, slots=True)
class ShareSignal:
    """One channel sending most of its references to one other.

    Directional even though the pair is not: ``src`` is the concentrated
    channel, and which side that was is the whole content of the signal.
    """

    pair: Pair
    strength: float
    share: float
    edges: int
    src: int


@dataclass(frozen=True, slots=True)
class MutualSignal:
    """Two channels referencing each other, counted each way."""

    pair: Pair
    strength: float
    edges_a_to_b: int
    edges_b_to_a: int


def ordered(first: int, second: int) -> Pair:
    """A pair in its stored order — smaller id first."""
    return (first, second) if first < second else (second, first)


def description_references(
    descriptions: Mapping[int, str],
    *,
    channel_by_username: Mapping[str, int],
    known_channels: frozenset[int],
) -> AboutResult:
    """Pairs proposed by one description naming another channel.

    A reference found one way is evidence at ``0.5``; found both ways, at
    ``1.0``. A one-way reference is deliberately **not** penalised for
    the return link that was not found, because most targets have no
    stored description in which a return link could exist — treating
    silence as denial would score metadata coverage rather than
    affiliation.

    ``channel_by_username`` maps a normalized username to its channel id;
    ``known_channels`` is every id the inventory holds, for the
    ``t.me/c/<id>`` form that names an id directly. A handle matching
    neither forms no candidate and is counted.
    """
    named: dict[int, set[int]] = defaultdict(set)
    outside = 0
    for channel_id, about in descriptions.items():
        for reference in extract_text_references(about):
            if reference.channel_id is not None:
                target: int | None = (
                    reference.channel_id
                    if reference.channel_id in known_channels
                    else None
                )
            elif reference.username is not None:
                target = channel_by_username.get(reference.username)
            else:  # pragma: no cover - a Reference always carries one
                target = None
            if target is None:
                outside += 1
            elif target != channel_id:
                # A description linking the channel it belongs to is a
                # fact about one channel, not a relationship between two.
                named[channel_id].add(target)

    signals: list[AboutSignal] = []
    for source, targets in named.items():
        for target in targets:
            pair = ordered(source, target)
            if source in named.get(target, frozenset()):
                # Emit a mutual pair once, from the lower id, rather than
                # letting both directions produce it.
                if source != pair[0]:
                    continue
                signals.append(
                    AboutSignal(
                        pair=pair,
                        strength=1.0,
                        direction=AboutDirection.MUTUAL,
                    )
                )
            else:
                signals.append(
                    AboutSignal(
                        pair=pair,
                        strength=0.5,
                        direction=(
                            AboutDirection.A_TO_B
                            if source == pair[0]
                            else AboutDirection.B_TO_A
                        ),
                    )
                )
    return AboutResult(signals=signals, refs_outside_inventory=outside)


def shared_username_tokens(
    usernames: Mapping[int, str], *, thresholds: Thresholds
) -> list[TokenSignal]:
    """Pairs proposed by a username token rare enough to mean something.

    Strength is ``(M + 1 − d) / M`` for a token carried by ``d`` channels
    under a cap of ``M``: rarest scores highest, and a token at the cap
    still scores above nothing. The cap is the signal. Without it the
    corpus yields 216 pairs, most of them from ``channel``, ``tech``,
    ``news``, ``jobs`` and ``data`` — five topics shared by 5 to 11
    unrelated channels each. With it, 44.

    Where two channels share several tokens the rarest one wins: it is
    the one carrying the claim. Ties are broken on the longer token and
    then alphabetically, and that is not cosmetic — two channels named
    ``fake_gonzo_main`` and ``fake_gonzo_pod`` share ``fake`` and
    ``gonzo`` at identical rarity, and ``_tokens`` returns a *set*, whose
    iteration order for strings moves with the interpreter's hash seed.
    Without a total order here the same input reports a different token
    from run to run, which the spec's reproducibility requirement
    forbids and which is invisible until it is not.
    """
    by_token: dict[str, list[int]] = defaultdict(list)
    for channel_id, username in usernames.items():
        for token in _tokens(username, thresholds.min_token_length):
            by_token[token].append(channel_id)

    strongest: dict[Pair, TokenSignal] = {}
    cap = thresholds.max_token_channels
    for token, channels in by_token.items():
        count = len(channels)
        if count < 2 or count > cap:
            continue
        strength = (cap + 1 - count) / cap
        for index, first in enumerate(channels):
            for second in channels[index + 1 :]:
                pair = ordered(first, second)
                current = strongest.get(pair)
                if current is None or _beats(
                    strength, token, current.strength, current.token
                ):
                    strongest[pair] = TokenSignal(
                        pair=pair,
                        strength=strength,
                        token=token,
                        token_channels=count,
                    )
    return list(strongest.values())


def _beats(
    strength: float, token: str, other_strength: float, other_token: str
) -> bool:
    """Whether this token is the better claim about the pair.

    Rarer first, then longer, then alphabetical. The last two are
    arbitrary as claims and essential as an ordering: they make the
    result independent of the set iteration order that produced it.
    """
    return (strength, len(token), other_token) > (
        other_strength,
        len(other_token),
        token,
    )


def outgoing_concentration(
    edges: EdgeCounts, *, thresholds: Thresholds
) -> list[ShareSignal]:
    """Pairs proposed by one channel aiming most of its output at one other.

    Strength is the observed share itself, which is already in
    ``[threshold, 1]``. Rescaling it to span the full range would give a
    channel sitting exactly on the threshold a strength of zero — it
    would enter the candidate list and contribute nothing to its own
    position in it.

    ``min_out_edges`` is not a nicety: a channel with three outgoing
    references and two to one target scores 0.67 on noise. The floor is
    what makes the ratio mean anything, so it is applied before the
    share is looked at.
    """
    total: dict[int, int] = defaultdict(int)
    for (source, _), count in edges.items():
        total[source] += count

    best: dict[int, tuple[int, int]] = {}
    for (source, target), count in edges.items():
        current = best.get(source)
        # Ties break on the lower target id, so a run is reproducible
        # rather than dependent on mapping order.
        if current is None or (count, -target) > (current[1], -current[0]):
            best[source] = (target, count)

    signals: list[ShareSignal] = []
    for source, (target, count) in best.items():
        edges_out = total[source]
        if edges_out < thresholds.min_out_edges:
            continue
        share = count / edges_out
        if share < thresholds.max_share_min:
            continue
        signals.append(
            ShareSignal(
                pair=ordered(source, target),
                strength=share,
                share=share,
                edges=edges_out,
                src=source,
            )
        )
    return signals


def mutual_density(
    edges: EdgeCounts, *, thresholds: Thresholds
) -> list[MutualSignal]:
    """Pairs proposed by two channels referencing each other repeatedly.

    Strength is ``min(1, min(n_ab, n_ba) / (2K))``: a pair at exactly the
    minimum each way scores ``0.5`` and one at twice it scores ``1.0``.
    The weaker direction is what is measured — a pair that is heavy one
    way and barely present the other is an audience relationship, and
    the minimum is what says so.

    Counts are reported against the pair's stored order, not against
    whichever channel was looked at first.
    """
    signals: list[MutualSignal] = []
    ceiling = 2 * thresholds.min_mutual_edges
    for (source, target), count in edges.items():
        if source >= target:
            continue
        back = edges.get((target, source))
        if back is None:
            continue
        if count < thresholds.min_mutual_edges:
            continue
        if back < thresholds.min_mutual_edges:
            continue
        signals.append(
            MutualSignal(
                pair=(source, target),
                strength=min(1.0, min(count, back) / ceiling),
                edges_a_to_b=count,
                edges_b_to_a=back,
            )
        )
    return signals


def _tokens(username: str, min_length: int) -> set[str]:
    """The parts of a username long enough to carry a claim.

    Split on the two separators Telegram usernames actually use. A
    username with no separator is one token, which is what makes a pair
    of channels named alike but written as one word reachable at all.
    """
    return {
        part
        for part in username.lower().replace("-", "_").split("_")
        if len(part) >= min_length
    }
