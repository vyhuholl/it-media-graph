"""What the YouTube rising offers' tables refuse, on the suite's schema.

Ids are fake and spelled so.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.db.models import YtDiscoverySource
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels

CHANNEL = "UCfakeRisingOffers000001"
DAY = date(2026, 11, 19)
AT = datetime(2026, 11, 19, 8, tzinfo=UTC)


async def arrange(database: Database) -> None:
    async with database.session() as session:
        await create_channels(
            session, [CHANNEL], discovered_via=YtDiscoverySource.MANUAL
        )
        await session.execute(
            text(
                "INSERT INTO yt_rising_offer_days "
                "(day, computed_at, size_chart, size_links) "
                "VALUES (:day, :at, 3, 3)"
            ),
            {"day": DAY, "at": AT},
        )
        await session.commit()


async def offer(database: Database, **columns: Any) -> None:
    values = {
        "day": DAY,
        "channel_id": CHANNEL,
        "ranking": "link",
        "families": 2,
        "families_before": 0,
        "discovered_via": "telegram",
        "topic": "en",
        "offered_at": AT,
        **columns,
    }
    names = ", ".join(values)
    params = ", ".join(
        f"CAST(:{name} AS {cast})" if (cast := CASTS.get(name)) else f":{name}"
        for name in values
    )
    async with database.session() as session:
        await session.execute(
            text(f"INSERT INTO yt_rising_offers ({names}) VALUES ({params})"),
            values,
        )
        await session.commit()


CASTS = {
    "decision": "channel_status",
    "kind": "yt_channel_kind",
    "language": "yt_lang",
    "reject_reason": "yt_reject_reason",
}


async def test_a_link_offer_with_its_decision_is_accepted(
    database: Database,
) -> None:
    await arrange(database)

    await offer(
        database,
        decision="seed",
        kind="media",
        language="en",
        decided_at=AT,
    )


async def test_a_chart_offer_is_accepted(database: Database) -> None:
    await arrange(database)

    await offer(
        database,
        ranking="chart",
        families=None,
        region="RU",
        position=4,
        discovered_via="chart",
        topic="ru",
    )


@pytest.mark.parametrize(
    "columns",
    [
        {"decision": "seed", "language": "en", "decided_at": AT},
        {"decision": "seed", "kind": "media", "decided_at": AT},
        {"decision": "rejected", "decided_at": AT},
        {"decision": "maybe", "kind": "media", "decided_at": AT},
        {"kind": "media"},
        {"language": "en"},
        {"decision": "candidate", "decided_at": AT},
        {"decision": "maybe"},
        {"applied_at": AT, "applied": True},
    ],
    ids=[
        "seed-without-kind",
        "seed-without-language",
        "rejection-without-reason",
        "maybe-with-kind",
        "kind-without-decision",
        "language-without-decision",
        "candidate-is-no-decision",
        "decision-without-time",
        "applied-before-decided",
    ],
)
async def test_an_offer_that_does_not_add_up_is_refused(
    database: Database, columns: dict[str, Any]
) -> None:
    await arrange(database)

    with pytest.raises(IntegrityError):
        await offer(database, **columns)


async def test_a_channel_is_offered_once(database: Database) -> None:
    await arrange(database)
    await offer(database)

    with pytest.raises(IntegrityError):
        await offer(database, ranking="chart", region="US", position=1)
