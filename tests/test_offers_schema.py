"""What the rising offers' tables refuse, on the suite's schema.

Ids are fake and spelled so.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import DiscoverySource
from itgraph.db.session import Database

CHANNEL = 2000000001
DAY = date(2026, 11, 18)
AT = datetime(2026, 11, 18, 8, tzinfo=UTC)


async def arrange(database: Database) -> None:
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(CHANNEL, "fake_rising", "Fake", False)],
            discovered_via=DiscoverySource.FORWARD,
        )
        await session.execute(
            text(
                "INSERT INTO tg_rising_offer_days (day, computed_at, size) "
                "VALUES (:day, :at, 5)"
            ),
            {"day": DAY, "at": AT},
        )
        await session.commit()


async def offer(database: Database, **columns: Any) -> None:
    values = {
        "day": DAY,
        "channel_id": CHANNEL,
        "rank": 1,
        "families": 2,
        "families_before": 0,
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
            text(f"INSERT INTO tg_rising_offers ({names}) VALUES ({params})"),
            values,
        )
        await session.commit()


CASTS = {
    "decision": "channel_status",
    "kind": "tg_channel_kind",
    "reject_reason": "tg_reject_reason",
}


async def test_an_offer_with_its_decision_is_accepted(
    database: Database,
) -> None:
    await arrange(database)

    await offer(database, decision="seed", kind="media", decided_at=AT)


@pytest.mark.parametrize(
    "columns",
    [
        {"decision": "seed", "decided_at": AT},
        {"decision": "rejected", "decided_at": AT},
        {"decision": "maybe", "kind": "media", "decided_at": AT},
        {"kind": "media"},
        {"decision": "candidate", "decided_at": AT},
        {"decision": "maybe"},
        {"applied_at": AT, "applied": True},
    ],
    ids=[
        "seed-without-kind",
        "rejection-without-reason",
        "maybe-with-kind",
        "kind-without-decision",
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
        await offer(database, rank=2)
