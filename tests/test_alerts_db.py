"""The alert queue and the pass that fills it, against a real Postgres.

Two of the guarantees here are the database's rather than the code's —
that one post and band cannot be raised twice, and that two claimers
cannot take one row — and a test against a fake would prove neither.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from itgraph.alerts.cascade import Cascade
from itgraph.alerts.run import run_cascades
from itgraph.db.alerts import (
    claim_undelivered,
    count_delivered_since,
    digest_is_due,
    failing_alerts,
    mark_delivered,
    mark_failed,
    raise_cascades,
    raised_bands,
    record_verdict,
)
from itgraph.db.models import AlertDelivery, AlertKind, AlertVerdict
from itgraph.db.session import Database

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
PUBLISHER = 1000000001
POST = 500


async def seed(
    database: Database,
    *,
    reposters: int = 0,
    published_ago: timedelta = timedelta(hours=1),
    repost_ago: timedelta = timedelta(minutes=30),
    album: int | None = None,
    families: list[tuple[int, int]] | None = None,
    publisher_reposts: bool = False,
) -> None:
    """A published post, some channels carrying it, and the edges between.

    Written directly rather than collected: what is under test is the
    detection and the queue, and going through the collector would test
    the collector.
    """
    published = NOW - published_ago
    reposted = NOW - repost_ago
    async with database.session() as session:
        channels = [PUBLISHER] + [PUBLISHER + n for n in range(1, 9)]
        for tg_id in channels:
            await session.execute(
                text(
                    "INSERT INTO channels "
                    "(tg_id, username, title, discovered_via, status) "
                    "VALUES (:id, :name, 'Example', 'manual', 'seed')"
                ),
                {"id": tg_id, "name": f"example_{tg_id}"},
            )
        payload = {
            "_": "Message",
            "id": POST,
            "date": published.isoformat(),
            "message": "a post that travelled",
        }
        if album is not None:
            payload["grouped_id"] = album
        await session.execute(
            text(
                "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                "VALUES (:c, :m, CAST(:p AS jsonb))"
            ),
            {"c": PUBLISHER, "m": POST, "p": _json(payload)},
        )

        sources = [PUBLISHER + n for n in range(1, reposters + 1)]
        if publisher_reposts:
            sources.append(PUBLISHER)
        for index, src in enumerate(sources):
            await session.execute(
                text(
                    "INSERT INTO edges (src_channel_id, dst_channel_id, kind, "
                    "msg_id, published_at, dst_msg_id, dst_published_at) "
                    "VALUES (:src, :dst, 'forward', :mid, :at, :dm, :dp)"
                ),
                {
                    "src": src,
                    "dst": PUBLISHER,
                    "mid": 9000 + index,
                    "at": reposted,
                    "dm": POST,
                    "dp": published,
                },
            )
        for a, b in families or []:
            await session.execute(
                text(
                    "INSERT INTO affiliation_candidates "
                    "(channel_a, channel_b, score, decision, decided_at) "
                    "VALUES (:a, :b, 1.0, 'confirmed', now())"
                ),
                {"a": min(a, b), "b": max(a, b)},
            )


def _json(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload)


async def alerts(database: Database) -> list[tuple[int, int, float]]:
    async with database.session() as session:
        rows = await session.execute(
            text("SELECT msg_id, band, value FROM alerts ORDER BY band")
        )
        return [tuple(row) for row in rows.all()]  # type: ignore[misc]


# --- the pass ------------------------------------------------------


async def test_two_families_raise_an_alert(database: Database) -> None:
    await seed(database, reposters=2)

    summary = await run_cascades(database, bands=(2, 3), now=NOW)

    assert summary.raised == 1
    assert await alerts(database) == [(POST, 2, 2.0)]


async def test_one_reposter_raises_nothing(database: Database) -> None:
    """One family carrying a post is ~19 events a day. That is noise."""
    await seed(database, reposters=1)

    summary = await run_cascades(database, bands=(2, 3), now=NOW)

    assert summary.raised == 0
    assert await alerts(database) == []


async def test_affiliated_reposters_count_once(database: Database) -> None:
    """Two channels sharing an author are one source, not two."""
    await seed(
        database,
        reposters=2,
        families=[(PUBLISHER + 1, PUBLISHER + 2)],
    )

    await run_cascades(database, bands=(2, 3), now=NOW)

    assert await alerts(database) == []


async def test_the_publishers_own_family_does_not_count(
    database: Database,
) -> None:
    await seed(database, reposters=1, publisher_reposts=True)

    await run_cascades(database, bands=(2,), now=NOW)

    assert await alerts(database) == []


async def test_a_post_outside_the_window_raises_nothing(
    database: Database,
) -> None:
    """The structural answer to the first-run problem.

    No record of already-handled posts is needed, because a post older
    than the window cannot cross a within-window threshold.
    """
    await seed(
        database,
        reposters=3,
        published_ago=timedelta(days=200),
        repost_ago=timedelta(days=200) - timedelta(hours=1),
    )

    summary = await run_cascades(database, bands=(2,), now=NOW)

    assert summary.raised == 0


async def test_a_second_run_raises_nothing(database: Database) -> None:
    await seed(database, reposters=2)

    first = await run_cascades(database, bands=(2,), now=NOW)
    second = await run_cascades(database, bands=(2,), now=NOW)

    assert (first.raised, second.raised) == (1, 0)
    assert len(await alerts(database)) == 1


async def test_a_growing_cascade_escalates(database: Database) -> None:
    await seed(database, reposters=2)
    await run_cascades(database, bands=(2, 3), now=NOW)

    # A third family picks it up.
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO edges (src_channel_id, dst_channel_id, kind, "
                "msg_id, published_at, dst_msg_id, dst_published_at) "
                "VALUES (:src, :dst, 'forward', 9500, :at, :dm, :dp)"
            ),
            {
                "src": PUBLISHER + 3,
                "dst": PUBLISHER,
                "at": NOW - timedelta(minutes=10),
                "dm": POST,
                "dp": NOW - timedelta(hours=1),
            },
        )

    await run_cascades(database, bands=(2, 3), now=NOW)

    assert await alerts(database) == [(POST, 2, 2.0), (POST, 3, 3.0)]


async def test_an_album_is_one_alert_naming_its_first_part(
    database: Database,
) -> None:
    """The grouping comes from `raw_messages`, not from `edges`.

    `edges.grouped_id` describes the repost, not the post being carried,
    and confusing the two is the easiest mistake in the pass.
    """
    await seed(database, reposters=2, album=777)
    async with database.session() as session:
        # A second part of the same album, with a lower id.
        await session.execute(
            text(
                "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                "VALUES (:c, :m, CAST(:p AS jsonb))"
            ),
            {
                "c": PUBLISHER,
                "m": POST - 1,
                "p": _json(
                    {
                        "_": "Message",
                        "id": POST - 1,
                        "date": (NOW - timedelta(hours=1)).isoformat(),
                        "grouped_id": 777,
                    }
                ),
            },
        )

    await run_cascades(database, bands=(2,), now=NOW)

    # One alert, and it names the album's first part.
    assert await alerts(database) == [(POST - 1, 2, 2.0)]


async def test_the_summary_reports_activity_and_freshness_apart(
    database: Database,
) -> None:
    """Two facts that must not be conflated, and once were.

    How long ago somebody last reposted something is a fact about the
    world; how far collection has run ahead of derivation is a fact about
    the pipeline. Reporting the first as the second told the operator to
    run `derive` because nobody had reposted anything for three hours.
    """
    await seed(database, reposters=2)

    summary = await run_cascades(database, bands=(2,), now=NOW)

    assert summary.newest_repost_at is not None
    assert "newest repost" in summary.line()
    # Derivation is not behind: the test wrote the edges directly, so
    # nothing is owed and no instruction is given.
    assert "run `itgraph derive`" not in summary.line()


async def test_a_quiet_window_is_not_reported_as_stale(
    database: Database,
) -> None:
    """The bug this pass shipped with, as a test.

    Hours without a repost is the normal state of this signal — it fires
    about once a day — and must never read as a broken pipeline.
    """
    await seed(
        database,
        reposters=2,
        repost_ago=timedelta(days=5),
        published_ago=timedelta(days=5),
    )

    summary = await run_cascades(database, bands=(2,), now=NOW)

    assert summary.considered == 0
    assert "run `itgraph derive`" not in summary.line()


async def test_collection_running_ahead_of_derivation_is_reported(
    database: Database,
) -> None:
    """The situation that *does* warrant the instruction."""
    await seed(database, reposters=2)
    async with database.session() as session:
        # Messages collected long after the last edge was derived.
        await session.execute(
            text("UPDATE edges SET derived_at = now() - interval '9 hours'")
        )
        await session.execute(
            text("UPDATE raw_messages SET fetched_at = now()")
        )

    summary = await run_cascades(database, bands=(2,), now=NOW)

    assert summary.undelivered_derivation() is not None
    assert "run `itgraph derive`" in summary.line()


async def test_an_idle_pipeline_is_not_reported_as_behind(
    database: Database,
) -> None:
    """Neither stage having done anything lately is not a fault.

    Overnight, collection is asleep and derivation has nothing to do; a
    warning here would fire every night and mean nothing.
    """
    await seed(database, reposters=2)
    async with database.session() as session:
        await session.execute(
            text("UPDATE edges SET derived_at = now() - interval '9 hours'")
        )
        await session.execute(
            text(
                "UPDATE raw_messages SET fetched_at = now() - interval '10 hours'"
            )
        )

    summary = await run_cascades(database, bands=(2,), now=NOW)

    assert summary.undelivered_derivation() is None
    assert "run `itgraph derive`" not in summary.line()


async def test_an_empty_window_says_so(database: Database) -> None:
    summary = await run_cascades(database, bands=(2,), now=NOW)

    assert summary.newest_repost_at is None
    assert "nothing reposted in the window" in summary.line()


async def test_the_pass_modifies_nothing_it_read(
    database: Database,
) -> None:
    await seed(database, reposters=2)

    async with database.session() as session:
        before = (
            await session.scalar(text("SELECT count(*) FROM edges")),
            await session.scalar(text("SELECT count(*) FROM raw_messages")),
        )

    await run_cascades(database, bands=(2,), now=NOW)

    async with database.session() as session:
        after = (
            await session.scalar(text("SELECT count(*) FROM edges")),
            await session.scalar(text("SELECT count(*) FROM raw_messages")),
        )
    assert before == after


# --- the queue -----------------------------------------------------


async def test_the_constraint_prevents_a_second_alert(
    database: Database,
) -> None:
    await seed(database, reposters=2)
    cascade = Cascade(post_key=(PUBLISHER, POST), band=2, value=2)

    async with database.session() as session:
        first = await raise_cascades(session, [cascade])
    async with database.session() as session:
        second = await raise_cascades(session, [cascade])

    assert (first, second) == (1, 0)


async def test_claiming_marks_nothing(database: Database) -> None:
    """Claim, commit, send, mark — never a row lock across a network call."""
    await seed(database, reposters=2)
    async with database.session() as session:
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=2, value=2)]
        )

    async with database.session() as session:
        claimed = await claim_undelivered(session, limit=10)
    async with database.session() as session:
        still_outstanding = await claim_undelivered(session, limit=10)

    assert len(claimed) == 1
    assert len(still_outstanding) == 1


async def test_two_claimers_never_take_one_row(
    database_url: str, database: Database
) -> None:
    """`SKIP LOCKED` is what makes a second bot harmless, not catastrophic."""
    await seed(database, reposters=2)
    async with database.session() as session:
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=2, value=2)]
        )

    other = Database(database_url)
    try:
        async with database.session() as first, other.session() as second:
            mine = await claim_undelivered(first, limit=10)
            theirs = await claim_undelivered(second, limit=10)
    finally:
        await other.dispose()

    assert len(mine) == 1
    assert theirs == []


async def test_delivery_is_recorded(database: Database) -> None:
    await seed(database, reposters=2)
    async with database.session() as session:
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=2, value=2)]
        )
        claimed = await claim_undelivered(session, limit=10)

    async with database.session() as session:
        await mark_delivered(
            session,
            [claimed[0].id],
            delivery=AlertDelivery.DIRECT,
            at=NOW,
        )

    async with database.session() as session:
        assert await claim_undelivered(session, limit=10) == []
        assert (
            await count_delivered_since(
                session,
                since=NOW - timedelta(hours=1),
                delivery=AlertDelivery.DIRECT,
            )
            == 1
        )


async def test_a_failed_send_stays_outstanding(database: Database) -> None:
    await seed(database, reposters=2)
    async with database.session() as session:
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=2, value=2)]
        )
        claimed = await claim_undelivered(session, limit=10)

    async with database.session() as session:
        await mark_failed(session, claimed[0].id, error="network unreachable")

    async with database.session() as session:
        again = await claim_undelivered(session, limit=10)
        assert len(again) == 1
        assert again[0].attempts == 1
        assert await failing_alerts(session, attempts=1) == 1


async def test_a_verdict_replaces_an_earlier_one(
    database: Database,
) -> None:
    await seed(database, reposters=2)
    async with database.session() as session:
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=2, value=2)]
        )
        claimed = await claim_undelivered(session, limit=10)

    async with database.session() as session:
        await record_verdict(
            session,
            alert_id=claimed[0].id,
            verdict=AlertVerdict.USEFUL,
            at=NOW,
        )
        await record_verdict(
            session,
            alert_id=claimed[0].id,
            verdict=AlertVerdict.BORING,
            at=NOW + timedelta(minutes=5),
        )

    async with database.session() as session:
        rows = await session.execute(
            text("SELECT verdict FROM alert_feedback")
        )
        assert [row[0] for row in rows.all()] == ["boring"]


async def test_raised_bands_reports_what_exists(database: Database) -> None:
    await seed(database, reposters=2)
    async with database.session() as session:
        await raise_cascades(
            session, [Cascade(post_key=(PUBLISHER, POST), band=2, value=2)]
        )

    async with database.session() as session:
        raised = await raised_bands(
            session,
            kind=AlertKind.REPOST_CASCADE,
            since=NOW - timedelta(days=1),
        )

    assert raised == {(PUBLISHER, POST): {2}}


def test_a_digest_is_due_once_a_day() -> None:
    morning = datetime(2026, 8, 4, 9, 30, tzinfo=UTC)

    assert digest_is_due(morning, hour=9, last=None)
    assert not digest_is_due(
        datetime(2026, 8, 4, 8, 0, tzinfo=UTC), hour=9, last=None
    )
    assert not digest_is_due(morning, hour=9, last=morning)
    assert digest_is_due(morning + timedelta(days=1), hour=9, last=morning)


async def test_concurrent_claims_do_not_deadlock(
    database_url: str, database: Database
) -> None:
    """Two bots is a supported accident, not a supported deployment."""
    await seed(database, reposters=3)
    async with database.session() as session:
        await raise_cascades(
            session,
            [
                Cascade(post_key=(PUBLISHER, POST), band=2, value=3),
                Cascade(post_key=(PUBLISHER, POST), band=3, value=3),
            ],
        )

    other = Database(database_url)

    async def claim(db: Database) -> int:
        async with db.session() as session:
            return len(await claim_undelivered(session, limit=1))

    try:
        taken = await asyncio.gather(claim(database), claim(other))
    finally:
        await other.dispose()

    assert sum(taken) == 2
