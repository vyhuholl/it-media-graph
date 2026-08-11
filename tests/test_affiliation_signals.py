"""The five affiliation signals, one shape at a time.

Pure functions over plain mappings: no database, no network, no fixture
beyond a dict. Each signal is tested where it fires, where it does not,
and at the boundary between the two — the boundary is the part that
decides how much noise reaches the operator.
"""

from itgraph.affiliation.signals import (
    Thresholds,
    description_references,
    mutual_density,
    named_handle_tokens,
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


# --- named handle tokens ----------------------------------------------


def test_a_signed_handle_proposes_every_pair_in_the_group() -> None:
    """The shape the signal was written for: one hub, three satellites,
    one handle, and the hub's description signing it."""
    signals = named_handle_tokens(
        {
            A: "tg_gonzo",
            B: "logs_gonzo",
            C: "files_gonzo",
            D: "braindump_gonzo",
        },
        {A: "Канал про ИИ\n\nБлоггер @gonzo\nИмя: Олег"},
        thresholds=Thresholds(),
    )

    assert {signal.pair for signal in signals} == {
        (A, B),
        (A, C),
        (A, D),
        (B, C),
        (B, D),
        (C, D),
    }
    assert {signal.token for signal in signals} == {"gonzo"}
    assert {signal.token_channels for signal in signals} == {4}


def test_a_handle_named_by_a_channel_that_does_not_carry_it() -> None:
    """The carrier requirement is the whole precision of the signal.

    Without it a description naming a big brand pulls every channel of
    that brand into one group on a stranger's say-so — measured, that is
    `@yandex` across 13 usernames, and 27 pairs becoming 105.
    """
    signals = named_handle_tokens(
        {A: "yandex_cup", B: "yandex_weather", C: "someone_else"},
        {C: "Пишу про поиск, много про @yandex"},
        thresholds=Thresholds(),
    )

    assert signals == []


def test_a_larger_group_is_not_weaker_evidence() -> None:
    """The rarity formula collapses at its cap; this one must not."""
    two = named_handle_tokens(
        {A: "tg_alpha", B: "logs_alpha"},
        {A: "@alpha"},
        thresholds=Thresholds(),
    )
    five = named_handle_tokens(
        {
            A: "tg_gonzo",
            B: "logs_gonzo",
            C: "files_gonzo",
            D: "braindump_gonzo",
            1005: "chat_gonzo",
        },
        {A: "@gonzo"},
        thresholds=Thresholds(),
    )

    assert min(signal.strength for signal in five) >= max(
        signal.strength for signal in two
    )


def test_a_handle_beginning_with_a_digit_is_read() -> None:
    """`1red2black` cannot be a Telegram username and is still the handle
    an author signs five channels with."""
    signals = named_handle_tokens(
        {A: "tg_1red2black", B: "logs_1red2black"},
        {A: "Блоггер @1red2black\nYouTube: https://youtube.com/@1red2black"},
        thresholds=Thresholds(),
    )

    assert [signal.pair for signal in signals] == [(A, B)]
    assert signals[0].token == "1red2black"


def test_a_handle_naming_no_channel_is_evidence_all_the_same() -> None:
    """The signal never resolves a handle, so naming nothing costs it
    nothing — `@gonzo` is no channel here and still signs two."""
    signals = named_handle_tokens(
        {A: "tg_gonzo", B: "logs_gonzo"},
        {A: "@gonzo"},
        thresholds=Thresholds(),
    )

    assert [signal.pair for signal in signals] == [(A, B)]


def test_a_handle_on_too_many_channels_proposes_nothing() -> None:
    """The cap bounds d(d−1)/2, and says nothing about credibility."""
    signals = named_handle_tokens(
        {A: "tg_gonzo", B: "logs_gonzo", C: "files_gonzo"},
        {A: "@gonzo"},
        thresholds=Thresholds(max_handle_token_channels=2),
    )

    assert signals == []


def test_a_handle_below_the_length_floor_proposes_nothing() -> None:
    signals = named_handle_tokens(
        {A: "tg_gonzo", B: "logs_gonzo"},
        {A: "@gonzo"},
        thresholds=Thresholds(min_token_length=6),
    )

    assert signals == []


def test_a_group_with_no_description_anywhere_proposes_nothing() -> None:
    signals = named_handle_tokens(
        {A: "tg_gonzo", B: "logs_gonzo"},
        {},
        thresholds=Thresholds(),
    )

    assert signals == []


def test_an_empty_description_signs_nothing() -> None:
    """Three of the four channels this signal was written for hold `''`
    rather than no row at all."""
    signals = named_handle_tokens(
        {A: "tg_gonzo", B: "logs_gonzo"},
        {A: "", B: ""},
        thresholds=Thresholds(),
    )

    assert signals == []


def test_a_tie_between_two_signed_handles_is_broken_the_same_way_always() -> (
    None
):
    """`_tokens` returns a set, and its iteration order for strings moves
    with `PYTHONHASHSEED`. The stored handle is what the operator reviews
    the pair on, so it may not depend on the seed."""
    usernames = {A: "fake_gonzo_main", B: "fake_gonzo_pod"}
    descriptions = {A: "@fake и @gonzo"}

    first = named_handle_tokens(
        usernames, descriptions, thresholds=Thresholds()
    )
    again = named_handle_tokens(
        dict(reversed(list(usernames.items()))),
        descriptions,
        thresholds=Thresholds(),
    )

    # `gonzo` over `fake`, the same total order the rarity signal uses:
    # equal claim, longer token.
    assert [signal.token for signal in first] == ["gonzo"]
    assert [signal.token for signal in again] == ["gonzo"]


def test_a_signed_token_is_withheld_from_the_rarity_signal() -> None:
    """One observation, one contribution — the exclusion is what keeps a
    shared token from being read twice."""
    usernames = {A: "tg_gonzo", B: "logs_gonzo"}

    unfiltered = shared_username_tokens(usernames, thresholds=Thresholds())
    filtered = shared_username_tokens(
        usernames, thresholds=Thresholds(), excluding=frozenset({"gonzo"})
    )

    assert [signal.token for signal in unfiltered] == ["gonzo"]
    assert filtered == []


def test_excluding_a_token_leaves_a_weaker_one_standing() -> None:
    """Excluding the strongest token must not drop the pair, only demote
    it to the next claim the two usernames actually share."""
    usernames = {A: "fake_gonzo_main", B: "fake_gonzo_pod"}

    filtered = shared_username_tokens(
        usernames, thresholds=Thresholds(), excluding=frozenset({"gonzo"})
    )

    assert [signal.token for signal in filtered] == ["fake"]
