"""The scoring pass and its replay, against a real Postgres.

The arithmetic is tested in ``test_scoring.py`` and the storage in
``test_baselines.py``. What is under test here is what only the whole
pass can answer: that one post raises one alert however many metrics it
is remarkable on, that a re-run is silent, that a replay of the present
names what the live pass named, and that nothing collected is touched.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from itgraph.db.baselines import (
    count_in_scope,
    start_run,
    store_channel_medians,
    store_curves,
)
from itgraph.db.models import AlertKind, ChannelKind, Metric
from itgraph.db.session import Database
from itgraph.scoring.curves import Curve
from itgraph.scoring.run import run_scoring

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
CHANNEL = 1000000001
POST = 500

# The measured shape of views: a third of the eight-hour value by an
# hour, half by two. With a mature median of 1000 and a factor of 0.44, a
# post is expected to hold 220 views at two hours.
CURVE = Curve(
    fractions={"15m": 0.17, "1h": 0.33, "2h": 0.50, "8h": 0.96},
    samples={band: 500 for band in ("15m", "1h", "2h", "8h")},
)
EXPECTED_AT_2H = 1000.0 * 0.44 * 0.50


async def seed_post(
    database: Database,
    *,
    msg_id: int = POST,
    published_ago: timedelta = timedelta(hours=3),
    album: int | None = None,
    channel: int = CHANNEL,
) -> None:
    """A channel and one published post. No snapshots yet."""
    published = NOW - published_ago
    payload: dict[str, object] = {
        "_": "Message",
        "id": msg_id,
        "date": published.isoformat(),
        "message": "a post that did well",
    }
    if album is not None:
        payload["grouped_id"] = album

    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO channels (tg_id, username, title, "
                "discovered_via, status, kind) VALUES "
                "(:id, :name, 'Example', 'manual', 'seed', 'personal') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": channel, "name": f"example_{channel}"},
        )
        await session.execute(
            text(
                "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                "VALUES (:c, :m, CAST(:p AS jsonb))"
            ),
            {"c": channel, "m": msg_id, "p": json.dumps(payload)},
        )


async def snapshot(
    database: Database,
    *,
    msg_id: int = POST,
    channel: int = CHANNEL,
    age: timedelta = timedelta(hours=2),
    published_ago: timedelta = timedelta(hours=3),
    views: int | None = None,
    forwards: int | None = None,
    reactions: dict[str, int] | None = None,
) -> None:
    """One reading of one post, taken at ``age`` after publication."""
    observed = NOW - published_ago + age
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO message_metrics (channel_id, msg_id, "
                "observed_at, views, forwards, reactions) VALUES "
                "(:c, :m, :at, :v, :f, CAST(:r AS jsonb))"
            ),
            {
                "c": channel,
                "m": msg_id,
                "at": observed,
                "v": views,
                "f": forwards,
                "r": None if reactions is None else json.dumps(reactions),
            },
        )


async def seed_baselines(
    database: Database,
    *,
    views_median: float = 1000.0,
    forwards_median: float | None = None,
    channel: int = CHANNEL,
    spread: float = 0.38,
) -> None:
    """A completed baseline run covering this channel."""
    async with database.session() as session:
        run = await start_run(
            session,
            mature_days=28,
            min_channel_posts=30,
            min_band_samples=20,
            channels_in_scope=await count_in_scope(session),
        )
        await store_channel_medians(
            session, run, Metric.VIEWS, {channel: (views_median, 40)}
        )
        await store_curves(
            session,
            run,
            ChannelKind.PERSONAL,
            Metric.VIEWS,
            curve=CURVE,
            factor=0.44,
            spread=spread,
            samples=500,
        )
        if forwards_median is not None:
            await store_channel_medians(
                session, run, Metric.FORWARDS, {channel: (forwards_median, 40)}
            )
            await store_curves(
                session,
                run,
                ChannelKind.PERSONAL,
                Metric.FORWARDS,
                curve=CURVE,
                factor=0.44,
                spread=spread,
                samples=500,
            )
        run.completed_at = NOW - timedelta(hours=1)


async def alerts(database: Database) -> list[tuple[str, int, int, float]]:
    """Every alert in the queue, as (kind, channel, msg, value)."""
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT kind::text, channel_id, msg_id, value "
                "FROM alerts ORDER BY id"
            )
        )
    return [(kind, channel, msg, value) for kind, channel, msg, value in rows]


# --- raising --------------------------------------------------------


async def test_an_unusual_post_raises_an_alert(database: Database) -> None:
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H * 4))

    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 1
    kind, channel, msg, value = alerts_one(await alerts(database))
    assert kind == AlertKind.VIEWS_SPIKE
    assert (channel, msg) == (CHANNEL, POST)
    assert value > 3.0


def alerts_one(
    rows: list[tuple[str, int, int, float]],
) -> tuple[str, int, int, float]:
    assert len(rows) == 1, rows
    return rows[0]


async def test_an_ordinary_post_raises_nothing(database: Database) -> None:
    """The threshold has to be the thing that decides, not the presence
    of a reading."""
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H))

    summary = await run_scoring(database, now=NOW)

    assert summary.scored == 1
    assert summary.crossed == 0
    assert await alerts(database) == []


async def test_one_post_unusual_on_two_metrics_raises_one_alert(
    database: Database,
) -> None:
    """Four independent alerts would put four messages about the most
    interesting post of the day into the chat inside an hour."""
    await seed_post(database)
    await seed_baselines(database, forwards_median=50.0)
    await snapshot(
        database,
        views=int(EXPECTED_AT_2H * 4),
        forwards=int(50.0 * 0.44 * 0.50 * 20),
    )

    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 1
    # ...and under the metric that scored highest, not the first one.
    kind, _, _, _ = alerts_one(await alerts(database))
    assert kind == AlertKind.FORWARD_SPIKE


async def test_a_re_run_raises_nothing(database: Database) -> None:
    """Safe on a short schedule: the constraint, not a flag."""
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H * 4))

    first = await run_scoring(database, now=NOW)
    second = await run_scoring(database, now=NOW)

    assert first.raised == 1
    assert second.crossed == 1
    assert second.raised == 0
    assert len(await alerts(database)) == 1


async def test_a_later_spike_on_another_metric_is_a_separate_alert(
    database: Database,
) -> None:
    """A different kind, so a different row — reach and endorsement are
    different events about the same post."""
    await seed_post(database)
    await seed_baselines(database, forwards_median=50.0)
    await snapshot(
        database, age=timedelta(hours=1), views=int(1000 * 0.44 * 0.33 * 4)
    )
    await run_scoring(database, now=NOW)

    await snapshot(
        database, age=timedelta(hours=2), forwards=int(50 * 0.44 * 0.5 * 20)
    )
    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 1
    assert {kind for kind, _, _, _ in await alerts(database)} == {
        AlertKind.VIEWS_SPIKE,
        AlertKind.FORWARD_SPIKE,
    }


async def test_a_post_is_scored_at_its_most_remarkable_age(
    database: Database,
) -> None:
    """Not only at the newest reading.

    A post extraordinary at one hour and merely good by two is still the
    thing worth being told about, and taking the last reading would miss
    exactly the fast ones.
    """
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(
        database, age=timedelta(hours=1), views=int(1000 * 0.44 * 0.33 * 6)
    )
    await snapshot(database, age=timedelta(hours=2), views=int(EXPECTED_AT_2H))

    summary = await run_scoring(database, now=NOW)

    assert summary.crossed == 1


async def test_an_album_raises_one_alert(database: Database) -> None:
    """Telegram stores each part as a message with its own counters."""
    for offset in range(3):
        await seed_post(database, msg_id=POST + offset, album=777)
        await snapshot(
            database, msg_id=POST + offset, views=int(EXPECTED_AT_2H * 4)
        )
    await seed_baselines(database)

    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 1
    # ...and against the first part, which is what a t.me link resolves to.
    _, _, msg, _ = alerts_one(await alerts(database))
    assert msg == POST


# --- refusals -------------------------------------------------------


async def test_without_baselines_nothing_is_raised(
    database: Database,
) -> None:
    """Rather than scoring against defaults nobody chose."""
    await seed_post(database)
    await snapshot(database, views=999_999)

    summary = await run_scoring(database, now=NOW)

    assert summary.raised == 0
    assert summary.channels_with_baseline == 0
    assert "itgraph baselines" in summary.line()
    assert await alerts(database) == []


async def test_a_channel_without_a_baseline_is_reported_not_scored(
    database: Database,
) -> None:
    """ "No alerts from this channel" and "this channel is not scored"
    are different facts."""
    other = CHANNEL + 1
    await seed_post(database)
    await seed_post(database, channel=other, msg_id=POST)
    await snapshot(database, channel=other, views=999_999)
    # After both channels exist, so the run's denominator covers both.
    await seed_baselines(database)

    summary = await run_scoring(database, now=NOW)

    assert summary.posts == 1
    assert summary.scored == 0
    assert summary.channels_in_scope == 2
    assert summary.channels_with_baseline == 1
    assert "too little history" in summary.line()
    assert await alerts(database) == []


async def test_a_channel_that_publishes_no_reactions_scores_none(
    database: Database,
) -> None:
    """Absent is not zero — the distinction `derive/metrics.py` keeps."""
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H), reactions=None)

    summary = await run_scoring(database, now=NOW)

    assert summary.crossed == 0


async def test_a_reading_outside_every_band_is_not_scored(
    database: Database,
) -> None:
    """Three hours sits between the two-hour and four-hour fits."""
    await seed_post(database, published_ago=timedelta(hours=5))
    await seed_baselines(database)
    await snapshot(
        database,
        age=timedelta(hours=3),
        published_ago=timedelta(hours=5),
        views=999_999,
    )

    summary = await run_scoring(database, now=NOW)

    assert summary.posts == 1
    assert summary.scored == 0


async def test_nothing_collected_is_modified(database: Database) -> None:
    """The pass reads the raw layer and the snapshots and writes alerts."""
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H * 4))

    async def fingerprint() -> tuple[int, int]:
        async with database.session() as session:
            raw = await session.scalar(
                text("SELECT count(*) FROM raw_messages")
            )
            metrics = await session.scalar(
                text("SELECT count(*) FROM message_metrics")
            )
        return int(raw or 0), int(metrics or 0)

    before = await fingerprint()
    await run_scoring(database, now=NOW)

    assert await fingerprint() == before


# --- replay ---------------------------------------------------------


async def test_a_replay_of_the_present_names_what_the_pass_named(
    database: Database,
) -> None:
    """The same code with the same moment, so the same answer.

    A parallel scorer would agree here and diverge on the case nobody
    checked, which is why replay is this function with an argument.
    """
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H * 4))

    replayed = await run_scoring(database, now=NOW, dry_run=True)
    live = await run_scoring(database, now=NOW)

    assert [spike.post_key for spike in replayed.spikes] == [
        spike.post_key for spike in live.spikes
    ]
    assert replayed.spikes[0].score.z == live.spikes[0].score.z


async def test_a_replay_writes_nothing(database: Database) -> None:
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H * 4))

    summary = await run_scoring(database, now=NOW, dry_run=True)

    assert summary.crossed == 1
    assert summary.raised == 0
    assert await alerts(database) == []
    assert "would raise 1" in summary.line()


async def test_a_replay_does_not_see_the_future(database: Database) -> None:
    """A past moment must not be scored with readings taken after it.

    Otherwise a replay answers with information the live pass could not
    have had, and agrees with it for the wrong reason.
    """
    await seed_post(database, published_ago=timedelta(hours=3))
    await seed_baselines(database)
    await snapshot(
        database, age=timedelta(hours=2), views=int(EXPECTED_AT_2H * 4)
    )

    summary = await run_scoring(
        database, now=NOW - timedelta(hours=2), dry_run=True
    )

    assert summary.posts == 0
    assert summary.crossed == 0


async def test_a_replay_accepts_another_threshold(
    database: Database,
) -> None:
    """An experiment that costs minutes instead of a day."""
    await seed_post(database)
    await seed_baselines(database)
    await snapshot(database, views=int(EXPECTED_AT_2H * 2))

    strict = await run_scoring(database, now=NOW, dry_run=True)
    loose = await run_scoring(database, now=NOW, dry_run=True, threshold=1.0)

    assert strict.crossed == 0
    assert loose.crossed == 1
