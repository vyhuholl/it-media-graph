"""The three YouTube affiliation signals, one shape at a time.

Pure functions over plain mappings: no database, no network, no fixture
beyond a dict. Each signal is tested where it fires, where it does not,
and at the boundary between the two — the boundary is the part that
decides how much noise reaches the operator.

Ids are fakes in the shape the inventory's check holds: ``UC`` and
twenty-two characters, and `fake` spelled out in the middle, so a
failing test cannot be mistaken for a real channel.
"""

from itgraph.yt.affiliation.signals import (
    DEFAULT_THRESHOLDS,
    YtRefDirection,
    corroborated_token_groups,
    description_references,
    ordered,
    shared_title_tokens,
)

A = "UCfake_signals_000000001"
B = "UCfake_signals_000000002"
C = "UCfake_signals_000000003"
D = "UCfake_signals_000000004"
E = "UCfake_signals_000000005"
F = "UCfake_signals_000000006"


# --- title tokens ------------------------------------------------------


def test_two_titles_sharing_a_distinctive_token_propose_the_pair() -> None:
    signals = shared_title_tokens(
        {A: "Data Engineering Show", B: "Data Engineering Radio"},
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert [(s.pair, s.token, s.token_channels) for s in signals] == [
        ((A, B), "engineering", 2)
    ]
    # Rarest scores (M + 1 - d) / M: two carriers under a cap of three.
    assert signals[0].strength == (3 + 1 - 2) / 3


def test_a_token_past_the_cap_produces_nothing_on_its_own() -> None:
    signals = shared_title_tokens(
        {
            A: "Tech Weekly One",
            B: "Tech Weekly Two",
            C: "Tech Weekly Three",
            D: "Tech Weekly Four",
        },
        thresholds=DEFAULT_THRESHOLDS,
    )

    # `tech` and `weekly` are both on four channels; nothing else is
    # shared, so the cap leaves no pair at all.
    assert signals == []


def test_a_short_token_is_not_evidence() -> None:
    signals = shared_title_tokens(
        {A: "Go Talks", B: "Go Radio"},
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert signals == []


def test_a_cyrillic_token_counts() -> None:
    signals = shared_title_tokens(
        {A: "Данные и боты", B: "Данные и графики"},
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert [s.token for s in signals] == ["данные"]


def test_punctuation_inside_a_title_does_not_split_a_claim() -> None:
    signals = shared_title_tokens(
        {A: "JetBrains-TV", B: "JetBrains Blog"},
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert [(s.token, s.token_channels) for s in signals] == [("jetbrains", 2)]


def test_the_rarest_of_several_shared_tokens_wins() -> None:
    signals = shared_title_tokens(
        {
            A: " gonzo podcast main",
            B: " gonzo podcast live",
            C: "gonzo interviews",
        },
        thresholds=DEFAULT_THRESHOLDS,
    )

    by_pair = {s.pair: s.token for s in signals}
    # `podcast` is on two channels and `gonzo` on three; each pair
    # carries the rarest token it shares.
    assert by_pair[(A, B)] == "podcast"
    assert by_pair[(A, C)] == "gonzo"
    assert by_pair[(B, C)] == "gonzo"


def test_excluded_tokens_are_skipped_not_filtered_afterwards() -> None:
    """A pair can still be proposed on a weaker token it also shares."""
    signals = shared_title_tokens(
        {A: "gonzo podcast main", B: "gonzo podcast live"},
        thresholds=DEFAULT_THRESHOLDS,
        excluding=frozenset({"gonzo"}),
    )

    assert [s.token for s in signals] == ["podcast"]


# --- description references --------------------------------------------


def test_a_description_linking_another_channel_proposes_it() -> None:
    result = description_references(
        {A: f"Main channel: youtube.com/channel/{B}"},
        handles={},
        known_channels=frozenset({A, B}),
    )

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.pair == (A, B)
    assert signal.strength == 0.5
    assert signal.direction is YtRefDirection.A_TO_B


def test_a_handle_link_resolves_through_the_carried_handles() -> None:
    result = description_references(
        {A: "Sibling: https://www.youtube.com/@sibling"},
        handles={"sibling": frozenset({B})},
        known_channels=frozenset({A, B}),
    )

    assert result.signals[0].pair == (A, B)


def test_a_bare_mention_of_a_carried_handle_resolves() -> None:
    result = description_references(
        {A: "Podcast at @sibling"},
        handles={"sibling": frozenset({B})},
        known_channels=frozenset({A, B}),
    )

    assert result.signals[0].pair == (A, B)


def test_a_reference_from_the_higher_id_is_recorded_as_such() -> None:
    """The pair is ordered lexicographically, so direction needs an anchor."""
    result = description_references(
        {B: f"Main channel: youtube.com/channel/{A}"},
        handles={},
        known_channels=frozenset({A, B}),
    )

    assert result.signals[0].pair == (A, B)
    assert result.signals[0].direction is YtRefDirection.B_TO_A


def test_references_both_ways_weigh_more_and_appear_once() -> None:
    result = description_references(
        {
            A: f"Talks: youtube.com/channel/{B}",
            B: f"Blog: youtube.com/channel/{A}",
        },
        handles={},
        known_channels=frozenset({A, B}),
    )

    assert len(result.signals) == 1
    assert result.signals[0].strength == 1.0
    assert result.signals[0].direction is YtRefDirection.MUTUAL


def test_a_description_naming_its_own_channel_forms_no_pair() -> None:
    result = description_references(
        {A: f"Subscribe: youtube.com/channel/{A}"},
        handles={},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    assert result.refs == {}


def test_a_reference_outside_the_inventory_is_counted_not_proposed() -> None:
    result = description_references(
        {
            A: "Uncollected: youtube.com/@elsewhere "
            "and youtube.com/channel/UCfake_signals_999999999"
        },
        handles={},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    # A handle link and a channel id, both naming nobody held.
    assert result.refs_outside_inventory == 2


def test_an_ambiguous_handle_names_no_channel_and_counts_nowhere() -> None:
    result = description_references(
        {A: "See @twin"},
        handles={"twin": frozenset({B, C})},
        known_channels=frozenset({A, B, C}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 0


def test_a_custom_url_names_no_channel() -> None:
    result = description_references(
        {A: "Visit youtube.com/c/SomeBrand"},
        handles={},
        known_channels=frozenset({A, B}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 0


def test_a_legacy_name_is_counted_outside() -> None:
    result = description_references(
        {A: "Old: youtube.com/user/somebody"},
        handles={},
        known_channels=frozenset({A}),
    )

    assert result.signals == []
    assert result.refs_outside_inventory == 1


# --- corroborated token groups -----------------------------------------


def _google_corpus() -> tuple[dict[str, str], dict[str, str]]:
    titles = {
        A: "Google Developers",
        B: "Google Tech Talks",
        C: "Google Cloud",
        D: "Unrelated Show",
    }
    descriptions = {A: f"Talks: youtube.com/channel/{B}"}
    return titles, descriptions


def test_a_reference_between_carriers_proposes_the_whole_group() -> None:
    titles, descriptions = _google_corpus()
    groups, result = corroborated_token_groups(
        titles,
        descriptions,
        handles={},
        known_channels=frozenset(titles),
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert sorted(g.token for g in groups) == ["google"] * 3
    assert sorted(g.pair for g in groups) == [(A, B), (A, C), (B, C)]
    assert {g.token_channels for g in groups} == {3}
    assert all(g.strength == 1.0 for g in groups)
    # The reference itself is still a reference signal.
    assert [(s.pair, s.strength) for s in result.signals] == [((A, B), 0.5)]


def test_a_reference_to_a_non_carrier_corroborates_nothing() -> None:
    groups, _ = corroborated_token_groups(
        {A: "Brand One", B: "Brand Two", C: "Plain Show"},
        {A: f"See youtube.com/channel/{C}"},
        handles={},
        known_channels=frozenset({A, B, C}),
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert groups == []


def test_an_unresolvable_reference_corroborates_nothing() -> None:
    groups, result = corroborated_token_groups(
        {A: "Brand One", B: "Brand Two"},
        {A: "Sibling: youtube.com/@unknown_sibling"},
        handles={},
        known_channels=frozenset({A, B}),
        thresholds=DEFAULT_THRESHOLDS,
    )

    assert groups == []
    assert result.refs_outside_inventory == 1


def test_an_over_cap_group_is_not_proposed() -> None:
    titles = {A: "Solo Token Show", B: "Solo Token Blog"}
    titles.update(
        {
            f"UCfake_signals_{n:09d}": f"Solo Token Extra {n}"
            for n in range(10, 14)
        }
    )
    descriptions = {A: f"See youtube.com/channel/{B}"}

    capped = corroborated_token_groups(
        titles,
        descriptions,
        handles={},
        known_channels=frozenset(titles),
        thresholds=DEFAULT_THRESHOLDS,
    )[0]
    assert len(capped) > 0

    tight = corroborated_token_groups(
        titles,
        descriptions,
        handles={},
        known_channels=frozenset(titles),
        # A cap below the carrier count drops the whole group.
        thresholds=DEFAULT_THRESHOLDS.__class__(
            min_token_length=DEFAULT_THRESHOLDS.min_token_length,
            max_token_channels=DEFAULT_THRESHOLDS.max_token_channels,
            max_corroborated_channels=3,
        ),
    )[0]
    assert tight == []


def test_a_larger_group_is_not_weaker_evidence() -> None:
    small_titles = {A: "Duet Show", B: "Duet Blog"}
    small = corroborated_token_groups(
        small_titles,
        {A: f"See youtube.com/channel/{B}"},
        handles={},
        known_channels=frozenset(small_titles),
        thresholds=DEFAULT_THRESHOLDS,
    )[0]

    big_titles = {
        f"UCfake_signals_{n:09d}": f"Big Family Channel {n}" for n in range(6)
    }
    big = corroborated_token_groups(
        big_titles,
        {
            "UCfake_signals_000000000": "youtube.com/channel/UCfake_signals_000000001"
        },
        handles={},
        known_channels=frozenset(big_titles),
        thresholds=DEFAULT_THRESHOLDS,
    )[0]

    assert small[0].strength == big[0].strength == 1.0


# --- the pair ordering --------------------------------------------------


def test_pairs_are_ordered_lexicographically() -> None:
    assert ordered(B, A) == (A, B)
    assert ordered(A, B) == (A, B)
