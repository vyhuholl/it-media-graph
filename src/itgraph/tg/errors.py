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

__all__ = ["NotAuthorizedError"]


class NotAuthorizedError(RuntimeError):
    """The session holds no authorized user."""
