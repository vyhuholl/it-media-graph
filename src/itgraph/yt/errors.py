"""YouTube failures a caller catches, without importing the HTTP client.

The same arrangement as ``tg/errors.py``, for the same reason: ``cli.py``
names these in the wrapper every YouTube command runs through, and the
offline commands — review, listing, the quota report — should not have to
import the networked path to be able to say why something failed.

A spent quota is not here. It is the ledger's to decide, and lives with
it in ``db/yt_quota.py``.
"""

__all__ = [
    "ApiError",
    "ListingFailed",
    "MissingKeyError",
    "RequestLimitReached",
    "YtError",
]


class YtError(RuntimeError):
    """A YouTube command cannot go on. The message is the operator's."""


class MissingKeyError(YtError):
    """No API key is configured, so nothing can be asked of YouTube."""

    def __init__(self) -> None:
        super().__init__(
            "no YouTube API key: set YOUTUBE_API_KEY in .env "
            "(youtube_api_key in the vault, on the host)"
        )


class RequestLimitReached(YtError):
    """The command made as many requests as ``--limit`` allowed.

    Not a failure: the run stops where it was asked to, and the same
    input continues it.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"stopped at --limit {limit}; run again with the same input "
            "to continue"
        )
        self.limit = limit


class ApiError(YtError):
    """YouTube answered something the command cannot work with.

    Carries what YouTube said, so the message names the status and the
    reason rather than only that it failed. The key never appears here:
    the client scrubs it from anything YouTube echoes back.
    """

    def __init__(
        self, status: int | None, reason: str | None, detail: str
    ) -> None:
        said = f"{status} {reason}" if reason else f"{status}"
        super().__init__(
            f"YouTube answered {said}: {detail}"
            if status is not None
            else f"could not reach YouTube: {detail}"
        )
        self.status = status
        self.reason = reason


class ListingFailed(YtError):
    """One channel's uploads could not be listed.

    The channel's failure, not the pass's: a playlist answered not found
    or not accessible says something about that channel and nothing
    about the next one, so the poll loop records it and goes on.
    """

    def __init__(self, playlist_id: str, said: str) -> None:
        super().__init__(f"could not list {playlist_id}: {said}")
        self.said = said
