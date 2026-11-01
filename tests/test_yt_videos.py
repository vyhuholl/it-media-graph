"""Asking for videos by id: fifty to a request, declared fields, dead ids absent."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from ytfakes import FakeYouTube, no_sleep, use_key, video_id, video_resource

from itgraph.db.models import YtCommand
from itgraph.db.session import Database
from itgraph.yt.client import YouTube, youtube
from itgraph.yt.videos import (
    FIELDS,
    LOOP,
    PARTS,
    PAST_UPLOADS,
    Asked,
    by_ids,
    for_past_uploads,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


@asynccontextmanager
async def client(
    database: Database, fake: FakeYouTube
) -> AsyncIterator[YouTube]:
    async with youtube(
        database, YtCommand.WATCH, transport=fake.transport(), sleep=no_sleep
    ) as connected:
        yield connected


def _fake(*numbers: int, batch_404: bool = False) -> FakeYouTube:
    return FakeYouTube(
        videos=[video_resource(n, published=NOW) for n in numbers],
        batch_404=batch_404,
    )


async def test_sixty_ids_make_two_requests(database: Database) -> None:
    fake = _fake(*range(60))

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(n) for n in range(60)])

    assert len(found) == 60
    calls = fake.calls("videos")
    assert [len(call["id"].split(",")) for call in calls] == [50, 10]
    for call in calls:
        assert call["part"] == PARTS
        assert call["fields"] == FIELDS


async def test_the_declared_fields_and_no_others() -> None:
    """What the privacy policy lists, and nothing it does not."""
    for wanted in (
        "publishedAt",
        "channelId",
        "title",
        "description",
        "liveBroadcastContent",
        "duration",
        "actualStartTime",
        "scheduledStartTime",
        "viewCount",
        "likeCount",
        "commentCount",
    ):
        assert wanted in FIELDS
    for unwanted in (
        "tags",
        "thumbnails",
        "categoryId",
        "localized",
        "concurrentViewers",
    ):
        assert unwanted not in FIELDS


async def test_a_missing_video_is_absent(database: Database) -> None:
    fake = _fake(1)

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(1), video_id(2)])

    assert list(found) == [video_id(1)]
    assert len(fake.requests) == 1


async def test_a_404_batch_still_yields_every_live_video(
    database: Database,
) -> None:
    fake = _fake(1, 3, batch_404=True)

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(1), video_id(2), video_id(3)])

    assert sorted(found) == [video_id(1), video_id(3)]
    # The batch, then one request per id.
    assert len(fake.requests) == 4


# --- past uploads -------------------------------------------------------

# Every field a video request may name, and the phrase the privacy policy
# uses for it. A field missing here, or a phrase missing from the page,
# is a field read that the page does not declare.
DECLARED = {
    "id": "the video ID",
    "publishedAt": "publication time",
    "channelId": "the ID of the channel it belongs to",
    "title": "title",
    "description": "description",
    "liveBroadcastContent": "live-broadcast state",
    "duration": "duration",
    "actualStartTime": "scheduled and actual start and end times",
    "actualEndTime": "scheduled and actual start and end times",
    "scheduledStartTime": "scheduled and actual start and end times",
    "scheduledEndTime": "scheduled and actual start and end times",
    "viewCount": "view, like and comment counts",
    "likeCount": "view, like and comment counts",
    "commentCount": "view, like and comment counts",
}
# Names in a `fields` string that are parts or containers, not data.
STRUCTURE = {"items", "snippet", "contentDetails", "liveStreamingDetails"}
STRUCTURE |= {"statistics"}


def _named(fields: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]+", fields)) - STRUCTURE


@pytest.mark.parametrize("asked", [LOOP, PAST_UPLOADS], ids=["loop", "past"])
def test_every_field_named_is_on_the_privacy_page(asked: Asked) -> None:
    page = (Path(__file__).parent.parent / "docs" / "privacy.html").read_text()
    videos = page[page.index("About videos") :]
    videos = videos[: videos.index("</li>")]
    for field in _named(asked.fields):
        assert field in DECLARED, f"{field} is read and not declared"
        assert DECLARED[field] in videos, f"{DECLARED[field]!r} not on page"


def test_a_past_upload_is_asked_for_without_counters() -> None:
    assert "statistics" not in PAST_UPLOADS.parts.split(",")
    assert not _named(PAST_UPLOADS.fields) & {
        "viewCount",
        "likeCount",
        "commentCount",
    }
    # Everything else the loop reads, so the payload has the same shape.
    assert _named(PAST_UPLOADS.fields) == _named(FIELDS) - {
        "viewCount",
        "likeCount",
        "commentCount",
    }


def test_the_loop_s_request_is_unchanged() -> None:
    assert LOOP == Asked(parts=PARTS, fields=FIELDS)
    assert FIELDS == (
        "items(id,"
        "snippet(publishedAt,channelId,title,description,liveBroadcastContent),"
        "contentDetails/duration,"
        "liveStreamingDetails("
        "actualStartTime,actualEndTime,scheduledStartTime,scheduledEndTime),"
        "statistics(viewCount,likeCount,commentCount))"
    )


def test_past_uploads_get_counters_only_after_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from itgraph import yt_policy

    assert for_past_uploads() == PAST_UPLOADS
    monkeypatch.setattr(
        yt_policy, "DERIVED_METRICS_ACCEPTED_ON", date(2026, 12, 1)
    )
    assert for_past_uploads() == LOOP


async def test_a_past_upload_request_comes_back_without_counters(
    database: Database,
) -> None:
    fake = _fake(1)

    async with client(database, fake) as yt:
        found = await by_ids(yt, [video_id(1)], asked=PAST_UPLOADS)

    [call] = fake.calls("videos")
    assert (call["part"], call["fields"]) == (
        PAST_UPLOADS.parts,
        PAST_UPLOADS.fields,
    )
    assert "statistics" not in found[video_id(1)]
