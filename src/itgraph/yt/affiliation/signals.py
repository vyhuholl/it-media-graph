"""The three signals that suggest two YouTube channels share an owner.

Pure functions over plain mappings — no session, no engine, no network,
no quota. Every input is data the project already holds: the titles and
descriptions stored in ``yt_raw_channels.payload`` and the handles those
same payloads carry. What comes back is a proposal with the evidence
behind it, never a decision.

Each signal answers with a strength in ``[0, 1]``; the caller weights
and sums them. The signals were measured against the inventory this was
written for (887 seeds, 1127 payloads, 795 descriptions) on 2026-11-18,
before any of them was implemented:

* **Shared title tokens are mostly subjects.** At a cap of 3 channels
  the corpus yields 198 pairs; the tokens beyond it are ``pyladies``
  (45), ``google`` (27), ``code`` (14), ``data`` (14) — subjects and
  organisations, not authors. The cap does the discriminating.
* **Descriptions barely link siblings.** 26 references resolve into the
  inventory (bare ``@``, ``@handle`` links and ``/channel/`` id links
  together); ~37 more name channels outside it, and each of those is a
  lead for the inventory rather than a failure of the signal.
* **An explicit reference turns a subject into a family.** Of the
  tokens many channels carry, the ones corroborated by a reference
  between two of their carriers are ``google`` (28), ``jetbrains`` (9),
  ``bombal`` (6), ``unity`` (3) — organisations and authors whose
  channels link each other. That is the discriminator the rarity cap
  cannot be on YouTube, where an organisation's product channels are a
  family by the operator's decision, the opposite reading from
  Telegram's "a token on many channels is a subject".

Two of Telegram's five signals are deliberately absent. Outgoing
concentration needs sources with tens of distinct reference targets and
no YouTube seed reaches even ten; mutual density needs repeated
references each way and 24 pairs have one at all. Porting them would
add two parameters no data can ever satisfy — the caps and weights are
settable precisely so that a corpus that grows into them can turn them
on, in a change of their own.
"""

import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from itgraph.db.models import YtRefDirection
from itgraph.derive.yt_references import read_text

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DEFAULT_WEIGHTS",
    "DescriptionResult",
    "GroupSignal",
    "Pair",
    "ReferenceSignal",
    "Thresholds",
    "TokenSignal",
    "Weights",
    "YtRefDirection",
    "corroborated_token_groups",
    "description_references",
    "ordered",
    "shared_title_tokens",
]

# A pair, always stored with the lexicographically smaller id first.
# YouTube channel ids are text, so "smaller" is string order — the
# deterministic analogue of Telegram's smaller-number-first, and what
# makes two signals reaching the same two channels from opposite
# directions produce one candidate.
Pair = tuple[str, str]

# handle (normalized, no ``@``) -> the channels whose payload carries it.
# The same shape `Known.handles` has, so the inventory loader fills one
# mapping for every reader.
Handles = Mapping[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class Thresholds:
    """What each signal requires before it says anything.

    Defaults come from measuring the corpus, not from intuition, and
    each is noted with what it yields there. They are a starting point
    for tuning — every one is settable per run.
    """

    # Length in characters, of any script; a shorter run is a fragment.
    # 198 pairs at the cap below, 352 with the cap at 5.
    min_token_length: int = 4
    # A token on more channels than this is a subject, not an owner —
    # unless a reference corroborates it, which has its own cap below.
    max_token_channels: int = 3
    # The corroborated-group signal's own bound, and not a rarity
    # threshold: an organisation's channels are not weaker evidence for
    # being many. This bounds the d(d−1)/2 pairs one token can produce
    # and nothing else. It binds nothing on the corpus it was set
    # against — `google` sits at 28 carriers, `pyladies` at 45 — and
    # exists so that a brand landing on a thousand channels cannot
    # arrive as half a million pairs in one block.
    max_corroborated_channels: int = 50


@dataclass(frozen=True, slots=True)
class Weights:
    """How much each signal contributes to the combined score.

    The weights order a reading list, they do not measure confidence:
    corroborated groups and mutual references interleave at the top
    because both are near-certain, title tokens below because the cap
    is what carries their precision.
    """

    reference: float = 1.0
    group: float = 1.0
    token: float = 0.6


# The defaults, as instances. What the CLI's option defaults read from,
# and the one place a default is named.
DEFAULT_THRESHOLDS = Thresholds()
DEFAULT_WEIGHTS = Weights()


@dataclass(frozen=True, slots=True)
class TokenSignal:
    """Two titles sharing a token, and how rare that token is."""

    pair: Pair
    strength: float
    token: str
    token_channels: int


@dataclass(frozen=True, slots=True)
class ReferenceSignal:
    """Two channels whose descriptions name each other, or one that does."""

    pair: Pair
    strength: float
    direction: YtRefDirection


@dataclass(frozen=True, slots=True)
class GroupSignal:
    """Two carriers of a token a reference between carriers corroborates."""

    pair: Pair
    strength: float
    token: str
    token_channels: int


@dataclass(frozen=True, slots=True)
class DescriptionResult:
    """What the descriptions named, and what they could not place.

    ``refs`` is the resolved reference graph, directed: who named whom.
    The per-pair signals are derived from it, and the corroborated-group
    signal needs the directions the pair form throws away — a reference
    found both ways is one pair but corroboration reads either way.

    ``refs_outside_inventory`` counts handles, legacy names and channel
    ids that parsed cleanly and named no channel the inventory holds —
    an owner's uncollected channel, an archived project, a person. It is
    reported rather than dropped silently because it is the larger
    number (~37 against 26 on the corpus) and every one of them is a
    lead for the inventory rather than a failure of the signal.
    """

    signals: list[ReferenceSignal]
    refs: Mapping[str, frozenset[str]]
    refs_outside_inventory: int


def ordered(first: str, second: str) -> Pair:
    """A pair in its stored order — lexicographically smaller id first."""
    return (first, second) if first < second else (second, first)


def _token_char(char: str) -> bool:
    """A letter, a digit or a combining mark, of any script.

    The same rule ``_handle_char`` applies to handles, for the same
    reason: a title in Devanagari is a title, and a token that a vowel
    sign splits in half is not two tokens.
    """
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _tokens(title: str, min_length: int) -> set[str]:
    """The parts of a title long enough to carry a claim.

    Maximal runs of letters, digits and marks, lowercased: punctuation
    is not a word boundary anyone agreed on, and a hyphen in
    ``JetBrains-TV`` separates one claim, not two. The custom URL is
    never read for tokens — YouTube handles are single fused words
    (``@microsoftazure``), and looking for tokens in them was measured
    to give nothing.
    """
    tokens: set[str] = set()
    current: list[str] = []
    for char in title:
        if _token_char(char):
            current.append(char)
            continue
        if len(current) >= min_length:
            tokens.add("".join(current).lower())
        current = []
    if len(current) >= min_length:
        tokens.add("".join(current).lower())
    return tokens


def title_tokens(
    titles: Mapping[str, str], min_length: int
) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Every channel's tokens, and the carriers of each token.

    Shared by both title-reading signals so they cannot disagree about
    what a token is. Carriers are lists because the group signal counts
    them; they are deduplicated per channel by the set on the left.
    """
    carried: dict[str, set[str]] = {}
    by_token: dict[str, list[str]] = defaultdict(list)
    for channel_id, title in titles.items():
        tokens = _tokens(title, min_length)
        carried[channel_id] = tokens
        for token in tokens:
            by_token[token].append(channel_id)
    return carried, by_token


def shared_title_tokens(
    titles: Mapping[str, str],
    *,
    thresholds: Thresholds,
    excluding: frozenset[str] = frozenset(),
) -> list[TokenSignal]:
    """Pairs proposed by a title token rare enough to mean something.

    ``excluding`` names tokens already read by the corroborated-group
    signal. They are skipped rather than filtered afterwards, so a pair
    whose *strongest* token is corroborated can still be proposed here
    on a weaker one it also shares. Reading one shared token twice would
    count a single observation twice and rank the pair above one
    carrying a token plus an independent reference.

    Strength is ``(M + 1 − d) / M`` for a token carried by ``d``
    channels under a cap of ``M``: rarest scores highest, and a token at
    the cap still scores above nothing. Telegram's formula, carried over
    unchanged, because the corpus behaves the same way — without the cap
    the titles yield 2730 pairs, most of them from ``pyladies``,
    ``google``, ``code`` and ``data``; with it, 198.

    Where two channels share several tokens the rarest one wins, ties
    break on the longer token and then alphabetically — a total order,
    so the same input reports the same token from run to run whatever
    order a set happened to iterate in.
    """
    _, by_token = title_tokens(titles, thresholds.min_token_length)

    strongest: dict[Pair, TokenSignal] = {}
    cap = thresholds.max_token_channels
    for token, channels in by_token.items():
        count = len(channels)
        if count < 2 or count > cap or token in excluding:
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


def description_references(
    descriptions: Mapping[str, str],
    *,
    handles: Handles,
    known_channels: frozenset[str],
) -> DescriptionResult:
    """Pairs proposed by one channel's description naming another.

    The description is read by ``derive/yt_references.py``'s
    :func:`read_text` — the same grammar the mention graph reads video
    descriptions with, deliberately: a channel description naming
    ``youtube.com/@handle``, ``youtube.com/channel/UC…``, a bare
    ``@handle`` the inventory carries or ``youtube.com/c/NAME`` is
    counted and never followed. One grammar, not two, is what keeps the
    two readers from disagreeing about what a link names.

    A reference found one way is evidence at ``0.5``; found both ways,
    at ``1.0``. A one-way reference is deliberately **not** penalised
    for the return link that was not found, because a target without a
    stored description has nowhere to put one — treating silence as
    denial would score payload coverage rather than affiliation.

    A handle two channels carry names neither (the same rule the
    mention graph applies); it forms no candidate and is not counted
    outside, because the inventory does hold what it names.
    """

    def by_handle(handle: str) -> str | None:
        carriers = handles.get(handle, frozenset())
        if len(carriers) > 1:
            # Ambiguous: it names channels the inventory holds, so it is
            # neither a candidate nor a lead — it is nothing.
            return None
        return next(iter(carriers), None)

    named: dict[str, set[str]] = defaultdict(set)
    outside = 0
    for channel_id, description in descriptions.items():
        found = read_text(description)
        targets: set[str] = set()

        for target in found.channel_ids:
            if target in known_channels:
                targets.add(target)
            else:
                outside += 1
        # A set: one handle linked *and* mentioned is one reference.
        for handle in {*found.handles, *found.mentions}:
            resolved = by_handle(handle)
            if resolved is not None:
                targets.add(resolved)
            elif len(handles.get(handle, frozenset())) == 0:
                outside += 1
        # A legacy name is never resolvable from stored payloads, and a
        # video link names a video, not a channel — the first is a lead,
        # the second is the mention graph's to read, not this signal's.
        outside += len(found.legacy_names)
        targets.discard(channel_id)

        for target in targets:
            named[channel_id].add(target)

    signals: list[ReferenceSignal] = []
    for source, targets in named.items():
        for target in targets:
            pair = ordered(source, target)
            if source in named.get(target, frozenset()):
                # Emit a mutual pair once, from the smaller id, rather
                # than letting both directions produce it.
                if source != pair[0]:
                    continue
                signals.append(
                    ReferenceSignal(
                        pair=pair,
                        strength=1.0,
                        direction=YtRefDirection.MUTUAL,
                    )
                )
            else:
                signals.append(
                    ReferenceSignal(
                        pair=pair,
                        strength=0.5,
                        direction=(
                            YtRefDirection.A_TO_B
                            if source == pair[0]
                            else YtRefDirection.B_TO_A
                        ),
                    )
                )
    return DescriptionResult(
        signals=signals,
        refs={source: frozenset(targets) for source, targets in named.items()},
        refs_outside_inventory=outside,
    )


def corroborated_token_groups(
    titles: Mapping[str, str],
    descriptions: Mapping[str, str],
    *,
    handles: Handles,
    known_channels: frozenset[str],
    thresholds: Thresholds,
) -> tuple[list[GroupSignal], DescriptionResult]:
    """Pairs proposed by a token a reference between its carriers names.

    The rule: one channel's description explicitly references another
    inventory channel, and both endpoints' titles share a token — then
    every carrier of that token is proposed as one group, whatever the
    carrier count. The explicit reference is the evidence and the token
    only names the set; an organisation's product channels and a
    franchise's local chapters link each other, and on YouTube that is
    exactly what a family looks like. Measured on the corpus: the
    corroborated tokens are ``google`` (28 carriers), ``jetbrains`` (9),
    ``david`` (8 — a name several channels carry, corroborated by their
    cross-links; the price of the rule, paid once through a token
    rejection), ``bombal`` (6), while ``pyladies`` and ``code`` wait
    uncorroborated under the rarity cap.

    **Strength is a flat ``1.0``, whatever ``d``.** The rarity formula
    is deliberately not reused: it exists to say "a token on many
    channels is a subject", and the reference between carriers is
    precisely the observation that here it is not.

    Returns the signals together with the description reading they were
    corroboration over, so the caller runs :func:`read_text` over each
    description exactly once.
    """
    description = description_references(
        descriptions, handles=handles, known_channels=known_channels
    )
    carried, by_token = title_tokens(titles, thresholds.min_token_length)

    corroborated: set[str] = set()
    for source, targets in description.refs.items():
        tokens = carried.get(source, set())
        for target in targets:
            # Both endpoints must carry the token: the reference is
            # between two carriers, not merely from one.
            shared = tokens & carried.get(target, set())
            corroborated |= shared

    strongest: dict[Pair, GroupSignal] = {}
    cap = thresholds.max_corroborated_channels
    for token in sorted(corroborated):
        channels = by_token[token]
        count = len(channels)
        if count < 2 or count > cap:
            continue
        for index, first in enumerate(channels):
            for second in channels[index + 1 :]:
                pair = ordered(first, second)
                current = strongest.get(pair)
                if current is None or _beats(1.0, token, 1.0, current.token):
                    strongest[pair] = GroupSignal(
                        pair=pair,
                        strength=1.0,
                        token=token,
                        token_channels=count,
                    )
    return list(strongest.values()), description
