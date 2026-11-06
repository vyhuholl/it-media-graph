"""The exporter's listener: one path, one method, on a port of its own.

Written on `asyncio.start_server`, because the whole of what it serves is
one `GET`, and a web framework would be a dependency to keep up for it.
The request is read as far as its headers, so that a client is not cut off
mid-request, and no further: nothing here ever needs a body.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from itgraph.db.session import Database
from itgraph.metrics.series import read_families, render

__all__ = ["CONTENT_TYPE", "METRICS_PORT", "serve"]

logger = logging.getLogger(__name__)

METRICS_PORT = 9090
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# A scraper sends a dozen headers at most, promptly. Bounded, so that a
# connection nobody finishes cannot sit here for the life of the process.
_MAX_HEADERS = 64
_READ_TIMEOUT_SECONDS = 5.0

_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    503: "Service Unavailable",
}


async def _read_request_line(reader: asyncio.StreamReader) -> list[str]:
    first = await reader.readline()
    for _ in range(_MAX_HEADERS):
        if await reader.readline() in (b"\r\n", b"\n", b""):
            break
    return first.decode("latin-1").split()


async def _respond(
    database: Database, method: str, target: str
) -> tuple[int, str]:
    if target != "/metrics":
        return 404, "not found\n"
    if method != "GET":
        return 405, "method not allowed\n"
    try:
        async with database.session() as session:
            families = await read_families(session, now=datetime.now(UTC))
    except (OSError, SQLAlchemyError) as exc:
        # The type alone: a connection error's text can carry the URL.
        logger.warning("could not read the series: %s", type(exc).__name__)
        return 503, "the database did not answer\n"
    return 200, render(families)


async def _handle(
    database: Database,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            request = await _read_request_line(reader)
        if len(request) != 3:
            status, body = 400, "bad request\n"
        else:
            status, body = await _respond(database, request[0], request[1])
        encoded = body.encode()
        writer.write(
            (
                f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
                f"Content-Type: {CONTENT_TYPE}\r\n"
                f"Content-Length: {len(encoded)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + encoded
        )
        await writer.drain()
    except TimeoutError, ConnectionError:
        pass
    finally:
        writer.close()


async def serve(
    database: Database,
    *,
    stop: asyncio.Event,
    host: str = "0.0.0.0",
    port: int = METRICS_PORT,
    bound: asyncio.Future[int] | None = None,
) -> None:
    """Serve until ``stop`` is set. ``bound`` receives the port taken."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await _handle(database, reader, writer)

    server = await asyncio.start_server(handle, host, port)
    async with server:
        if bound is not None:
            bound.set_result(server.sockets[0].getsockname()[1])
        logger.info("serving the series on port %d", port)
        await stop.wait()
