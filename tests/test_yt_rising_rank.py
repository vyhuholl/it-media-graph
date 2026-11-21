"""The YouTube rising ranking, pure: rows and a moment in, the ranking out.

A scenario for each requirement of `youtube-rising-candidates`: what
counts as evidence, when an appearance is new, how families count
across both platforms, what is rising, and how the ranking orders
itself. Every channel id is invented.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ytfakes import channel_id

from itgraph.derive.yt_rising import (
    FeaturedReadings,
    LinkReference,
    YtRising,
    YtRisingRows,
    rank_yt_rising,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
WINDOW = 14
MIN_FAMILIES = 2
FRESH = 30

TG_A = "tg:1001"  # Telegram families
TG_B = "tg:1002"
YT_A = "yt:UCaaaaaaaaaaaaaaaaaaaaA"  # YouTube families
YT_B = "yt:UCaaaaaaaaaaaaaaaaaaaaB"

EMPTY = YtRisingRows(inventory=frozenset())


def _rank(rows: YtRisingRows) -> YtRising:
    return rank_yt_rising(
        rows,
        NOW,
        window_days=WINDOW,
        min_families=MIN_FAMILIES,
        answers_fresh_days=FRESH,
    )


def _link(channel: int, family: str, *, days_ago: float = 1) -> LinkReference:
    return LinkReference(
        channel=channel_id(channel),
        family=family,
        at=NOW - timedelta(days=days_ago),
    )


def _readings(
    seed: int,
    family: str,
    *,
    newest: tuple[int, ...],
    previous: tuple[int, ...] | None,
    newest_days_ago: float = 1,
) -> FeaturedReadings:
    return FeaturedReadings(
        seed=channel_id(seed),
        family=family,
        newest_at=NOW - timedelta(days=newest_days_ago),
        newest=tuple(channel_id(n) for n in newest),
        previous=(
            tuple(channel_id(n) for n in previous)
            if previous is not None
            else None
        ),
    )


# --- what counts as evidence ------------------------------------------


def test_all_three_sources_name_one_channel() -> None:
    """A Telegram post, a video description and a new featuring are all
    evidence for the channel they name — three families, rising."""
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(90, TG_A),),
            references=(_link(90, YT_A),),
            featured=(_readings(3, YT_B, newest=(90,), previous=()),),
        )
    )

    [channel] = ranked.links
    assert channel.channel_id == channel_id(90)
    assert channel.families == 3
    assert channel.families_before == 0


def test_a_standing_feature_is_not_this_ranking_s_evidence() -> None:
    """A channel listed in the newest reading and the one before it is
    no evidence here; its new list-mate is."""
    ranked = _rank(
        replace(
            EMPTY,
            featured=(
                _readings(3, YT_A, newest=(90, 91), previous=(90,)),
                _readings(4, YT_B, newest=(90,), previous=(90,)),
            ),
            references=(_link(91, TG_A),),  # a fourth family for 91
        )
    )

    [channel] = ranked.links
    assert channel.channel_id == channel_id(91)


def test_what_could_not_be_counted_comes_back_as_counts() -> None:
    """Awaiting and not-found references name no channel and are
    counted as such."""
    ranked = _rank(
        replace(EMPTY, awaiting_lookup=7, not_found=3, lists_out_of_date=2)
    )

    assert (ranked.awaiting_lookup, ranked.not_found) == (7, 3)
    assert ranked.lists_out_of_date == 2


# --- an appearance is new in a seed's list -----------------------------


def test_the_first_reading_establishes_the_list() -> None:
    """One reading, five channels: none of the five is an appearance,
    and their ids still count as the standing queue reads them."""
    ranked = _rank(
        replace(
            EMPTY,
            featured=(
                _readings(3, YT_A, newest=(1, 2, 3, 4, 90), previous=None),
            ),
            links=(_link(90, TG_A), _link(90, TG_B)),
        )
    )

    [channel] = ranked.links
    assert channel.channel_id == channel_id(90)
    assert channel.families == 2  # the listing itself contributed none


def test_a_channel_taken_off_and_put_back_appears_again() -> None:
    """Listed, then dropped, then listed again: the re-listing is an
    appearance, dated by the reading that lists it again — the previous
    reading, of any age, is the one that dropped it."""
    relisted = _readings(3, YT_A, newest=(90,), previous=())
    ranked = _rank(
        replace(EMPTY, featured=(relisted,), references=(_link(90, TG_B),))
    )

    [channel] = ranked.links
    assert channel.channel_id == channel_id(90)
    assert channel.first_in_window == relisted.newest_at


def test_a_list_out_of_date_contributes_nothing() -> None:
    ranked = _rank(
        replace(
            EMPTY,
            featured=(
                _readings(
                    3, YT_A, newest=(90,), previous=(), newest_days_ago=31
                ),
            ),
            links=(_link(90, TG_A),),
        )
    )

    assert ranked.links == []


# --- families across both platforms ------------------------------------


def test_one_family_however_many_links() -> None:
    """Two seeds confirmed as one family — a description reference from
    one, a new featuring from the other — refer as one family."""
    ranked = _rank(
        replace(
            EMPTY,
            references=(_link(90, "yt:shared"), _link(90, "yt:shared")),
            featured=(_readings(4, "yt:shared", newest=(90,), previous=()),),
        )
    )

    assert ranked.links == []


def test_a_family_across_platforms_is_two_families() -> None:
    """A Telegram family and a YouTube family, however many times the
    first links, are two."""
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(90, TG_A), _link(90, TG_A), _link(90, TG_A)),
            references=(_link(90, YT_B),),
        )
    )

    [channel] = ranked.links
    assert channel.families == 2


def test_the_same_family_key_never_meets_across_platforms() -> None:
    """The prefix is what keeps a Telegram family and a YouTube family
    apart, even at the same key."""
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(90, "tg:2001"),),
            references=(_link(90, "yt:2001"),),
        )
    )

    [channel] = ranked.links
    assert channel.families == 2


# --- what is rising ----------------------------------------------------


def test_two_families_in_the_window() -> None:
    relisted = _readings(3, YT_B, newest=(90,), previous=())
    ranked = _rank(
        replace(
            EMPTY, links=(_link(90, TG_A, days_ago=7),), featured=(relisted,)
        )
    )

    [channel] = ranked.links
    assert channel.families == 2
    assert channel.first_in_window == NOW - timedelta(days=7)


def test_one_family_is_not_enough() -> None:
    """Three references of one family, however often it writes: one
    family."""
    ranked = _rank(
        replace(
            EMPTY,
            references=(
                _link(90, "yt:one"),
                _link(90, "yt:one", days_ago=2),
                _link(90, "yt:one", days_ago=3),
            ),
        )
    )

    assert ranked.links == []


def test_old_evidence_is_not_in_the_window() -> None:
    """Referred to by three families, every reference older than the
    window: not rising."""
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(90, TG_A, days_ago=15),),
            references=(
                _link(90, YT_A, days_ago=20),
                _link(90, YT_B, days_ago=40),
            ),
        )
    )

    assert ranked.links == []


def test_evidence_is_dated_by_its_content_not_its_parsing() -> None:
    """An old video's description naming a channel is old evidence,
    whenever it was parsed: the publication time decides."""
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(90, TG_A), _link(90, TG_B)),
            references=(_link(90, YT_A, days_ago=60),),
        )
    )

    [channel] = ranked.links
    assert channel.families == 2
    assert channel.families_before == 1


def test_a_known_channel_is_never_ranked() -> None:
    """A record in any status — candidate, maybe, rejected, gone — is
    not ranked, nor counted among those left out."""
    ranked = _rank(
        replace(
            EMPTY,
            inventory=frozenset(channel_id(n) for n in (90, 91, 92, 93, 94)),
            links=tuple(_link(n, TG_A) for n in (90, 91, 92, 93, 94))
            + tuple(_link(n, TG_B) for n in (90, 91, 92, 93, 94)),
        )
    )

    assert ranked.links == []


# --- how rising link candidates rank -----------------------------------


def test_more_families_first() -> None:
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(90, TG_A), _link(90, TG_B)),
            references=(_link(91, YT_A), _link(91, YT_B)),
            featured=(_readings(5, "yt:C", newest=(90,), previous=()),),
        )
    )

    assert [row.channel_id for row in ranked.links] == [
        channel_id(90),
        channel_id(91),
    ]


def test_the_newer_comes_first_on_a_tie() -> None:
    """Two families each; one was referred to by four families before
    the window and the other by none — the newer ranks first."""
    ranked = _rank(
        replace(
            EMPTY,
            links=(
                _link(90, TG_A),
                _link(90, TG_B),
                _link(90, "tg:old1", days_ago=30),
                _link(90, "tg:old2", days_ago=31),
                _link(90, "tg:old3", days_ago=32),
                _link(90, "tg:old4", days_ago=33),
                _link(91, TG_B),
            ),
            references=(_link(91, YT_A),),
        )
    )

    assert [row.channel_id for row in ranked.links] == [
        channel_id(91),
        channel_id(90),
    ]
    assert ranked.links[1].families_before == 4


def test_a_tie_ends_in_a_fixed_order_by_id() -> None:
    ranked = _rank(
        replace(
            EMPTY,
            links=(_link(5, TG_A), _link(2, TG_A)),
            references=(_link(5, YT_B), _link(2, YT_B)),
        )
    )

    assert [row.channel_id for row in ranked.links] == [
        channel_id(2),
        channel_id(5),
    ]


def test_evidence_after_the_moment_is_not_read() -> None:
    future = LinkReference(
        channel=channel_id(90), family=TG_A, at=NOW + timedelta(days=1)
    )
    ranked = _rank(replace(EMPTY, links=(future,), references=(future,)))

    assert ranked.links == []
