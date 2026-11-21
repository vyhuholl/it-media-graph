"""Which channels a day of YouTube's rising offers, pure.

Ids are fake and spelled so.
"""

from datetime import UTC, datetime, timedelta

from itgraph.db.models import YtDiscoverySource
from itgraph.derive.yt_offers import (
    choose_offers,
    offer_day,
    strongest_sources,
)
from itgraph.derive.yt_rising import (
    FeaturedReadings,
    LinkReference,
    RisingChannel,
    YtRisingRows,
)

MOMENT = datetime(2026, 11, 19, 8, tzinfo=UTC)
FIRST = datetime(2026, 11, 10, 9, tzinfo=UTC)


def link(n: int, families: int = 2) -> RisingChannel:
    return RisingChannel(
        channel_id=f"UCfakeRisingLinks0000{n}",
        families=families,
        families_before=0,
        first_in_window=FIRST,
    )


LINKS = [link(n, families=9 - n) for n in range(1, 5)]  # 4 link channels


def test_the_head_of_the_ranking() -> None:
    offers = choose_offers(LINKS, set(), size_links=3)

    # The three ranked highest, in rank order.
    assert [o.channel_id for o in offers] == [
        LINKS[0].channel_id,
        LINKS[1].channel_id,
        LINKS[2].channel_id,
    ]
    assert offers[0].families == LINKS[0].families
    assert offers[0].first_in_window == FIRST
    # The rest are not offered.
    assert len(offers) == 3


def test_the_overflow_waits_for_the_next_day() -> None:
    yesterday = choose_offers(LINKS, set(), size_links=3)
    offered = {o.channel_id for o in yesterday}
    # Something new rose overnight, between the one left over.
    newcomer = link(90, families=3)
    today_ranking = [LINKS[3], newcomer]

    today = choose_offers(today_ranking, offered, size_links=3)

    assert [o.channel_id for o in today] == [
        LINKS[3].channel_id,
        newcomer.channel_id,
    ]


def test_a_channel_offered_before_is_not_offered_again() -> None:
    """Whatever came of it."""
    offered = {LINKS[0].channel_id}

    offers = choose_offers(LINKS, offered, size_links=3)

    assert offered.isdisjoint(o.channel_id for o in offers)
    assert len(offers) == 3


def test_an_empty_day() -> None:
    assert choose_offers([], set(), size_links=3) == []


def test_the_day_is_the_chat_s_date() -> None:
    # 23:30 UTC is already the next day in Moscow.
    assert offer_day(datetime(2026, 11, 18, 23, 30, tzinfo=UTC)) == (
        datetime(2026, 11, 19, 0, tzinfo=UTC).date()
    )


def _rows(
    links: list[LinkReference] | None = None,
    references: list[LinkReference] | None = None,
    featured: list[FeaturedReadings] | None = None,
) -> YtRisingRows:
    return YtRisingRows(
        inventory=frozenset(),
        links=tuple(links or ()),
        references=tuple(references or ()),
        featured=tuple(featured or ()),
    )


def test_the_strongest_source_wins_and_a_tie_is_ordered() -> None:
    channel = "UCfakeRisingLinks00009"
    telegram = [
        LinkReference(channel, "tg:fake_a", MOMENT),
        LinkReference(channel, "tg:fake_b", MOMENT),
    ]
    mentions = [LinkReference(channel, "yt:fake_c", MOMENT)]

    sources = strongest_sources(
        _rows(links=telegram, references=mentions),
        MOMENT,
        window_days=14,
        answers_fresh_days=30,
    )

    assert sources[channel] is YtDiscoverySource.TELEGRAM
    # Two families against one, and a tie goes telegram, mention, featured.
    tie = strongest_sources(
        _rows(
            links=[LinkReference(channel, "tg:fake_a", MOMENT)],
            references=[LinkReference(channel, "yt:fake_c", MOMENT)],
        ),
        MOMENT,
        window_days=14,
        answers_fresh_days=30,
    )
    assert tie[channel] is YtDiscoverySource.TELEGRAM
    tie_two = strongest_sources(
        _rows(
            references=[LinkReference(channel, "yt:fake_c", MOMENT)],
            featured=[
                FeaturedReadings(
                    seed="UCfakeRisingSeed0001",
                    family="yt:fake_d",
                    newest_at=MOMENT,
                    newest=(channel,),
                    previous=(),
                )
            ],
        ),
        MOMENT,
        window_days=14,
        answers_fresh_days=30,
    )
    assert tie_two[channel] is YtDiscoverySource.MENTION


def test_only_evidence_inside_the_window_counts() -> None:
    channel = "UCfakeRisingLinks00008"
    rows = _rows(
        links=[
            LinkReference(channel, "tg:fake_a", MOMENT),
            # Before the window, and after the moment: not evidence.
            LinkReference(channel, "tg:fake_old", MOMENT - timedelta(days=20)),
            LinkReference(
                channel, "tg:fake_future", MOMENT + timedelta(days=1)
            ),
        ],
        references=[
            LinkReference(channel, "yt:fake_b", MOMENT),
            LinkReference(channel, "yt:fake_c", MOMENT),
        ],
    )

    sources = strongest_sources(
        rows, MOMENT, window_days=14, answers_fresh_days=30
    )

    assert sources[channel] is YtDiscoverySource.MENTION
