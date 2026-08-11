"""Where the scripts in this directory get their database connection.

Exploratory tooling, like everything else here: not part of the package,
not spec'd, not tested. It exists because six scripts otherwise repeat
the same eight lines, and the last time they did, they repeated a bug.

Two conversions, and each is a thing that has already gone wrong once.

``DATABASE_URL`` is written for SQLAlchemy, so its scheme names the
driver: ``postgresql+asyncpg://``. psycopg does not parse that at all —
it reads the whole string as a keyword-value connection string and fails
on the missing ``=``. Stripping the driver suffix is the whole fix, and
it belongs here rather than in each caller.

``.env`` is not the environment. Nothing in `uv run` loads it, and the
package only sees it because pydantic-settings reads the file itself.
A script that imports no settings object gets an empty variable, so the
file is read explicitly — and ``override=False``, so a DSN exported in
the shell for a one-off query against a scratch database still wins.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["dsn"]

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def dsn() -> str:
    """The working database, in the form psycopg accepts."""
    load_dotenv(ENV_FILE, override=False)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            f"DATABASE_URL is not set and {ENV_FILE} does not supply it"
        )
    scheme, _, rest = url.partition("://")
    return f"{scheme.partition('+')[0]}://{rest}"
