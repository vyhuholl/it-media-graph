"""The lookup pass over YouTube's own references.

Reads the mention graph as the payloads stand (``db/yt_references.py``),
takes the handles, legacy names and videos that seeds' videos reference
and that no stored payload and no lookup in date answers, and asks
YouTube which channel each belongs to — most distinct seeds first, so a
run bounded by ``--limit`` spends on what matters most. A channel id
needs no lookup, and nothing a featured list names is looked up: a
featured list names channels by id.

The answers go to the same ``yt_raw_lookups`` as Telegram's, so a name
either source asked about costs nothing to the other. The asking, its
spending and its logging are ``yt/lookup_pass.py``'s.
"""

from itgraph.db.session import Database
from itgraph.db.yt_references import read_mention_queue
from itgraph.yt.client import YouTube
from itgraph.yt.lookup_pass import LookupSummary, look_up

__all__ = ["run_mention_lookups"]


async def run_mention_lookups(
    youtube: YouTube, database: Database
) -> LookupSummary:
    """Look up every reference awaiting one. Never raises for YouTube."""
    async with database.session() as session:
        _, naming = await read_mention_queue(session)
    return await look_up(youtube, database, naming.to_look_up())
