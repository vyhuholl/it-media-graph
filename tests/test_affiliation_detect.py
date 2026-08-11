"""Merging the signals into a ranked list.

No database: an `Inventory` is a handful of dicts, which is the whole
point of keeping the computation pure. What is checked here is the
merge — that a pair reached twice is one row, that one signal is enough,
and that the exclusions hold.
"""

import pytest

from itgraph.affiliation.detect import (
    InvalidParameterError,
    Inventory,
    detect,
    validate_parameters,
)
from itgraph.affiliation.signals import Thresholds, Weights
from itgraph.db.models import AboutDirection

A = 1001
B = 1002
C = 1003

BOTH_KINDS = ["forward", "mention"]


def inventory(
    *,
    usernames: dict[int, str] | None = None,
    descriptions: dict[int, str] | None = None,
    edges: dict[tuple[int, int], int] | None = None,
    known: set[int] | None = None,
    linked_to: dict[int, int] | None = None,
    family_of: dict[int, int] | None = None,
) -> Inventory:
    return Inventory(
        usernames=usernames or {},
        descriptions=descriptions or {},
        edges=edges or {},
        known_channels=frozenset(known or {A, B, C}),
        linked_to=linked_to or {},
        family_of=family_of or {},
    )


def test_one_signal_is_enough_to_propose() -> None:
    """The measured overlap between signals is near zero, so a rule
    demanding two would propose almost nothing."""
    result = detect(
        inventory(usernames={A: "gonzo_one", B: "gonzo_two"}),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert [candidate.pair for candidate in result.candidates] == [(A, B)]
    assert result.candidates[0].shared_token == "gonzo"


def test_a_corroborated_pair_ranks_above_a_single_signal() -> None:
    result = detect(
        inventory(
            usernames={A: "gonzo_one", B: "gonzo_two", C: "unrelated_name"},
            descriptions={A: "Подкаст @gonzo_two"},
            edges={(A, C): 25},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert result.candidates[0].pair == (A, B)
    assert result.candidates[0].score > result.candidates[1].score


def test_a_pair_reached_from_both_directions_appears_once() -> None:
    result = detect(
        inventory(
            descriptions={A: "Подкаст @b_channel", B: "Основной @a_channel"},
            usernames={A: "a_channel", B: "b_channel"},
            edges={(A, B): 8, (B, A): 7},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.about_direction is AboutDirection.MUTUAL
    assert candidate.edges_a_to_b == 8
    assert candidate.edges_b_to_a == 7


def test_evidence_from_several_signals_accumulates() -> None:
    result = detect(
        inventory(
            usernames={A: "gonzo_one", B: "gonzo_two"},
            descriptions={A: "Подкаст @gonzo_two"},
            edges={(A, B): 20},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    candidate = result.candidates[0]
    assert candidate.shared_token == "gonzo"
    assert candidate.about_direction is AboutDirection.A_TO_B
    assert candidate.out_share == 1.0
    assert candidate.out_share_src == A


def test_a_discussion_chat_is_never_paired_with_its_parent() -> None:
    result = detect(
        inventory(
            usernames={A: "gonzo_main", B: "gonzo_chat"},
            edges={(A, B): 10, (B, A): 10},
            linked_to={B: A},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert result.candidates == []


def test_two_channels_already_in_one_family_are_not_proposed() -> None:
    result = detect(
        inventory(
            usernames={A: "gonzo_one", B: "gonzo_two"},
            family_of={A: A, B: A},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert result.candidates == []


def test_channels_in_different_families_are_still_proposed() -> None:
    result = detect(
        inventory(
            usernames={A: "gonzo_one", B: "gonzo_two"},
            family_of={A: A, B: C},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert [candidate.pair for candidate in result.candidates] == [(A, B)]


def test_the_ranking_is_reproducible_across_runs() -> None:
    """Ties break on the pair, not on which signal inserted first."""
    fixture = inventory(
        usernames={A: "gonzo_one", B: "gonzo_two", C: "gonzo_three"},
    )
    first = detect(fixture, thresholds=Thresholds(), weights=Weights())
    second = detect(fixture, thresholds=Thresholds(), weights=Weights())

    assert [candidate.pair for candidate in first.candidates] == [
        candidate.pair for candidate in second.candidates
    ]


def test_weights_change_the_reading_order() -> None:
    fixture = inventory(
        usernames={A: "gonzo_one", B: "gonzo_two", C: "unrelated_name"},
        edges={(A, C): 25},
    )

    token_first = detect(
        fixture, thresholds=Thresholds(), weights=Weights(token=10.0)
    )
    share_first = detect(
        fixture, thresholds=Thresholds(), weights=Weights(share=10.0)
    )

    assert token_first.candidates[0].pair == (A, B)
    assert share_first.candidates[0].pair == (A, C)


def test_coverage_travels_with_the_result() -> None:
    result = detect(
        inventory(
            descriptions={A: "Автор @someone_uncollected"},
            known={A, B, C},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert result.channels_scored == 3
    assert result.with_description == 1
    assert result.refs_outside_inventory == 1


# --- parameters -------------------------------------------------------


def test_a_share_above_one_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="max_share_min"):
        validate_parameters(
            Thresholds(max_share_min=1.5), Weights(), BOTH_KINDS
        )


def test_a_negative_share_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="max_share_min"):
        validate_parameters(
            Thresholds(max_share_min=-0.1), Weights(), BOTH_KINDS
        )


def test_a_non_positive_minimum_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="min_out_edges"):
        validate_parameters(Thresholds(min_out_edges=0), Weights(), BOTH_KINDS)


def test_a_token_cap_below_two_is_refused() -> None:
    """A token on one channel is shared with nobody."""
    with pytest.raises(InvalidParameterError, match="max_token_channels"):
        validate_parameters(
            Thresholds(max_token_channels=1), Weights(), BOTH_KINDS
        )


def test_a_negative_weight_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="about"):
        validate_parameters(Thresholds(), Weights(about=-1.0), BOTH_KINDS)


def test_counting_no_edge_kinds_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="edge kind"):
        validate_parameters(Thresholds(), Weights(), [])


def test_the_defaults_are_valid() -> None:
    validate_parameters(Thresholds(), Weights(), BOTH_KINDS)


def test_a_named_handle_proposes_every_pair_in_its_group() -> None:
    """The whole group, from one channel's description signing it."""
    result = detect(
        inventory(
            usernames={A: "tg_gonzo", B: "logs_gonzo", C: "files_gonzo"},
            descriptions={A: "Блоггер @gonzo"},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert {candidate.pair for candidate in result.candidates} == {
        (A, B),
        (A, C),
        (B, C),
    }
    assert all(
        candidate.handle_token == "gonzo" for candidate in result.candidates
    )
    assert all(
        candidate.handle_token_channels == 3 for candidate in result.candidates
    )


def test_a_signed_token_contributes_once() -> None:
    """Both signals read one observation — these usernames share this
    token — so the score must not add them up."""
    signed = detect(
        inventory(
            usernames={A: "tg_gonzo", B: "logs_gonzo"},
            descriptions={A: "@gonzo"},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert len(signed.candidates) == 1
    candidate = signed.candidates[0]
    assert candidate.handle_token == "gonzo"
    # The rarity signal was withheld, so it claims nothing here.
    assert candidate.shared_token is None
    assert candidate.score == pytest.approx(Weights().handle)


def test_a_handle_and_an_edge_signal_both_show_on_one_pair() -> None:
    """Independent evidence still accumulates — it is only the *same*
    observation that may not be counted twice."""
    result = detect(
        inventory(
            usernames={A: "tg_gonzo", B: "logs_gonzo"},
            descriptions={A: "@gonzo"},
            edges={(A, B): 25},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    candidate = result.candidates[0]
    assert candidate.handle_token == "gonzo"
    assert candidate.out_share == pytest.approx(1.0)
    assert candidate.score > Weights().handle


def test_a_handle_group_skips_a_chat_and_its_parent() -> None:
    """A discussion chat carries the family handle too, and its link to
    the channel is already recorded."""
    result = detect(
        inventory(
            usernames={A: "tg_gonzo", B: "chat_gonzo", C: "logs_gonzo"},
            descriptions={A: "@gonzo"},
            linked_to={B: A},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert {candidate.pair for candidate in result.candidates} == {
        (A, C),
        (B, C),
    }


def test_a_handle_group_skips_a_pair_already_in_one_family() -> None:
    result = detect(
        inventory(
            usernames={A: "tg_gonzo", B: "logs_gonzo", C: "files_gonzo"},
            descriptions={A: "@gonzo"},
            family_of={A: A, B: A, C: C},
        ),
        thresholds=Thresholds(),
        weights=Weights(),
    )

    assert {candidate.pair for candidate in result.candidates} == {
        (A, C),
        (B, C),
    }


def test_a_handle_cap_below_two_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="max_handle_token"):
        validate_parameters(
            Thresholds(max_handle_token_channels=1), Weights(), BOTH_KINDS
        )


def test_a_negative_handle_weight_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="handle"):
        validate_parameters(Thresholds(), Weights(handle=-1.0), BOTH_KINDS)
