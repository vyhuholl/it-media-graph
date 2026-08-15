"""Collection failures that a caller catches, without importing Telethon.

A module of its own for one reason, and it is not tidiness. ``cli.py``
catches ``NotAuthorizedError`` in the wrapper every command runs through,
so importing it from ``tg/client.py`` pulled Telethon into commands that
never touch the network — and Telethon announces itself on import, so
`itgraph derive` and `itgraph channels` printed a line about encryption
libraries while doing nothing but read Postgres.

That is a small thing to look at and a bad thing to leave: this project's
clearest promise is that some passes go nowhere near Telegram, and a log
line saying otherwise makes the promise unverifiable by the cheapest
method anyone has, which is reading the output.

So anything a non-networked caller has to name lives here. Errors that
only a networked path can raise or catch — ``FloodWaitTooLong``,
``PeerNotCached`` — stay where they are: they are Telethon's world by
construction, and moving them would buy nothing.
"""

__all__ = ["NotAuthorizedError", "WatchStalled"]


class NotAuthorizedError(RuntimeError):
    """The session holds no authorized user."""


class WatchStalled(RuntimeError):
    """The loop had work to do and did none of it for too long.

    Raised so the process exits and a supervisor starts a fresh one.
    That is the whole design: it does not know *why* the loop stopped
    making progress, and deliberately does not try to — the failure it
    was written for was a request that Telethon accepted, never sent and
    never failed, which no specific check would have anticipated.

    Distinct from every other way the loop stops. A rate limit postpones
    the schedule and the loop keeps running; a lost lease is fatal
    because something else may hold the session. This one means the loop
    is still holding everything it should and using none of it.
    """
