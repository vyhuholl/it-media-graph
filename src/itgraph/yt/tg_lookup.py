"""The lookup pass: every name the Telegram seeds link that nothing answers.

Reads the seeds' links as they stand (``db/yt_telegram_links.py``),
takes the names that no stored payload and no lookup in date answers,
and asks YouTube about them, best evidenced first — most distinct seed
families linking a name — so that a run bounded by ``--limit`` spends
on what matters most. The asking itself, its spending and its logging
are ``yt/lookup_pass.py``'s, shared with ``yt mention-lookup``.

No Telegram session and no Telegram request: the posts are already in
``raw_messages``.
"""

from itgraph.db.session import Database
from itgraph.db.yt_telegram_links import read_telegram_links
from itgraph.derive.tg_youtube import to_look_up
from itgraph.yt.client import YouTube
from itgraph.yt.lookup_pass import LookupSummary, look_up

__all__ = ["LookupSummary", "run_lookups"]


async def run_lookups(youtube: YouTube, database: Database) -> LookupSummary:
    """Look up every name awaiting one. Never raises for YouTube.

    A limit, a spent quota or a failure ends the run with what it did,
    already committed, and is returned in the summary.
    """
    async with database.session() as session:
        links = await read_telegram_links(session)
    return await look_up(youtube, database, to_look_up(links.reading))
