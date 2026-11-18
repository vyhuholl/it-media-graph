"""The merge of the three signals into one ranked list.

Pure functions over an in-memory inventory: no database, no network.
What is tested here is the shape of the merge — one candidate per pair,
any one signal enough to propose, corroboration ranking above a lone
token — and the refusal of parameters that cannot mean anything.
"""

import pytest

from itgraph.yt.affiliation.detect import (
    Detection,
    InvalidParameterError,
    Inventory,
    detect,
    normalize_pair,
    validate_parameters,
)
from itgraph.yt.affiliation.signals import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    Thresholds,
    Weights,
    YtRefDirection,
)

A = "UCfake_detect_0000000001"
B = "UCfake_detect_0000000002"
C = "UCfake_detect_0000000003"
D = "UCfake_detect_0000000004"


def inventory(
    *,
    titles: dict[str, str] | None = None,
    descriptions: dict[str, str] | None = None,
    handles: dict[str, frozenset[str]] | None = None,
    known: set[str] | None = None,
    family_of: dict[str, str] | None = None,
) -> Inventory:
    titles = titles if titles is not None else {}
    known = known if known is not None else set(titles)
    return Inventory(
        titles=titles,
        descriptions=descriptions or {},
        handles=handles or {},
        known_channels=frozenset(known),
        family_of=family_of or {},
    )


def detection(**kwargs: object) -> Detection:
    return detect(
        inventory(**kwargs),  # type: ignore[arg-type]
        thresholds=DEFAULT_THRESHOLDS,
        weights=DEFAULT_WEIGHTS,
    )


# --- the merge ----------------------------------------------------------


def test_one_signal_is_enough_to_propose() -> None:
    run = detection(
        titles={A: "Lonely Words Show", B: "Lonely Words Blog"},
        known={A, B, C},
    )

    assert [candidate.pair for candidate in run.candidates] == [(A, B)]


def test_a_pair_two_signals_reached_appears_once_and_ranks_higher() -> None:
    lone = detection(
        titles={A: "Shared Words Show", B: "Shared Words Blog"},
        known={A, B},
    )
    both = detection(
        titles={A: "Shared Words Show", B: "Shared Words Blog"},
        descriptions={A: f"Sibling: youtube.com/channel/{B}"},
        known={A, B},
    )

    assert len(both.candidates) == 1
    candidate = both.candidates[0]
    assert candidate.pair == (A, B)
    assert candidate.shared_token == "words"
    assert candidate.ref_direction is YtRefDirection.A_TO_B
    assert candidate.score > lone.candidates[0].score


def test_a_pair_proposed_from_both_directions_is_one_candidate() -> None:
    run = detection(
        titles={A: "Mirror Show", B: "Mirror Blog"},
        descriptions={
            A: f"See youtube.com/channel/{B}",
            B: f"See youtube.com/channel/{A}",
        },
        known={A, B},
    )

    assert len(run.candidates) == 1
    assert run.candidates[0].ref_direction is YtRefDirection.MUTUAL


def test_a_channel_is_never_its_own_candidate() -> None:
    """The signals cannot produce it; a hand-made inventory can."""
    run = detection(
        titles={A: "Same Words"},
        descriptions={A: f"See youtube.com/channel/{A}"},
        known={A},
    )

    assert run.candidates == []


def test_two_channels_in_one_family_are_not_proposed() -> None:
    run = detection(
        titles={A: "Family Words Show", B: "Family Words Blog"},
        known={A, B},
        family_of={A: A, B: A},
    )

    assert run.candidates == []


def test_a_corroborated_group_carries_the_group_evidence_only() -> None:
    """The token a reference corroborated is not read a second time."""
    run = detection(
        titles={A: "Brand Show", B: "Brand Blog", C: "Brand Extra"},
        descriptions={A: f"See youtube.com/channel/{B}"},
        known={A, B, C},
    )

    by_pair = {c.pair: c for c in run.candidates}
    # Every pair among the carriers, each carrying the group evidence
    # and no `shared_token` second reading of `brand`. The pair the
    # reference itself connected counts both observations: a corroborated
    # group and an explicit reference are two facts, not one counted
    # twice.
    assert sorted(by_pair) == [(A, B), (A, C), (B, C)]
    for candidate in by_pair.values():
        assert candidate.corroborated_token == "brand"
        assert candidate.corroborated_token_channels == 3
        assert candidate.shared_token is None
    assert by_pair[(A, B)].score == (
        DEFAULT_WEIGHTS.group + DEFAULT_WEIGHTS.reference * 0.5
    )
    assert by_pair[(A, C)].score == DEFAULT_WEIGHTS.group


def test_ties_break_on_the_pair_so_a_run_is_reproducible() -> None:
    run = detection(
        titles={
            B: "Equal Words Two",
            A: "Equal Words One",
            D: "Equal Words Four",
            C: "Equal Words Three",
        },
        known={A, B, C, D},
    )

    pairs = [candidate.pair for candidate in run.candidates]
    assert pairs == sorted(pairs)


def test_the_coverage_counts_what_the_run_could_see() -> None:
    run = detection(
        titles={A: "Title Holder", B: "Title Holder Too"},
        descriptions={A: "Only one description"},
        known={A, B, C},
    )

    assert run.channels_scored == 3
    assert run.with_payload == 2
    assert run.with_description == 1
    assert run.refs_outside_inventory == 0


def test_unresolvable_references_travel_with_the_run() -> None:
    run = detection(
        titles={A: "Solo", B: "Soloist"},
        descriptions={A: "Uncollected: youtube.com/@nobody_here"},
        known={A, B},
    )

    assert run.refs_outside_inventory == 1


# --- parameters ---------------------------------------------------------


def test_a_non_positive_minimum_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="min_token_length"):
        validate_parameters(Thresholds(min_token_length=0), DEFAULT_WEIGHTS)


def test_a_cap_below_two_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="max_token_channels"):
        validate_parameters(Thresholds(max_token_channels=1), DEFAULT_WEIGHTS)
    with pytest.raises(
        InvalidParameterError, match="max_corroborated_channels"
    ):
        validate_parameters(
            Thresholds(max_corroborated_channels=1), DEFAULT_WEIGHTS
        )


def test_a_negative_weight_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="reference"):
        validate_parameters(DEFAULT_THRESHOLDS, Weights(reference=-0.1))


def test_the_defaults_validate() -> None:
    validate_parameters(DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS)


def test_pairs_are_normalized_to_their_stored_order() -> None:
    assert normalize_pair(B, A) == (A, B)
    assert normalize_pair(A, B) == (A, B)
