"""The barrier between a destructive migration and the working database.

`alembic downgrade` drops tables, and nothing in the Alembic CLI knows
which database it is pointed at — the URL comes from the environment, so
the command that verifies a migration on a scratch database and the one
that destroys months of manual review are keystroke-identical.

This applies the rule ``conftest.py`` already applies to the database it
creates: a name ending in ``_test`` is disposable, anything else is not
and needs the operator to say so out loud.
"""

import os
from collections.abc import Mapping

from sqlalchemy.engine import make_url

__all__ = [
    "ALLOW_DESTRUCTIVE",
    "SKIP_BACKUP",
    "DestructiveMigrationError",
    "check_downgrade_allowed",
    "is_scratch_database",
]

ALLOW_DESTRUCTIVE = "ITGRAPH_ALLOW_DESTRUCTIVE"

# Lets an upgrade proceed without the dump that normally precedes it.
# Named for what it costs, not for what it saves.
SKIP_BACKUP = "ITGRAPH_SKIP_BACKUP"

# The same suffix the test fixture requires. Kept identical on purpose:
# two different definitions of "safe to wipe" is one too many.
SCRATCH_SUFFIX = "_test"


class DestructiveMigrationError(RuntimeError):
    """A downgrade was aimed at a database that is not disposable."""


def is_scratch_database(url: str) -> bool:
    """Whether the URL names a throwaway database."""
    return (make_url(url).database or "").endswith(SCRATCH_SUFFIX)


def check_downgrade_allowed(
    url: str, *, env: Mapping[str, str] | None = None
) -> None:
    """Raise unless this downgrade is allowed to run against ``url``.

    Permitted when the target is a scratch database, or when the operator
    has set the opt-in variable for this one command. Anything else
    raises before a connection is opened, so nothing is dropped.
    """
    if is_scratch_database(url):
        return

    environ = os.environ if env is None else env
    if environ.get(ALLOW_DESTRUCTIVE) == "1":
        return

    name = make_url(url).database or "<unnamed>"
    raise DestructiveMigrationError(
        f"refusing to downgrade {name!r}: it is not a scratch database "
        f"(a disposable one ends in {SCRATCH_SUFFIX!r}).\n"
        "A downgrade drops tables — the inventory included. To verify a "
        "migration, point DATABASE_URL at a scratch database:\n"
        f"    DATABASE_URL=...{SCRATCH_SUFFIX} uv run alembic downgrade base\n"
        "To preview without touching anything, use offline mode:\n"
        "    uv run alembic downgrade --sql head:base\n"
        f"If you really mean to downgrade {name!r}, back it up first and "
        f"then set {ALLOW_DESTRUCTIVE}=1 for that one command."
    )
