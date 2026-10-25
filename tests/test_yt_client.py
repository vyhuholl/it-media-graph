"""The YouTube client: booked before sending, the key never in sight."""

import logging
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from ytfakes import (
    API_KEY,
    FakeYouTube,
    channel_id,
    channel_resource,
    error_body,
    no_sleep,
    use_key,
)

from itgraph.config import settings
from itgraph.db.models import (
    YtCommand,
    YtQuotaDay,
    YtQuotaExhausted,
    YtRequest,
    YtRequestOutcome,
)
from itgraph.db.session import Database
from itgraph.db.yt_quota import QuotaExhaustedError, check_not_exhausted
from itgraph.yt.client import KEY_HEADER, youtube
from itgraph.yt.errors import ApiError, MissingKeyError

PARAMS = {"part": "snippet", "id": channel_id(1)}


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


async def _get(database: Database, fake: FakeYouTube) -> dict[str, Any] | None:
    async with youtube(
        database,
        YtCommand.ADD,
        transport=fake.transport(),
        sleep=no_sleep,
    ) as client:
        return await client.get("channels.list", PARAMS)


async def _spent(database: Database) -> int:
    async with database.session() as session:
        rows = await session.scalars(select(YtQuotaDay.spent))
        return sum(rows)


async def _outcomes(database: Database) -> list[YtRequestOutcome | None]:
    async with database.session() as session:
        rows = await session.scalars(
            select(YtRequest.outcome).order_by(YtRequest.id)
        )
        return list(rows)


async def test_the_key_travels_in_a_header_not_the_url(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1))

    body = await _get(database, fake)

    assert body is not None and body["items"][0]["id"] == channel_id(1)
    [request] = fake.requests
    assert request.headers[KEY_HEADER] == API_KEY
    assert API_KEY not in str(request.url)
    assert "key" not in request.url.params


async def test_the_key_never_reaches_the_log(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """Including when YouTube echoes it back in an error message."""
    caplog.set_level(logging.DEBUG)
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [
        httpx.Response(503, json=error_body(503, "backendError")),
        httpx.Response(
            400,
            json=error_body(400, "keyInvalid", f"bad key {API_KEY}"),
        ),
    ]

    with pytest.raises(ApiError) as caught:
        await _get(database, fake)

    assert API_KEY not in str(caught.value)
    assert API_KEY not in caplog.text
    assert "HTTP Request" in caplog.text  # httpx did log, and safely


async def test_a_success_is_booked_and_recorded(database: Database) -> None:
    await _get(database, FakeYouTube(channel_resource(1)))

    assert await _spent(database) == 1
    assert await _outcomes(database) == [YtRequestOutcome.OK]


async def test_not_found_is_an_answer(database: Database) -> None:
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(404, json=error_body(404, "channelNotFound"))
    ]

    assert await _get(database, fake) is None
    assert await _outcomes(database) == [YtRequestOutcome.NOT_FOUND]


async def test_quota_exceeded_ends_the_day_and_is_not_retried(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [httpx.Response(403, json=error_body(403, "quotaExceeded"))]

    with pytest.raises(QuotaExhaustedError):
        await _get(database, fake)

    assert len(fake.requests) == 1
    assert await _outcomes(database) == [YtRequestOutcome.QUOTA_EXCEEDED]
    with pytest.raises(QuotaExhaustedError):
        await check_not_exhausted(database)
    async with database.session() as session:
        [row] = await session.scalars(select(YtQuotaExhausted))
    assert row.command is YtCommand.ADD


async def test_an_unreadable_403_is_not_taken_for_exhaustion(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [httpx.Response(403, text="<html>Forbidden</html>")]

    with pytest.raises(ApiError, match="403"):
        await _get(database, fake)

    await check_not_exhausted(database)  # does not raise
    assert len(fake.requests) == 1


async def test_a_server_error_is_retried_and_both_attempts_count(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [httpx.Response(500, json=error_body(500, "backendError"))]

    body = await _get(database, fake)

    assert body is not None
    assert len(fake.requests) == 2
    assert await _spent(database) == 2
    assert await _outcomes(database) == [
        YtRequestOutcome.ERROR,
        YtRequestOutcome.OK,
    ]


async def test_a_rate_limit_is_retried(database: Database) -> None:
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [
        httpx.Response(429, json=error_body(429, "rateLimitExceeded"))
    ]

    assert await _get(database, fake) is not None
    assert len(fake.requests) == 2


async def test_persistent_server_errors_stop_the_command(
    database: Database,
) -> None:
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [
        httpx.Response(503, json=error_body(503, "backendError"))
        for _ in range(5)
    ]

    with pytest.raises(ApiError, match="503 backendError"):
        await _get(database, fake)

    assert len(fake.requests) == 3
    assert await _spent(database) == 3


async def test_a_client_error_is_not_retried(database: Database) -> None:
    fake = FakeYouTube(channel_resource(1))
    fake.queued = [
        httpx.Response(400, json=error_body(400, "invalidCriteria", "nope"))
    ]

    with pytest.raises(ApiError) as caught:
        await _get(database, fake)

    assert len(fake.requests) == 1
    assert caught.value.status == 400
    assert caught.value.reason == "invalidCriteria"
    assert "invalidCriteria" in str(caught.value)


async def test_no_answer_leaves_the_outcome_unknown(
    database: Database,
) -> None:
    """A broken connection may or may not have been charged; the units
    stay booked and the journal says it does not know."""

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    async with youtube(
        database,
        YtCommand.ADD,
        transport=httpx.MockTransport(broken),
        sleep=no_sleep,
    ) as client:
        with pytest.raises(ApiError, match="could not reach YouTube"):
            await client.get("channels.list", PARAMS)

    assert await _spent(database) == 1
    assert await _outcomes(database) == [None]


async def test_a_missing_key_fails_before_any_request(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", None)
    fake = FakeYouTube(channel_resource(1))

    with pytest.raises(MissingKeyError, match="YOUTUBE_API_KEY"):
        await _get(database, fake)

    assert fake.requests == []
    assert await _spent(database) == 0


async def test_the_mtproto_proxy_is_not_used(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured for Telegram, and none of YouTube's business — nor is
    any proxy an environment variable happens to name."""
    monkeypatch.setattr(settings, "proxy_host", "proxy.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    built: list[dict[str, Any]] = []
    original = httpx.AsyncClient.__init__

    def spy(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        built.append(kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy)

    await _get(database, FakeYouTube(channel_resource(1)))

    [kwargs] = built
    assert kwargs["trust_env"] is False
    assert "proxy" not in kwargs and "mounts" not in kwargs
