"""Telegram's scoring, recorded before `scoring/` was made platform-blind.

A characterisation test, not a specification: it records what Telegram's
refresh, replay and live pass produce on one synthetic inventory, and holds
every later version of the code to the same answer. It was written before
`add-youtube-virality-scoring` touched `scoring/`, so the record is the code
as it was then; that change's proof that Telegram did not move starts here.

A missing record is written and the test fails, so none is ever made
silently. A deliberate change to Telegram's scoring deletes the record in
the same commit and says why.
"""

import json
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from itgraph.config import Settings, settings
from itgraph.db.session import Database
from itgraph.scoring.refresh import BaselineSummary, refresh_baselines
from itgraph.scoring.run import SpikeSummary, run_scoring

RECORD = Path(__file__).parent / "fixtures" / "telegram_scoring_record.json"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
THRESHOLD = 2.5

# Every setting the refresh and the pass read, at the code's defaults, so
# that the operator's `.env` cannot decide the record.
PINNED = (
    "alert_spike_metrics",
    "alert_spike_comments_min",
    "alert_spike_fold_hours",
    "scoring_burst_seconds",
    "scoring_window_hours",
    "scoring_replay_days",
    "baseline_window_days",
    "baseline_mature_days",
    "baseline_mature_max_days",
    "baseline_min_channel_posts",
    "baseline_recent_posts",
    "baseline_recent_min_posts",
    "baseline_recent_max_days",
    "baseline_refresh_days",
)

# The floors, lowered so that a small inventory fits every band and kind
# but the thinnest, which then borrows.
FLOORS = {"baseline_min_band_samples": 5, "baseline_min_band_spread_posts": 8}

# Ages a post is read at, in minutes: inside every band, and one past the
# last, which nothing may score.
AGES = (12, 18, 26, 40, 58, 85, 125, 185, 270, 380, 480, 540, 600)

# Channel id → kind, typical views, posts in the last twenty days, settled
# posts behind it. Two channels too quiet for a recent reference fall back
# to their settled posts; the one event channel is too thin for a curve of
# its own and borrows the pool's; one media channel publishes no reactions.
CHANNELS: dict[int, tuple[str, int, int, int]] = {
    3000000001: ("personal", 1200, 28, 0),
    3000000002: ("personal", 300, 26, 0),
    3000000003: ("personal", 8000, 30, 25),
    3000000004: ("personal", 2500, 6, 25),
    3000000005: ("aggregator", 5000, 30, 25),
    3000000006: ("aggregator", 900, 24, 0),
    3000000007: ("aggregator", 15000, 5, 25),
    3000000008: ("media", 20000, 30, 25),
    3000000009: ("media", 4000, 22, 0),
    3000000010: ("event", 700, 3, 0),
}
NO_REACTIONS = {3000000009}

# Posts far above their channel, by channel and position from the newest:
# a channel-wide event of three posts, single spikes, a fresh spike for the
# live pass, and a discussion on a channel that rarely has one.
SPIKES = {
    (3000000001, 1): 6.0,
    (3000000001, 2): 6.0,
    (3000000001, 3): 6.0,
    (3000000003, 5): 9.0,
    (3000000005, 8): 7.0,
    (3000000008, 0): 8.0,
}
# The channel-wide event's posts went out an hour apart, so they cross
# inside one fold window.
EVENT = {
    (3000000001, position): NOW - timedelta(hours=30 + position)
    for position in (1, 2, 3)
}
DISCUSSION = (3000000002, 4)
ALBUM = (3000000005, 2)
BURST = (3000000006, 3)


def _fraction(metric: str, minutes: float) -> float:
    """A smooth shape per metric, normalised to eight hours."""
    power = {"views": 0.6, "reactions": 0.5, "forwards": 0.4, "comments": 0.3}
    return min((minutes / 480) ** power[metric], 1.08)


def _rising(last: dict[str, int], metric: str, value: float) -> int:
    """A counter only rises: the reading is at least the one before."""
    last[metric] = max(last.get(metric, 0), round(value))
    return last[metric]


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth's method; the means here are small."""
    limit, count, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def _inventory() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Channels, messages and readings, the same on every run."""
    rng = random.Random(20260820)
    channels: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    readings: list[dict[str, Any]] = []

    for channel, (kind, level, recent, settled) in CHANNELS.items():
        channels.append({"id": channel, "kind": kind})

        for index in range(settled):
            published = NOW - timedelta(days=60 + index)
            effect = math.exp(rng.gauss(0, 0.3))
            payload: dict[str, Any] = {
                "_": "Message",
                "id": index + 1,
                "date": published.isoformat(),
                "views": round(level * 1.4 * effect),
                "forwards": round(level * 0.012 * effect),
                "replies": {"replies": _poisson(rng, level / 1500)},
            }
            if channel not in NO_REACTIONS:
                payload["reactions"] = {
                    "results": [{"count": round(level * 0.03 * effect)}]
                }
            messages.append(
                {
                    "c": channel,
                    "m": index + 1,
                    "p": payload,
                    "f": published + timedelta(days=45),
                }
            )

        span = timedelta(days=20) / recent
        posts = [
            EVENT.get(
                (channel, position),
                NOW
                - timedelta(hours=2)
                - span * position
                + timedelta(minutes=rng.randint(-90, 90)),
            )
            for position in range(recent)
        ]
        for position, published in enumerate(posts):
            msg = 1000 + 10 * position
            parts = [(msg, published)]
            if (channel, position) == ALBUM:
                parts.append((msg + 1, published + timedelta(seconds=1)))
            if (channel, position) == BURST:
                parts.append((msg + 2, published + timedelta(seconds=20)))
            boost = SPIKES.get((channel, position), 1.0)
            effect = math.exp(rng.gauss(0, 0.35)) * boost
            final_comments = (
                30
                if (channel, position) == DISCUSSION
                else _poisson(rng, level / 2000 * math.exp(rng.gauss(0, 0.5)))
            )
            for part, (part_msg, part_published) in enumerate(parts):
                payload = {"_": "Message", "id": part_msg}
                payload["date"] = part_published.isoformat()
                if (channel, position) == ALBUM:
                    payload["grouped_id"] = 77
                messages.append(
                    {
                        "c": channel,
                        "m": part_msg,
                        "p": payload,
                        "f": part_published + timedelta(minutes=1),
                    }
                )
                share = 1.0 if part == 0 else 0.4
                last: dict[str, int] = {}
                for minutes in AGES:
                    observed = part_published + timedelta(minutes=minutes)
                    if observed > NOW:
                        break
                    noise = math.exp(rng.gauss(0, 0.08))
                    views = _rising(
                        last,
                        "views",
                        level
                        * effect
                        * share
                        * noise
                        * _fraction("views", minutes),
                    )
                    forwards = _rising(
                        last,
                        "forwards",
                        level
                        * 0.012
                        * effect
                        * share
                        * _fraction("forwards", minutes),
                    )
                    reactions = (
                        None
                        if channel in NO_REACTIONS
                        else {
                            "👍": _rising(
                                last,
                                "reactions",
                                level
                                * 0.03
                                * effect
                                * share
                                * _fraction("reactions", minutes),
                            )
                        }
                    )
                    comments = _rising(
                        last,
                        "comments",
                        math.floor(
                            final_comments
                            * share
                            * min(_fraction("comments", minutes), 1.0)
                        ),
                    )
                    readings.append(
                        {
                            "c": channel,
                            "m": part_msg,
                            "at": observed,
                            "v": views,
                            "f": forwards,
                            "r": None
                            if reactions is None
                            else json.dumps(reactions),
                            "n": comments,
                        }
                    )
    return channels, messages, readings


async def _seed(database: Database) -> None:
    channels, messages, readings = _inventory()
    async with database.session() as session:
        for channel in channels:
            await session.execute(
                text(
                    "INSERT INTO channels (tg_id, username, title, "
                    "discovered_via, status, kind) VALUES "
                    "(:id, :name, 'Example', 'manual', 'seed', :kind)"
                ),
                {**channel, "name": f"example_{channel['id']}"},
            )
        await session.execute(
            text(
                "INSERT INTO raw_messages (channel_id, msg_id, payload, "
                "fetched_at) VALUES (:c, :m, CAST(:p AS jsonb), :f)"
            ),
            [{**row, "p": json.dumps(row["p"])} for row in messages],
        )
        await session.execute(
            text(
                "INSERT INTO message_metrics (channel_id, msg_id, observed_at, "
                "views, forwards, reactions, comments) VALUES "
                "(:c, :m, :at, :v, :f, CAST(:r AS jsonb), :n)"
            ),
            readings,
        )


def _number(value: float | None) -> float | None:
    """Ten significant digits: what the record holds a float to."""
    return None if value is None else float(f"{value:.10g}")


async def _rows(database: Database, query: str) -> list[list[Any]]:
    async with database.session() as session:
        result = await session.execute(text(query))
        return [
            [
                _number(value) if isinstance(value, float) else value
                for value in row
            ]
            for row in result
        ]


def _refresh(summary: BaselineSummary) -> dict[str, Any]:
    return {
        "channels_in_scope": summary.channels_in_scope,
        "channels_with_baseline": summary.channels_with_baseline,
        "fitted": sorted(
            f"{kind}/{metric}" for kind, metric in summary.fitted
        ),
        "borrowed": sorted(
            f"{kind}/{metric}" for kind, metric in summary.borrowed
        ),
        "skipped": sorted(
            f"{kind}/{metric}/{why}" for kind, metric, why in summary.skipped
        ),
        "references": {
            str(metric): list(counts)
            for metric, counts in sorted(summary.references.items())
        },
    }


def _key(entry: Any) -> list[Any] | None:
    return None if entry is None else list(entry.post_key)


def _replay(summary: SpikeSummary) -> dict[str, Any]:
    return {
        "posts": summary.posts,
        "scored": summary.scored,
        "crossed": summary.crossed,
        "folded": summary.folded,
        "channels_in_scope": summary.channels_in_scope,
        "channels_with_baseline": summary.channels_with_baseline,
        "channels_on_fallback": summary.channels_on_fallback,
        "uncounted": list(summary.uncounted),
        "spikes": [
            [
                *spike.post_key,
                spike.score.metric,
                spike.score.age.total_seconds(),
                _number(spike.score.observed),
                _number(spike.score.expected),
                _number(spike.score.z),
                spike.crossed_at.isoformat(),
            ]
            for spike in summary.spikes
        ],
        "placed": [
            [_key(placed.spike), _key(placed.into)]
            for placed in summary.placed
        ],
    }


async def _record(database: Database) -> dict[str, Any]:
    """Everything Telegram's scoring produces on the inventory."""
    refreshed = await refresh_baselines(database, now=NOW)
    replayed = await run_scoring(
        database,
        now=NOW,
        since=NOW - timedelta(days=7),
        threshold=THRESHOLD,
        dry_run=True,
    )
    live = await run_scoring(database, now=NOW, threshold=THRESHOLD)
    return {
        "refresh": _refresh(refreshed),
        "run": await _rows(
            database,
            "SELECT mature_days, min_channel_posts, min_band_samples, "
            "min_band_spread_posts, recent_posts, recent_min_posts, "
            "recent_max_days, channels_in_scope, channels_with_baseline "
            "FROM baseline_runs",
        ),
        "channel_baselines": await _rows(
            database,
            "SELECT channel_id, metric::text, median, samples, "
            "reference::text FROM channel_baselines "
            "ORDER BY channel_id, metric",
        ),
        "metric_baselines": await _rows(
            database,
            "SELECT kind::text, metric::text, factor, spread, samples, "
            "dispersion, borrowed FROM metric_baselines ORDER BY kind, metric",
        ),
        "curve_points": await _rows(
            database,
            "SELECT kind::text, metric::text, band, fraction, spread, "
            "samples FROM curve_points ORDER BY kind, metric, band",
        ),
        "replay": _replay(replayed),
        "live": {"raised": live.raised, "folded": live.folded},
        "alerts": await _rows(
            database,
            "SELECT a.kind::text, a.channel_id, a.msg_id, a.band, a.value, "
            "a.observed, a.expected, h.channel_id, h.msg_id "
            "FROM alerts a LEFT JOIN alerts h ON h.id = a.folded_into "
            "ORDER BY a.channel_id, a.msg_id, a.kind",
        ),
    }


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PINNED:
        monkeypatch.setattr(
            settings, name, Settings.model_fields[name].default
        )
    for name, value in FLOORS.items():
        monkeypatch.setattr(settings, name, value)


async def test_telegram_scoring_matches_its_record(database: Database) -> None:
    await _seed(database)
    produced = await _record(database)

    # The record exercises what it is meant to: a fallback, a borrowed
    # kind, a comment dispersion, folds and a live alert.
    assert produced["refresh"]["borrowed"]
    assert any(row[4] == "mature" for row in produced["channel_baselines"])
    assert any(row[5] is not None for row in produced["metric_baselines"])
    assert produced["replay"]["folded"] > 0
    assert produced["live"]["raised"] > 0

    if not RECORD.exists():
        RECORD.write_text(
            json.dumps(produced, indent=1, ensure_ascii=False) + "\n"
        )
        pytest.fail(f"no record: written to {RECORD.name}, run again")
    assert produced == json.loads(RECORD.read_text())
