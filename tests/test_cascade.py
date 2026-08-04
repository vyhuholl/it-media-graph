"""Measuring how far a post has travelled.

Pure arithmetic over edges, so every test states a moment. The cases
worth the most here are the three exclusions — own family, the window,
and a repost predating its post — because each removes a way to be
*wrong* rather than a way to be uninteresting, and a refactor that
"simplifies" any of them changes what the alert means.
"""

from datetime import UTC, datetime, timedelta

from itgraph.alerts.cascade import (
    Cascade,
    RepostEdge,
    crossings,
    family_counts,
)

PUBLISHED = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
WINDOW = timedelta(hours=6)
POST = (1000000001, 500)


def repost(
    src_family: int,
    *,
    after: timedelta = timedelta(hours=1),
    post_family: int = 1000000001,
    post_key: tuple[int, int] = POST,
    published: datetime = PUBLISHED,
) -> RepostEdge:
    return RepostEdge(
        post_key=post_key,
        post_published_at=published,
        post_family=post_family,
        src_family=src_family,
        reposted_at=published + after,
    )


def counts(*edges: RepostEdge) -> dict[tuple[int, int], set[int]]:
    return family_counts(edges, now=PUBLISHED + WINDOW, window=WINDOW)


def test_distinct_families_are_counted() -> None:
    assert counts(repost(10), repost(20), repost(30))[POST] == {10, 20, 30}


def test_one_family_carrying_a_post_twice_counts_once() -> None:
    """The difference between measuring travel and measuring volume."""
    result = counts(
        repost(10, after=timedelta(hours=1)),
        repost(10, after=timedelta(hours=2)),
    )
    assert result[POST] == {10}


def test_two_channels_of_one_family_are_one_source() -> None:
    """Both resolve to the same family key before they get here."""
    assert counts(repost(10), repost(10))[POST] == {10}


def test_the_posts_own_family_does_not_count() -> None:
    """A network carrying its own post is distribution, not travel."""
    assert counts(repost(1000000001)) == {}


def test_outside_carriers_still_count_when_the_family_also_reposts() -> None:
    result = counts(repost(1000000001), repost(10), repost(20))
    assert result[POST] == {10, 20}


def test_a_repost_after_the_window_does_not_count() -> None:
    """A post picked up in three days travelled — but not *now*.

    The window is what makes this an alert rather than a report.
    """
    assert counts(repost(10, after=timedelta(hours=7))) == {}


def test_a_repost_exactly_at_the_window_edge_counts() -> None:
    assert counts(repost(10, after=WINDOW))[POST] == {10}


def test_a_repost_predating_its_post_is_dropped() -> None:
    """Clock skew and a wrong `dst_published_at` both produce these.

    A negative age passes every window test silently, which is the worst
    way for bad data to behave.
    """
    assert counts(repost(10, after=-timedelta(minutes=5))) == {}


def test_posts_are_counted_independently() -> None:
    other = (1000000002, 900)
    result = counts(
        repost(10),
        repost(20),
        repost(30, post_key=other, post_family=1000000002),
    )
    assert result[POST] == {10, 20}
    assert result[other] == {30}


def test_a_post_crosses_every_band_it_reaches() -> None:
    """The second alert is what says a post is still going.

    Jumping straight to the highest band would lose that.
    """
    found = crossings(
        [repost(10), repost(20), repost(30)],
        bands=(2, 3),
        now=PUBLISHED + WINDOW,
        window=WINDOW,
    )
    assert found == [
        Cascade(post_key=POST, band=2, value=3),
        Cascade(post_key=POST, band=3, value=3),
    ]


def test_a_post_below_every_band_crosses_nothing() -> None:
    found = crossings(
        [repost(10)], bands=(2, 3), now=PUBLISHED + WINDOW, window=WINDOW
    )
    assert found == []


def test_an_already_raised_band_is_not_offered_again() -> None:
    """An optimisation, not the correctness mechanism.

    The unique constraint is what actually prevents a second alert; this
    only avoids asking the database to reject rows it has rejected before.
    """
    found = crossings(
        [repost(10), repost(20)],
        bands=(2, 3),
        now=PUBLISHED + WINDOW,
        window=WINDOW,
        already_raised={POST: {2}},
    )
    assert found == []


def test_a_higher_band_is_still_offered_after_the_lower_one() -> None:
    found = crossings(
        [repost(10), repost(20), repost(30)],
        bands=(2, 3),
        now=PUBLISHED + WINDOW,
        window=WINDOW,
        already_raised={POST: {2}},
    )
    assert found == [Cascade(post_key=POST, band=3, value=3)]


def test_the_value_is_what_was_measured_not_the_band() -> None:
    """The stored number is what a later verdict is about."""
    found = crossings(
        [repost(n) for n in (10, 20, 30, 40)],
        bands=(2,),
        now=PUBLISHED + WINDOW,
        window=WINDOW,
    )
    assert found[0].value == 4
    assert found[0].band == 2


def test_old_posts_cross_nothing_however_far_they_travelled() -> None:
    """This is what removes the first-run problem structurally.

    A pass run for the first time over a year of edges raises nothing
    about old posts, and needs no record of what was already handled to
    achieve it.
    """
    ancient = PUBLISHED - timedelta(days=200)
    found = crossings(
        [
            repost(10, published=ancient),
            repost(20, published=ancient),
            repost(30, published=ancient),
        ],
        bands=(2, 3),
        now=PUBLISHED,
        window=WINDOW,
    )
    assert found == []


def test_the_output_is_stable() -> None:
    """Sorted, so a report reads the same twice and a test needs no sort."""
    second = (1000000000, 1)
    found = crossings(
        [
            repost(10),
            repost(20),
            repost(30, post_key=second, post_family=1000000000),
            repost(40, post_key=second, post_family=1000000000),
        ],
        bands=(2,),
        now=PUBLISHED + WINDOW,
        window=WINDOW,
    )
    assert [entry.post_key for entry in found] == [second, POST]
