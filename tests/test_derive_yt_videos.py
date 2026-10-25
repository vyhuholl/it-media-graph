"""Reading a stored YouTube video payload: the facts and the counters.

Payloads are built by ``ytfakes.video_resource`` in the shape the
requested ``fields`` produce. The cases that matter are the absent ones:
a counter YouTube left out must read as absent, never as zero.
"""

from datetime import UTC, datetime

from ytfakes import channel_id, video_id, video_resource

from itgraph.derive.yt_videos import Counters, counters_of, facts_of

PUBLISHED = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def test_the_counters_are_read_from_strings() -> None:
    payload = video_resource(1, published=PUBLISHED, views=1200, likes=80)

    assert counters_of(payload) == Counters(views=1200, likes=80, comments=3)


def test_a_missing_like_count_is_absent_not_zero() -> None:
    payload = video_resource(1, published=PUBLISHED, likes=None)

    counters = counters_of(payload)
    assert counters.likes is None
    assert counters.views is not None


def test_counts_beyond_two_billion_survive() -> None:
    payload = video_resource(1, published=PUBLISHED, views=3_000_000_000)

    assert counters_of(payload).views == 3_000_000_000


def test_something_that_is_not_a_count_is_absent() -> None:
    payload = video_resource(1, published=PUBLISHED)
    payload["statistics"] = {
        "viewCount": True,
        "likeCount": "-4",
        "commentCount": "12k",
    }

    assert counters_of(payload) == Counters()


def test_a_payload_without_statistics_has_no_counters() -> None:
    assert counters_of({"id": video_id(1)}) == Counters()


def test_the_facts_of_an_ordinary_upload() -> None:
    facts = facts_of(video_resource(1, published=PUBLISHED, channel=4))

    assert facts.channel_id == channel_id(4)
    assert facts.published_at == PUBLISHED
    assert facts.actual_start is None
    assert facts.broadcast == "none"
    assert not facts.upcoming


def test_a_completed_stream_yields_its_actual_start() -> None:
    went_live = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
    facts = facts_of(
        video_resource(1, published=PUBLISHED, live_started=went_live)
    )

    assert facts.actual_start == went_live
    assert facts.broadcast == "none"


def test_an_upcoming_broadcast_is_recognised() -> None:
    tomorrow = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)
    facts = facts_of(
        video_resource(
            1, published=PUBLISHED, broadcast="upcoming", scheduled=tomorrow
        )
    )

    assert facts.upcoming
    assert facts.scheduled_start == tomorrow
    assert facts.actual_start is None


def test_an_unknown_broadcast_state_reads_as_none() -> None:
    payload = video_resource(1, published=PUBLISHED)
    payload["snippet"]["liveBroadcastContent"] = "rerun"

    assert facts_of(payload).broadcast == "none"


def test_a_moment_without_a_zone_is_not_trusted() -> None:
    payload = video_resource(1, published=PUBLISHED)
    payload["snippet"]["publishedAt"] = "2026-10-26T12:00:00"

    assert facts_of(payload).published_at is None
