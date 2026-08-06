"""Storing and reading baselines, against a real Postgres.

The arithmetic is tested in ``test_scoring.py``. What is tested here is
what only a database can answer: that a refresh replaces rather than
accumulates, that a half-finished one is invisible, and that a channel
without enough history simply has no baseline rather than a thin one.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from itgraph.config import settings
from itgraph.db.baselines import (
    Baselines,
    count_in_scope,
    current_run,
    load_baselines,
    mature_medians,
    observations,
    start_run,
    store_channel_medians,
    store_curves,
)
from itgraph.db.models import ChannelKind, Metric
from itgraph.db.session import Database
from itgraph.scoring.curves import Curve
from itgraph.scoring.refresh import refresh_baselines

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
CHANNEL = 1000000001
OTHER = 1000000002


async def seed_channel(
    database: Database,
    tg_id: int = CHANNEL,
    *,
    kind: str = "personal",
    mature_posts: int = 0,
    views: int = 1000,
    is_chat: bool = False,
) -> None:
    """A seed channel with a given number of settled posts behind it."""
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO channels (tg_id, username, title, "
                "discovered_via, status, kind, is_chat) VALUES "
                "(:id, :name, 'Example', 'manual', 'seed', :kind, :chat)"
            ),
            {
                "id": tg_id,
                "name": f"example_{tg_id}",
                "kind": kind,
                "chat": is_chat,
            },
        )
        for index in range(mature_posts):
            await session.execute(
                text(
                    "INSERT INTO raw_messages "
                    "(channel_id, msg_id, payload, fetched_at) "
                    "VALUES (:c, :m, CAST(:p AS jsonb), :f)"
                ),
                {
                    "c": tg_id,
                    "m": index + 1,
                    "f": NOW,
                    "p": json.dumps(
                        {
                            "_": "Message",
                            "id": index + 1,
                            "date": (NOW - timedelta(days=60)).isoformat(),
                            "views": views,
                        }
                    ),
                },
            )


async def a_run(database: Database, *, complete: bool = True) -> int:
    """A refresh carrying one channel median and one curve."""
    async with database.session() as session:
        run = await start_run(
            session,
            mature_days=28,
            min_channel_posts=30,
            min_band_samples=20,
            channels_in_scope=await count_in_scope(session),
        )
        await store_channel_medians(
            session, run, Metric.VIEWS, {CHANNEL: (1000.0, 40)}
        )
        await store_curves(
            session,
            run,
            ChannelKind.PERSONAL,
            Metric.VIEWS,
            curve=Curve(fractions={"1h": 0.33}, samples={"1h": 500}),
            factor=0.44,
            spread=0.38,
            samples=500,
        )
        if complete:
            run.completed_at = NOW
        return run.id


# --- medians --------------------------------------------------------


async def test_a_channel_with_enough_history_gets_a_median(
    database: Database,
) -> None:
    await seed_channel(database, mature_posts=40, views=1000)

    async with database.session() as session:
        medians = await mature_medians(
            session, Metric.VIEWS, mature_days=28, min_posts=30
        )

    assert medians[CHANNEL] == (1000.0, 40)


async def test_a_thin_channel_gets_none(database: Database) -> None:
    """A median over a handful of posts is not a baseline.

    Absent rather than thin, because a wrong baseline is worse than a
    missing one: the missing one is reported.
    """
    await seed_channel(database, mature_posts=5)

    async with database.session() as session:
        medians = await mature_medians(
            session, Metric.VIEWS, mature_days=28, min_posts=30
        )

    assert CHANNEL not in medians


async def test_maturity_is_measured_per_row(database: Database) -> None:
    """`fetched_at - date`, not against one cutoff date.

    The backfill ran over a week, so a single cutoff would mean four
    weeks of maturity for one channel and five for another.
    """
    await seed_channel(database, mature_posts=40)
    async with database.session() as session:
        # Same publication date, read only a day later: not settled.
        await session.execute(
            text(
                "UPDATE raw_messages SET fetched_at = "
                "(payload->>'date')::timestamptz + interval '1 day'"
            )
        )

    async with database.session() as session:
        medians = await mature_medians(
            session, Metric.VIEWS, mature_days=28, min_posts=30
        )

    assert medians == {}


async def test_a_chat_is_not_given_a_baseline(database: Database) -> None:
    """The same scope predicate every other pass uses."""
    await seed_channel(database, mature_posts=40, is_chat=True)

    async with database.session() as session:
        medians = await mature_medians(
            session, Metric.VIEWS, mature_days=28, min_posts=30
        )

    assert medians == {}


# --- observations ---------------------------------------------------


async def test_observations_carry_the_age_they_were_read_at(
    database: Database,
) -> None:
    """`observed_at` minus publication, never the intended sample.

    A curve fitted on intended ages would describe the schedule rather
    than the metric.
    """
    await seed_channel(database, mature_posts=1)
    published = NOW - timedelta(hours=3)
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE raw_messages SET payload = "
                "jsonb_set(payload, '{date}', to_jsonb(CAST(:d AS text)))"
            ),
            {"d": published.isoformat()},
        )
        await session.execute(
            text(
                "INSERT INTO message_metrics "
                "(channel_id, msg_id, observed_at, views) "
                "VALUES (:c, 1, :at, 500)"
            ),
            {"c": CHANNEL, "at": published + timedelta(hours=1)},
        )

    async with database.session() as session:
        grouped = await observations(
            session, Metric.VIEWS, since=NOW - timedelta(days=1)
        )

    entries = grouped[ChannelKind.PERSONAL]
    assert len(entries) == 1
    assert entries[0].age == timedelta(hours=1)


async def test_a_reading_predating_its_post_is_dropped(
    database: Database,
) -> None:
    """A negative age passes every band test silently."""
    await seed_channel(database, mature_posts=1)
    published = NOW - timedelta(hours=3)
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE raw_messages SET payload = "
                "jsonb_set(payload, '{date}', to_jsonb(CAST(:d AS text)))"
            ),
            {"d": published.isoformat()},
        )
        await session.execute(
            text(
                "INSERT INTO message_metrics "
                "(channel_id, msg_id, observed_at, views) "
                "VALUES (:c, 1, :at, 500)"
            ),
            {"c": CHANNEL, "at": published - timedelta(minutes=5)},
        )

    async with database.session() as session:
        grouped = await observations(
            session, Metric.VIEWS, since=NOW - timedelta(days=1)
        )

    assert grouped == {}


# --- runs -----------------------------------------------------------


async def test_baselines_read_back(database: Database) -> None:
    await seed_channel(database, mature_posts=40)
    await a_run(database)

    async with database.session() as session:
        loaded = await load_baselines(session)

    assert isinstance(loaded, Baselines)
    baseline = loaded.for_channel(CHANNEL, Metric.VIEWS)
    assert baseline is not None
    assert baseline.mature_median == 1000.0
    assert baseline.spread == 0.38
    assert baseline.curve.fractions["1h"] == 0.33


async def test_a_refresh_replaces_rather_than_accumulates(
    database: Database,
) -> None:
    """Nothing may score against a mixture of two vintages."""
    await seed_channel(database, mature_posts=40)
    await a_run(database)

    async with database.session() as session:
        run = await start_run(
            session,
            mature_days=28,
            min_channel_posts=30,
            min_band_samples=20,
            channels_in_scope=1,
        )
        await store_channel_medians(
            session, run, Metric.VIEWS, {CHANNEL: (2000.0, 50)}
        )
        await store_curves(
            session,
            run,
            ChannelKind.PERSONAL,
            Metric.VIEWS,
            curve=Curve(fractions={"1h": 0.4}, samples={"1h": 600}),
            factor=0.5,
            spread=0.3,
            samples=600,
        )
        run.completed_at = NOW + timedelta(hours=1)

    async with database.session() as session:
        loaded = await load_baselines(session)

    assert loaded is not None
    baseline = loaded.for_channel(CHANNEL, Metric.VIEWS)
    assert baseline is not None
    # Entirely the newer run — not a median from one and a curve from the other.
    assert baseline.mature_median == 2000.0
    assert baseline.spread == 0.3


async def test_an_unfinished_refresh_is_invisible(
    database: Database,
) -> None:
    """Half a refresh is medians for some channels and curves without them."""
    await seed_channel(database, mature_posts=40)
    await a_run(database, complete=False)

    async with database.session() as session:
        assert await current_run(session) is None
        assert await load_baselines(session) is None


async def test_the_parameters_travel_with_the_run(
    database: Database,
) -> None:
    """A threshold argued about later has to say what it was arguing with."""
    await seed_channel(database, mature_posts=40)
    await a_run(database)

    async with database.session() as session:
        run = await current_run(session)

    assert run is not None
    assert run.mature_days == 28
    assert run.min_channel_posts == 30


async def test_a_channel_without_a_baseline_scores_nothing(
    database: Database,
) -> None:
    """No partial baselines: a z may not be part measured and part assumed."""
    await seed_channel(database, mature_posts=40)
    await seed_channel(database, OTHER, mature_posts=2)
    await a_run(database)

    async with database.session() as session:
        loaded = await load_baselines(session)

    assert loaded is not None
    assert loaded.for_channel(CHANNEL, Metric.VIEWS) is not None
    assert loaded.for_channel(OTHER, Metric.VIEWS) is None
    # ...and the metric that was never fitted is absent too.
    assert loaded.for_channel(CHANNEL, Metric.COMMENTS) is None


async def test_the_denominator_is_recorded(database: Database) -> None:
    """ "465 channels have baselines" means nothing without "out of how many"."""
    await seed_channel(database, mature_posts=40)
    await seed_channel(database, OTHER, mature_posts=2)
    await a_run(database)

    async with database.session() as session:
        loaded = await load_baselines(session)

    assert loaded is not None
    assert loaded.channels_in_scope == 2
    assert loaded.scored_channels == 1


# --- the refresh pass -----------------------------------------------


async def seed_history(
    database: Database, *, posts: int = 6, mature: int = 6
) -> None:
    """A channel with settled history and recent posts read twice each.

    The recent readings deliberately differ from post to post: a corpus
    where every post reaches exactly the expectation has a spread of
    zero, and a spread of zero is refused rather than fitted.
    """
    await seed_channel(database, mature_posts=mature, views=1000)
    async with database.session() as session:
        for index in range(posts):
            msg_id = 1000 + index
            published = NOW - timedelta(hours=12)
            await session.execute(
                text(
                    "INSERT INTO raw_messages "
                    "(channel_id, msg_id, payload, fetched_at) VALUES "
                    "(:c, :m, CAST(:p AS jsonb), :f)"
                ),
                {
                    "c": CHANNEL,
                    "m": msg_id,
                    "f": NOW,
                    "p": json.dumps(
                        {
                            "_": "Message",
                            "id": msg_id,
                            "date": published.isoformat(),
                        }
                    ),
                },
            )
            settled = 380.0 + 20 * index
            for age, value in (
                (timedelta(hours=1), settled * (0.70 + 0.02 * index)),
                (timedelta(hours=8), settled),
            ):
                await session.execute(
                    text(
                        "INSERT INTO message_metrics "
                        "(channel_id, msg_id, observed_at, views) "
                        "VALUES (:c, :m, :at, :v)"
                    ),
                    {
                        "c": CHANNEL,
                        "m": msg_id,
                        "at": published + age,
                        "v": int(value),
                    },
                )


async def test_a_refresh_fits_and_publishes_one_run(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole pass: medians, a curve, a factor and a spread, at once."""
    monkeypatch.setattr(settings, "baseline_min_channel_posts", 3)
    monkeypatch.setattr(settings, "baseline_min_band_samples", 3)
    await seed_history(database)

    summary = await refresh_baselines(database, now=NOW)

    assert (ChannelKind.PERSONAL, Metric.VIEWS) in summary.fitted
    assert summary.channels_with_baseline == 1

    async with database.session() as session:
        loaded = await load_baselines(session)
    assert loaded is not None
    baseline = loaded.for_channel(CHANNEL, Metric.VIEWS)
    assert baseline is not None
    assert baseline.mature_median == 1000.0
    # The reference band is the 8h one, so its fraction is 1 by
    # construction and the 1h one is the shape actually measured.
    assert baseline.curve.fractions["8h"] == 1.0
    assert 0.6 < baseline.curve.fractions["1h"] < 0.9
    assert baseline.factor == pytest.approx(0.44, rel=0.1)
    assert baseline.spread > 0


async def test_a_metric_nobody_publishes_is_reported_not_fitted(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh that fits views and nothing else has not half worked.

    It has reported that three metrics are too thinly published to be
    measured, which is a finding rather than a fault.
    """
    monkeypatch.setattr(settings, "baseline_min_channel_posts", 3)
    monkeypatch.setattr(settings, "baseline_min_band_samples", 3)
    await seed_history(database)

    summary = await refresh_baselines(database, now=NOW)

    unfitted = {metric for _, metric, _ in summary.skipped}
    assert Metric.REACTIONS in unfitted
    assert Metric.VIEWS not in unfitted
    assert "not fitted" in summary.line()


async def test_an_interrupted_refresh_leaves_the_old_one_in_use(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupting a refresh is safe, which is what makes it re-runnable."""
    monkeypatch.setattr(settings, "baseline_min_channel_posts", 3)
    monkeypatch.setattr(settings, "baseline_min_band_samples", 3)
    await seed_history(database)
    await refresh_baselines(database, now=NOW)

    async with database.session() as session:
        good = await current_run(session)
    assert good is not None

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("interrupted half way")

    monkeypatch.setattr("itgraph.scoring.refresh.fit_curve", explode)
    with pytest.raises(RuntimeError):
        await refresh_baselines(database, now=NOW + timedelta(hours=1))

    async with database.session() as session:
        still = await current_run(session)
    assert still is not None
    assert still.id == good.id
