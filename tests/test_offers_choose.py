"""Which channels a day offers, pure.

Ids and names are fake and spelled so.
"""

from datetime import UTC, date, datetime

from itgraph.derive.offers import choose_offers, offer_day
from itgraph.derive.rising import Rising

FIRST = datetime(2026, 11, 10, 9, tzinfo=UTC)


def rising(tg_id: int, families: int = 2) -> Rising:
    return Rising(
        tg_id=tg_id,
        username=f"fake_{tg_id}",
        title=None,
        families=families,
        families_before=0,
        first_in_window=FIRST,
    )


RANKED = [rising(2000000000 + n, families=9 - n) for n in range(1, 8)]


def test_the_head_of_the_ranking() -> None:
    offers = choose_offers(RANKED, set(), 5)

    assert [offer.channel_id for offer in offers] == [
        item.tg_id for item in RANKED[:5]
    ]
    assert [offer.rank for offer in offers] == [1, 2, 3, 4, 5]
    assert offers[0].families == RANKED[0].families
    assert offers[0].first_in_window == FIRST


def test_the_overflow_waits_for_the_next_day() -> None:
    yesterday = choose_offers(RANKED, set(), 5)
    offered = {offer.channel_id for offer in yesterday}
    # Something new rose overnight, between the two left over.
    newcomer = rising(2000000100, families=3)
    today_ranking = [RANKED[5], newcomer, RANKED[6]]

    today = choose_offers(today_ranking, offered, 5)

    assert [offer.channel_id for offer in today] == [
        RANKED[5].tg_id,
        newcomer.tg_id,
        RANKED[6].tg_id,
    ]
    assert [offer.rank for offer in today] == [1, 2, 3]


def test_a_channel_offered_before_is_not_offered_again() -> None:
    """Whatever came of it: left alone, or marked maybe."""
    offered = {RANKED[0].tg_id, RANKED[2].tg_id}

    offers = choose_offers(RANKED, offered, 5)

    assert offered.isdisjoint(offer.channel_id for offer in offers)
    assert len(offers) == 5


def test_an_empty_day() -> None:
    assert choose_offers([], set(), 5) == []
    assert choose_offers(RANKED, {item.tg_id for item in RANKED}, 5) == []


def test_the_day_is_the_moscow_date() -> None:
    # 23:30 UTC is already the next day in Moscow.
    assert offer_day(datetime(2026, 11, 17, 23, 30, tzinfo=UTC)) == date(
        2026, 11, 18
    )
