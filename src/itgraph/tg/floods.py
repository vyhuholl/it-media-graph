"""Turning a caught rate limit into a row, without endangering the run.

Two jobs, both narrow. Work out which Telegram method was limited, and
write that down somewhere durable — because the question is always asked
after the fact, usually a day later, and a log line answers it only for
someone who was already watching.

Neither job may cost anything. A rate limit is survivable; telemetry that
turns one into a crashed run, or into a lost batch of history, is worse
than no telemetry at all. Both hazards are handled below and neither is
theoretical.
"""

import logging
from dataclasses import dataclass, replace
from typing import Any

from itgraph.db.floods import store_flood_event
from itgraph.db.models import CollectionCommand
from itgraph.db.session import Database

__all__ = ["UNKNOWN_METHOD", "FloodRecorder", "method_name"]

logger = logging.getLogger(__name__)

# A rate limit that named no request. A real value, not a failure: the
# duration and the timing are still worth having, and refusing to record
# would throw those away too.
UNKNOWN_METHOD = "unknown"

# Telegram wraps a real request inside an invocation wrapper, and the
# wrapper's name would file `getFullChannel` and `resolveUsername` under
# the same label — which would defeat the entire point of the table.
#
# Matched by name rather than against `telethon.errors.rpcbaseerrors.
# _NESTS_QUERY`, and not only because that tuple is private. It is also
# incomplete: it lists seven wrappers, while eleven exist in the
# generated classes — `InvokeWithApnsSecretRequest`,
# `InvokeWithBusinessConnectionRequest`,
# `InvokeWithGooglePlayIntegrityRequest` and
# `InvokeWithReCaptchaRequest` are absent from it. A name rule covers the
# ones Telethon's own tuple misses, and covers whatever it adds next.
#
# Duck-typing on the presence of `.query` would be worse than either:
# `channels.SearchPostsRequest`, `channels.CheckSearchPostsFloodRequest`
# and `messages.GetInlineBotResultsRequest` all carry a `query` that is a
# search string, and unwrapping one would store the name of a `str`.
_WRAPPER_PREFIX = "Invoke"
_WRAPPER_NAMES = frozenset({"InitConnectionRequest"})

# Real nesting is two deep at most. The bound is here so a cycle in an
# unexpected shape cannot hang the one code path that must never hang.
_MAX_UNWRAP = 10


def _is_wrapper(request: Any) -> bool:
    name = type(request).__name__
    return name.startswith(_WRAPPER_PREFIX) or name in _WRAPPER_NAMES


def method_name(request: Any) -> str:
    """The name of the method that was actually called.

    Unwraps invocation wrappers down to the request underneath. An
    unrecognised shape stops the walk and reports whatever it reached: a
    slightly wrong name is recoverable, and a crash inside a rate-limit
    handler is not.
    """
    if request is None:
        return UNKNOWN_METHOD

    for _ in range(_MAX_UNWRAP):
        if not _is_wrapper(request):
            break
        inner = getattr(request, "query", None)
        # A wrapper whose payload is not a request is not a wrapper.
        if inner is None or isinstance(inner, str | bytes):
            break
        request = inner

    return type(request).__name__


@dataclass(frozen=True, slots=True)
class FloodRecorder:
    """Writes rate-limit events for one run, and never raises.

    Carries the run's identity — which command, and which channel is
    being walked — because neither can be worked out at the point the
    limit is caught: both commands go through the same handler, and it
    has no idea what the caller was doing.
    """

    database: Database
    command: CollectionCommand
    channel_id: int | None = None

    def for_channel(self, channel_id: int | None) -> FloodRecorder:
        """The same recorder, attributing events to one channel."""
        return replace(self, channel_id=channel_id)

    async def record(
        self, *, request: Any, seconds: int, halted: bool
    ) -> None:
        """Write one event. Any failure is logged and then dropped.

        On a session of its own, never the caller's. The handler that
        calls this runs mid-walk, and ``backfill_channel`` commits per
        batch — so writing on the caller's session would mean either
        committing a half-finished batch or rolling it back inside the
        handler for an unrelated error, and that is how a run loses
        history it already fetched. A separate session is a separate
        transaction: this commits, and whatever the caller has pending
        stays pending.

        The engine is shared, so this costs a pooled connection for the
        length of one insert, on a path that is about to sleep for
        minutes anyway.
        """
        try:
            async with self.database.session() as session:
                await store_flood_event(
                    session,
                    method=method_name(request),
                    seconds=seconds,
                    command=self.command,
                    channel_id=self.channel_id,
                    halted=halted,
                )
        except Exception:
            # Deliberately everything. Recording is a convenience; the
            # rate limit still has to be handled the way it always was,
            # and no diagnostic is worth turning a survivable wait into a
            # failed run.
            logger.warning("could not record the rate limit", exc_info=True)
