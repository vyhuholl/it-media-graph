"""How long to wait before the next request, and the waiting itself.

Every pacing sleep in the project goes through here. That is worth one
module rather than three call sites because the collector now waits for
five different reasons — a jittered gap, a rare long pause, the step
between two channels, a FloodWait slept off, a FloodWait that stops the
run — and only the first three are politeness. Keeping them together
keeps "we are being polite" distinct from "we were told to stop", which
is a distinction the code should not lose, and it gives the tests one
seam to patch instead of one per module.

What this buys, honestly: the randomized gaps do not reduce how many
requests a run makes, so they cannot move a limit that counts calls per
day. They are cheap insurance against pattern detection that nobody
outside Telegram can confirm exists. The pause between channels is the
part that does something concrete — it spaces the per-channel requests
that carry daily quotas, at the one boundary in the walk that used to
have no gap at all.

Nothing here holds state beyond the random source, so nothing needs
resetting between runs.
"""

import asyncio
import secrets

from itgraph.config import settings

__all__ = ["channel_gap", "pace", "pause_between_channels", "request_gap"]

# The one random source in the project; nothing else imports `random`.
#
# `secrets.SystemRandom` is `random.SystemRandom` under another name: the
# full `random` API — `uniform`, which bare `secrets` does not offer —
# drawn from the OS CSPRNG. Cryptographic unpredictability is not the
# point and buys nothing here; no adversary is predicting the next gap
# from the previous ones. The reason is negative: the module-level
# `random` functions share a global, seedable state, so a `random.seed()`
# anywhere in the process could quietly make the pacing reproducible and
# correlated. This cannot be seeded, so that failure mode does not exist.
_rng = secrets.SystemRandom()


def request_gap(delay: float) -> float:
    """How long to wait before one request.

    Usually a value from a band around ``delay`` — relative, not
    absolute, so the band stays sensible whether the delay is 1 second or
    30. Rarely, a value from a much longer range *instead*: adding the
    two would change the result by a few percent and cost a reader the
    ability to state the rule in one sentence.

    A delay of zero means no pacing at all — not a band around zero, and
    not a long pause that might still fire. Zero is how the tests run and
    how an operator says they know what they are doing; a mechanism that
    occasionally sleeps 40 seconds despite being switched off would be a
    surprise in both cases.
    """
    if delay <= 0:
        return 0.0
    if _rng.random() < settings.pacing_long_pause_chance:
        return _rng.uniform(
            settings.pacing_long_pause_min, settings.pacing_long_pause_max
        )
    jitter = settings.pacing_jitter
    return _rng.uniform(delay * (1 - jitter), delay * (1 + jitter))


def channel_gap() -> float:
    """How long to wait between finishing one channel and starting the next."""
    return _rng.uniform(
        settings.backfill_channel_pause_min,
        settings.backfill_channel_pause_max,
    )


async def pace(delay: float) -> None:
    """Wait before a request. Every request in ``tg/`` is preceded by this.

    A gap of zero is not slept — not even ``sleep(0)``. Pacing switched
    off should leave no trace, including in whatever is watching.
    """
    gap = request_gap(delay)
    if gap > 0:
        await asyncio.sleep(gap)


async def pause_between_channels() -> None:
    """Wait between two channels, before the second one's first request."""
    await asyncio.sleep(channel_gap())
