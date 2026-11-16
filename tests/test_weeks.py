"""The week and the ranking both platforms' weekly lists share; pure."""

from datetime import UTC, datetime, timedelta

import pytest

from itgraph.scoring.score import Best, Score
from itgraph.scoring.weeks import (
    Candidate,
    Paces,
    Ranked,
    merge_best,
    pace_of,
    qualifies,
    rank_revivals,
    rank_week,
)

MOMENT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def best(z: float, *, crossed: float | None = 0, read: float = 0) -> Best:
    return Best(
        score=Score(
            metric="views",
            age=timedelta(hours=2),
            observed=100.0,
            expected=10.0,
            z=z,
        ),
        crossed_at=None
        if crossed is None
        else MOMENT + timedelta(hours=crossed),
        scored_at=MOMENT + timedelta(hours=read),
    )


def test_telegram_posts_rank_by_score_then_crossing_then_message() -> None:
    """Integer keys, as a Telegram post's are: a tie goes to the earlier
    crossing, then to the lower message id."""
    found = rank_week(
        {
            (1, 30): best(4.0, crossed=2),
            (2, 20): best(4.0, crossed=1),
            (3, 50): best(4.0, crossed=1),
            (4, 10): best(5.0, crossed=9),
        },
        floor=3.7,
    )
    assert [(entry.channel, entry.item) for entry in found] == [
        (4, 10),
        (2, 20),
        (3, 50),
        (1, 30),
    ]


def test_one_entry_per_channel_with_its_others() -> None:
    found = rank_week(
        {
            (1, 10): best(4.5),
            (1, 11): best(4.0),
            (1, 12): best(3.8),
            (1, 13): best(3.0),
        },
        floor=3.7,
    )
    assert found == [Ranked(channel=1, item=10, best=best(4.5), others=2)]


def test_nothing_below_the_floor() -> None:
    assert rank_week({(1, 10): best(3.6)}, floor=3.7) == []


def test_the_better_of_two_crosses_at_the_earlier() -> None:
    merged = merge_best(best(4.0, crossed=5, read=5), best(3.8, crossed=1))
    assert merged.score.z == 4.0
    assert merged.crossed_at == MOMENT + timedelta(hours=1)
    assert merged.scored_at == MOMENT + timedelta(hours=5)
    assert merge_best(best(4.0), None) == best(4.0)


# --- the second wind ------------------------------------------------------

WEEK_START = datetime(2026, 11, 15, 21, 0, tzinfo=UTC)


def marks(
    previous: float, now: float, *, base: float = 1000.0
) -> tuple[
    tuple[datetime, float], tuple[datetime, float], tuple[datetime, float]
]:
    """Readings a week before, at and a week after the start, at these
    paces a day."""
    before = (WEEK_START - timedelta(days=7), base)
    start = (WEEK_START, base + 7 * previous)
    end = (WEEK_START + timedelta(days=7), base + 7 * previous + 7 * now)
    return before, start, end


def judged(previous: float, now: float) -> Paces:
    found = pace_of(*marks(previous, now), week_start=WEEK_START)
    assert found is not None
    return found


def test_a_post_that_sped_up() -> None:
    """From 100 views a day to 400, against a norm of 1,000: four times
    the pace, and 2,800 a week over a floor of 500."""
    paces = judged(100, 400)
    assert paces.acceleration == pytest.approx(4.0)
    assert qualifies(paces, norm=1000, acceleration=3, share=0.5)


def test_ordinary_decay() -> None:
    assert not qualifies(judged(100, 50), norm=1000, acceleration=3, share=0.5)


def test_speeding_up_by_a_few_views() -> None:
    """From 2 a day to 10: five times as fast, and 70 a week."""
    assert not qualifies(judged(2, 10), norm=1000, acceleration=3, share=0.5)


def test_a_post_that_did_not_grow_the_week_before() -> None:
    paces = judged(0, 600)
    assert paces.previous_pace is None
    assert paces.acceleration is None
    assert qualifies(paces, norm=1000, acceleration=3, share=0.5)
    assert not qualifies(judged(0, 10), norm=1000, acceleration=3, share=0.5)


def test_a_slow_burn_channel() -> None:
    """Its old video gains from search about as fast as last week: many
    times its tiny norm, and still not a second wind."""
    paces = judged(400, 420)
    assert 7 * 420 / 46 > 50
    assert not qualifies(paces, norm=46, acceleration=3, share=0.5)


def test_too_few_readings() -> None:
    before, start, end = marks(100, 400)
    assert pace_of(None, start, end, week_start=WEEK_START) is None
    near = (start[0] - timedelta(hours=30), before[1])
    assert pace_of(near, start, end, week_start=WEEK_START) is None
    early_end = (start[0] + timedelta(hours=30), end[1])
    assert pace_of(before, start, early_end, week_start=WEEK_START) is None
    stale = (WEEK_START - timedelta(hours=1), end[1])
    assert (
        pace_of(
            before,
            (start[0] - timedelta(days=3), start[1]),
            stale,
            week_start=WEEK_START,
        )
        is None
    )


def test_views_taken_away() -> None:
    before, start, end = marks(100, 400)
    fewer = (end[0], start[1] - 50)
    assert pace_of(before, start, fewer, week_start=WEEK_START) is None


def test_paces_follow_the_readings_not_the_week() -> None:
    """Readings nine and six days apart give paces a day over each."""
    before = (WEEK_START - timedelta(days=9), 0.0)
    start = (WEEK_START - timedelta(days=1), 800.0)
    end = (WEEK_START + timedelta(days=5), 3800.0)
    paces = pace_of(before, start, end, week_start=WEEK_START)
    assert paces is not None
    assert paces.previous_pace == pytest.approx(100.0)
    assert paces.pace == pytest.approx(500.0)


def candidate(
    channel: int, item: int, previous: float, now: float
) -> Candidate[int, int]:
    return Candidate(
        channel=channel, item=item, paces=judged(previous, now), norm=1000.0
    )


def test_one_revival_per_channel_with_its_others() -> None:
    """Shares of 2.8 and 1.12 in one channel: the 2.8, and one more."""
    found = rank_revivals(
        [candidate(1, 10, 100, 400), candidate(1, 11, 20, 160)],
        acceleration=3,
        share=0.5,
    )
    assert [(r.candidate.item, r.others) for r in found] == [(10, 1)]
    assert found[0].candidate.share == pytest.approx(2.8)


def test_revivals_rank_by_share_then_acceleration_then_item() -> None:
    found = rank_revivals(
        [
            candidate(1, 10, 100, 400),  # share 2.8, four times
            candidate(2, 20, 50, 400),  # share 2.8, eight times
            candidate(3, 30, 0, 400),  # share 2.8, no previous growth
            candidate(4, 40, 100, 900),  # share 6.3
            candidate(5, 50, 100, 50),  # decayed
        ],
        acceleration=3,
        share=0.5,
    )
    assert [r.candidate.channel for r in found] == [4, 3, 2, 1]
