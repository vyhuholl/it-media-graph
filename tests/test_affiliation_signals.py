"""The four affiliation signals, one shape at a time.

Pure functions over plain mappings: no database, no network, no fixture
beyond a dict. Each signal is tested where it fires, where it does not,
and at the boundary between the two — the boundary is the part that
decides how much noise reaches the operator.
"""

from itgraph.affiliation.signals import (
    Thresholds,
    description_references,
    mutual_density,
    outgoing_concentration,
    shared_username_tokens,
)
from itgraph.db.models import AboutDirection

A = 1001
B = 1002
C = 1003
D = 1004


# --- description references -------------------------------------------


def test_a_description_naming_another_channel_proposes_it() -> None:
    result = description_references(
        {A: "Подкаст: @example_pod"},
        channel_by_username={"example_pod": B},
        known_channels=frozenset({A, B}),
    )

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.pair == (A, B)
    assert signal.strength == 0.5
    assert signal.direction is AboutDirection.A_TO_B


def test_a_reference_from_the_higher_id_is_recorded_as_such() -> None:
    """The pair is ordered by id, so the direction needs an anchor."""
    result = description_references(
        {B: "Основной: @example_main"},
        channel_by_username={"example_main": A},
        known_channels=frozenset({A, B}),
    )

    assert result.signals[0].pair == (A, B)
    assert result.signals[0].direction is AboutDirection.B_TO_A


def test_references_both_ways_weigh_more_and_appear_once() -> None:
    result = description_references(
        {A: "Подкаст @example_pod", B: "Основной канал @example_main"},
        channel_by_username={"example_pod": B, "example_main": A},
        known_channels=frozenset({A, B}),
    )

    assert len(result.signals) == 1
    assert result.signals[0].direction is AboutDirection.MUTUAL
    assert result.signals[0].strength == 1.0


def test_a_one_way_reference_is_not_penalised_for_a_silent_target() -> None:
    """31 of 37 real references point at a channel with no stored
    description. Requiring a return link would measure coverage."""
    result = description_references(
        {A: "Подкаст @example_pod"},
        channel_by_username={"example_pod": B},
        known_channels=frozenset({A, B}),
    )

    assert result.signals[0].strength == 0.5


def test_a_handle_outside_the_inventory_is_counted_not_proposed() -> None:
    result = description_references(
        {A: "Автор @someone_else"},
        channel_by_username={},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 1


def test_a_description_naming_its_own_channel_proposes_nothing() -> None:
    result = description_references(
        {A: "Это @example_main"},
        channel_by_username={"example_main": A},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 0


def test_a_link_by_bare_id_reaches_the_channel() -> None:
    result = description_references(
        {A: f"см. t.me/c/{B}"},
        channel_by_username={},
        known_channels=frozenset({A, B}),
    )

    assert result.signals[0].pair == (A, B)


def test_a_link_by_bare_id_outside_the_inventory_is_counted() -> None:
    result = description_references(
        {A: "см. t.me/c/987654"},
        channel_by_username={},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 1


def test_an_invite_link_in_a_description_proposes_nothing() -> None:
    result = description_references(
        {A: "Чат t.me/+AbCdEfGh"},
        channel_by_username={},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 0


# --- shared username tokens -------------------------------------------


def test_a_rare_token_proposes_a_pair() -> None:
    signals = shared_username_tokens(
        {A: "gonzo_reviews", B: "gonzo_podcast"},
        thresholds=Thresholds(),
    )

    assert len(signals) == 1
    assert signals[0].pair == (A, B)
    assert signals[0].token == "gonzo"
    assert signals[0].token_channels == 2


def test_a_token_on_too_many_channels_proposes_nothing() -> None:
    """`tech`, `news`, `data` are subjects, not authors."""
    signals = shared_username_tokens(
        {A: "some_tech", B: "other_tech", C: "third_tech", D: "fourth_tech"},
        thresholds=Thresholds(max_token_channels=3),
    )

    assert signals == []


def test_a_token_exactly_at_the_cap_still_proposes() -> None:
    signals = shared_username_tokens(
        {A: "some_tech", B: "other_tech", C: "third_tech"},
        thresholds=Thresholds(max_token_channels=3),
    )

    assert len(signals) == 3
    assert all(signal.token_channels == 3 for signal in signals)


def test_a_rarer_token_scores_above_a_commoner_one() -> None:
    rare = shared_username_tokens(
        {A: "gonzo_one", B: "gonzo_two"},
        thresholds=Thresholds(max_token_channels=3),
    )
    common = shared_username_tokens(
        {A: "some_tech", B: "other_tech", C: "third_tech"},
        thresholds=Thresholds(max_token_channels=3),
    )

    assert rare[0].strength > common[0].strength


def test_a_token_below_the_length_floor_proposes_nothing() -> None:
    signals = shared_username_tokens(
        {A: "ml_one", B: "ml_two"}, thresholds=Thresholds(min_token_length=4)
    )

    assert signals == []


def test_the_rarest_shared_token_is_the_one_recorded() -> None:
    signals = shared_username_tokens(
        {
            A: "gonzo_tech_one",
            B: "gonzo_tech_two",
            C: "third_tech",
            D: "fourth_tech",
        },
        thresholds=Thresholds(max_token_channels=4),
    )

    pair = next(signal for signal in signals if signal.pair == (A, B))
    assert pair.token == "gonzo"
    assert pair.token_channels == 2


def test_a_username_without_a_separator_is_one_token() -> None:
    """Which is what makes a bare name and its suffixed sibling reachable."""
    signals = shared_username_tokens(
        {A: "gonzoml", B: "gonzoml_podcast"}, thresholds=Thresholds()
    )

    assert len(signals) == 1
    assert signals[0].token == "gonzoml"


def test_two_unsplittable_usernames_sharing_nothing_propose_nothing() -> None:
    signals = shared_username_tokens(
        {A: "gonzoml", B: "otherchannel"}, thresholds=Thresholds()
    )

    assert signals == []


def test_a_channel_with_no_username_is_simply_absent() -> None:
    signals = shared_username_tokens(
        {A: "gonzo_one", B: "gonzo_two"}, thresholds=Thresholds()
    )

    assert {channel for signal in signals for channel in signal.pair} == {A, B}


# --- outgoing concentration -------------------------------------------


def test_a_concentrated_channel_proposes_its_target() -> None:
    signals = outgoing_concentration(
        {(A, B): 18, (A, C): 2}, thresholds=Thresholds()
    )

    assert len(signals) == 1
    assert signals[0].pair == (A, B)
    assert signals[0].src == A
    assert signals[0].share == 0.9
    assert signals[0].edges == 20
    assert signals[0].strength == 0.9


def test_a_channel_under_the_edge_floor_proposes_nothing() -> None:
    """Two of three references to one target is 0.67 of nothing."""
    signals = outgoing_concentration(
        {(A, B): 19}, thresholds=Thresholds(min_out_edges=20)
    )

    assert signals == []


def test_a_share_below_the_threshold_proposes_nothing() -> None:
    signals = outgoing_concentration(
        {(A, B): 13, (A, C): 12}, thresholds=Thresholds(max_share_min=0.7)
    )

    assert signals == []


def test_a_share_exactly_at_the_threshold_proposes_and_scores() -> None:
    """Rescaling from the threshold would give this pair a strength of
    zero — in the list and contributing nothing to its own place in it."""
    signals = outgoing_concentration(
        {(A, B): 14, (A, C): 6},
        thresholds=Thresholds(min_out_edges=20, max_share_min=0.7),
    )

    assert len(signals) == 1
    assert signals[0].strength == 0.7


def test_concentration_is_reported_from_the_concentrated_side() -> None:
    signals = outgoing_concentration(
        {(C, A): 20}, thresholds=Thresholds(min_out_edges=20)
    )

    assert signals[0].pair == (A, C)
    assert signals[0].src == C


# --- mutual density ---------------------------------------------------


def test_a_densely_mutual_pair_is_proposed() -> None:
    signals = mutual_density(
        {(A, B): 8, (B, A): 6}, thresholds=Thresholds(min_mutual_edges=5)
    )

    assert len(signals) == 1
    assert signals[0].pair == (A, B)
    assert signals[0].edges_a_to_b == 8
    assert signals[0].edges_b_to_a == 6


def test_a_one_directional_relationship_proposes_nothing() -> None:
    signals = mutual_density(
        {(A, B): 40}, thresholds=Thresholds(min_mutual_edges=5)
    )

    assert signals == []


def test_a_thin_return_direction_proposes_nothing() -> None:
    """Heavy one way and thin the other is an audience, not a peer."""
    signals = mutual_density(
        {(A, B): 40, (B, A): 2}, thresholds=Thresholds(min_mutual_edges=5)
    )

    assert signals == []


def test_the_weaker_direction_sets_the_strength() -> None:
    signals = mutual_density(
        {(A, B): 100, (B, A): 5}, thresholds=Thresholds(min_mutual_edges=5)
    )

    assert signals[0].strength == 0.5


def test_twice_the_minimum_each_way_reaches_full_strength() -> None:
    signals = mutual_density(
        {(A, B): 10, (B, A): 12}, thresholds=Thresholds(min_mutual_edges=5)
    )

    assert signals[0].strength == 1.0


def test_counts_are_reported_against_the_stored_order() -> None:
    signals = mutual_density(
        {(B, A): 9, (A, B): 6}, thresholds=Thresholds(min_mutual_edges=5)
    )

    assert signals[0].pair == (A, B)
    assert signals[0].edges_a_to_b == 6
    assert signals[0].edges_b_to_a == 9


def test_a_tie_between_two_shared_tokens_is_broken_the_same_way_always() -> (
    None
):
    """`_tokens` returns a set, and set iteration order for strings moves
    with the hash seed. Two equally rare shared tokens must still report
    the same one every run."""
    signals = shared_username_tokens(
        {A: "fake_gonzo_main", B: "fake_gonzo_pod"}, thresholds=Thresholds()
    )

    assert len(signals) == 1
    # `gonzo` over `fake`: same rarity, longer token.
    assert signals[0].token == "gonzo"


def test_the_tie_break_does_not_depend_on_username_order() -> None:
    forwards = shared_username_tokens(
        {A: "fake_gonzo_main", B: "fake_gonzo_pod"}, thresholds=Thresholds()
    )
    backwards = shared_username_tokens(
        {B: "fake_gonzo_pod", A: "fake_gonzo_main"}, thresholds=Thresholds()
    )

    assert forwards[0].token == backwards[0].token
