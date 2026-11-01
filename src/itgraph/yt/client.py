"""The YouTube Data API client: the only place an HTTP client is built.

Every request goes through :meth:`YouTube.get`, which books its units in
the ledger before sending and records what came of it afterwards. There
is no other path to the API, so there is no request the ledger did not
see.

The key travels in the ``x-goog-api-key`` header. The ``key`` query
parameter would work too, and would put the key in every URL — which
httpx logs at INFO, so into journald on the host, on every request.

What an answer means:

- 200 is the body.
- 404 is "no such channel, playlist or video", returned as ``None``.
- 403 ``quotaExceeded`` ends the quota day for the bucket the method is
  charged to — search's own, or the main one every other method shares:
  recorded, never retried.
- 5xx and 429 are retried, each attempt booked again.
- Anything else stops the command with what YouTube said. So does a 403
  whose body cannot be read: failing closed must not mean marking a day
  spent that is not.
- A request that never got an answer — a timeout, a broken connection —
  stops the command and leaves its outcome unknown in the journal, which
  is what it is: YouTube may have charged it.
"""

import asyncio
import logging
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
)
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any

import httpx

from itgraph.config import settings
from itgraph.db.models import YtCommand, YtQuotaBucket, YtRequestOutcome
from itgraph.db.session import Database
from itgraph.db.yt_quota import (
    QuotaExhaustedError,
    book,
    bucket_of,
    record_exhaustion,
    record_outcome,
)
from itgraph.yt.errors import ApiError, MissingKeyError, RequestLimitReached
from itgraph.yt_quota import next_reset

__all__ = [
    "API_ROOT",
    "COSTS",
    "KEY_HEADER",
    "SEPARATE_BUCKETS",
    "YouTube",
    "bucket_of_method",
    "request_urls_unlogged",
    "youtube",
]

logger = logging.getLogger(__name__)

API_ROOT = "https://www.googleapis.com/youtube/v3/"
KEY_HEADER = "x-goog-api-key"

# Units per call, from the API reference. A method missing here cannot
# be called at all, which is the point: a request whose price nobody
# looked up is a request the ledger cannot book honestly.
COSTS: Mapping[str, int] = {
    "channels.list": 1,
    "channelSections.list": 1,
    "playlistItems.list": 1,
    "search.list": 1,
    "videos.list": 1,
}

# Which of Google's buckets a method is charged to, when not the main
# one. Since 2026-06-01 `search.list` has a bucket of its own — 100 calls
# a day at a unit each, outside the daily 10,000 — and a `quotaExceeded`
# it draws ends that bucket's day and no other.
SEPARATE_BUCKETS: Mapping[str, YtQuotaBucket] = {
    "search.list": YtQuotaBucket.SEARCH,
}


def bucket_of_method(method: str) -> YtQuotaBucket:
    """The bucket Google charges a method to."""
    return SEPARATE_BUCKETS.get(method, YtQuotaBucket.MAIN)


# Attempts at one request, and the wait before each attempt after the
# first. Three, because a 5xx from Google is nearly always one bad
# backend and the next attempt lands elsewhere; a fourth would be paying
# for the same answer again.
ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 4.0)

Sleep = Callable[[float], Awaitable[None]]


def _what_youtube_said(response: httpx.Response) -> tuple[str | None, str]:
    """The reason and message from Google's error body, if there is one.

    The shape is Google's general one — ``error.errors[].reason`` — and
    not shown on YouTube's own errors page, so every step is checked
    rather than assumed. A body that does not fit gives no reason, and a
    response with no reason is never taken for an exhausted quota.
    """
    try:
        body = response.json()
    except ValueError:
        return None, response.text[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, ""
    message = str(error.get("message", ""))
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        reason = errors[0].get("reason")
        if isinstance(reason, str):
            return reason, message
    return None, message


class YouTube:
    """One command's connection to the Data API.

    Counts the requests it made — attempts, not calls — and refuses one
    past ``limit``, so ``--limit`` bounds everything a command sends,
    retries and the one-by-one fallback for a failed batch included.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        database: Database,
        command: YtCommand,
        *,
        key: str,
        limit: int | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._database = database
        self._command = command
        self._key = key
        self._sleep = sleep
        self.limit = limit
        self.requests_made = 0

    def _scrub(self, text: str) -> str:
        """Text YouTube sent back, with the key taken out if it echoed it."""
        return text.replace(self._key, "[key]") if self._key else text

    async def get(
        self, method: str, params: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """One API call: the JSON body, or ``None`` if YouTube says 404.

        Raises ``QuotaExhaustedError`` or ``PoolSpentError`` (from the
        ledger) when the call may not be made or YouTube refuses it for
        quota, and ``ApiError`` for everything else that is not an
        answer.

        A method charged to another bucket than the command's pool is
        refused before anything is booked: booking a search against the
        general pool would spend units Google never charged, and a
        ``quotaExceeded`` it drew would end the wrong bucket's day.
        """
        units = COSTS[method]
        bucket = bucket_of_method(method)
        if bucket is not bucket_of(self._command):
            raise ValueError(
                f"{method} is charged to the {bucket.value} bucket, and "
                f"`yt {self._command.value}` spends from the "
                f"{bucket_of(self._command).value} one"
            )
        path = method.split(".")[0]
        for attempt in range(ATTEMPTS):
            if attempt:
                await self._sleep(BACKOFF_SECONDS[attempt - 1])
            if self.limit is not None and self.requests_made >= self.limit:
                raise RequestLimitReached(self.limit)
            booking = await book(
                self._database,
                command=self._command,
                method=method,
                units=units,
            )
            self.requests_made += 1
            try:
                response = await self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                # No answer, so no outcome: the journal keeps it unknown.
                raise ApiError(None, None, type(exc).__name__) from exc

            status = response.status_code
            if status == httpx.codes.OK:
                await record_outcome(
                    self._database, booking, YtRequestOutcome.OK
                )
                body: dict[str, Any] = response.json()
                return body

            reason, message = _what_youtube_said(response)
            if status == httpx.codes.NOT_FOUND:
                await record_outcome(
                    self._database, booking, YtRequestOutcome.NOT_FOUND
                )
                return None

            if status == httpx.codes.FORBIDDEN and reason == "quotaExceeded":
                await record_outcome(
                    self._database, booking, YtRequestOutcome.QUOTA_EXCEEDED
                )
                now = datetime.now(UTC)
                # The command's bucket is the method's, as checked above.
                await record_exhaustion(
                    self._database, command=self._command, now=now
                )
                raise QuotaExhaustedError(next_reset(now), bucket)

            await record_outcome(
                self._database, booking, YtRequestOutcome.ERROR
            )
            transient = (
                status >= httpx.codes.INTERNAL_SERVER_ERROR
                or status == httpx.codes.TOO_MANY_REQUESTS
            )
            if transient and attempt + 1 < ATTEMPTS:
                logger.warning(
                    "%s answered %s (%s); attempt %d of %d",
                    method,
                    status,
                    reason or "no reason",
                    attempt + 1,
                    ATTEMPTS,
                )
                continue
            raise ApiError(status, reason, self._scrub(message) or "-")

        raise AssertionError("unreachable: the last attempt raises")


@contextmanager
def request_urls_unlogged() -> Iterator[None]:
    """Keep httpx's per-request line, and the URL in it, out of the log.

    httpx logs every URL it requests at INFO. Most commands' URLs carry
    ids only; a lookup's carries the handle or legacy name it asks about,
    which must not reach a log. ``--verbose`` does not bring it back, for
    the same reason. The level is restored when the block ends.
    """
    log = logging.getLogger("httpx")
    previous = log.level
    log.setLevel(logging.WARNING)
    try:
        yield
    finally:
        log.setLevel(previous)


@asynccontextmanager
async def youtube(
    database: Database,
    command: YtCommand,
    *,
    limit: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
) -> AsyncIterator[YouTube]:
    """A ``YouTube`` for one command run, closed when it ends.

    Raises ``MissingKeyError`` before anything is built when no key is
    configured. ``trust_env`` is off: the request goes directly, never
    through a proxy some environment variable names — the MTProto proxy
    is Telegram's, and this is not Telegram.
    """
    if settings.youtube_api_key is None:
        raise MissingKeyError()
    key = settings.youtube_api_key.get_secret_value()
    async with httpx.AsyncClient(
        base_url=API_ROOT,
        headers={KEY_HEADER: key},
        timeout=settings.youtube_request_timeout_seconds,
        transport=transport,
        trust_env=False,
    ) as http:
        yield YouTube(
            http, database, command, key=key, limit=limit, sleep=sleep
        )
