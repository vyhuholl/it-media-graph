"""The week and the ranking both platforms' weekly lists share; pure."""

from datetime import UTC, datetime, timedelta

from itgraph.scoring.score import Best, Score
from itgraph.scoring.weeks import Ranked, merge_best, rank_week

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
