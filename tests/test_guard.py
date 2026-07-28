"""The barrier that refuses a downgrade against the working database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from itgraph.db.guard import (
    ALLOW_DESTRUCTIVE,
    DestructiveMigrationError,
    check_downgrade_allowed,
    is_scratch_database,
)

REPO_ROOT = Path(__file__).parent.parent

WORKING = "postgresql+asyncpg://itgraph:itgraph@localhost:5433/itgraph"
SCRATCH = "postgresql+asyncpg://itgraph:itgraph@localhost:5433/itgraph_test"

# Deliberately not a database that exists: if the barrier ever fails
# open, the command behind it must still have nothing to destroy.
PROBE = (
    "postgresql+asyncpg://itgraph:itgraph@localhost:5433/itgraph_no_such_db"
)


def test_scratch_database_is_recognised_by_suffix() -> None:
    assert is_scratch_database(SCRATCH)
    assert not is_scratch_database(WORKING)


def test_a_scratch_database_may_be_downgraded() -> None:
    check_downgrade_allowed(SCRATCH, env={})


def test_the_working_database_may_not() -> None:
    with pytest.raises(DestructiveMigrationError) as excinfo:
        check_downgrade_allowed(WORKING, env={})

    message = str(excinfo.value)
    # The refusal has to say which database and how to proceed, or it
    # just gets worked around.
    assert "itgraph" in message
    assert ALLOW_DESTRUCTIVE in message


def test_the_opt_in_permits_it() -> None:
    check_downgrade_allowed(WORKING, env={ALLOW_DESTRUCTIVE: "1"})


def test_a_merely_present_opt_in_is_not_enough() -> None:
    # Anything other than an explicit "1" — an empty value left in a
    # shell, say — must not read as consent.
    for value in ("", "0", "yes", "true"):
        with pytest.raises(DestructiveMigrationError):
            check_downgrade_allowed(WORKING, env={ALLOW_DESTRUCTIVE: value})


def _alembic(*args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    """Run the real Alembic CLI, so env.py's wiring is exercised."""
    env = {
        **os.environ,
        "TELEGRAM_API_ID": "0",
        "TELEGRAM_API_HASH": "test-api-hash",
        **overrides,
    }
    env.pop(ALLOW_DESTRUCTIVE, None)
    env.update({k: v for k, v in overrides.items()})
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_cli_refuses_a_downgrade_of_a_non_scratch_database() -> None:
    """End to end, through Alembic's own command dispatch.

    This is what catches a future Alembic renaming the function `env.py`
    reads the direction from: the barrier would silently stop applying,
    and only a test that runs the real command would notice.
    """
    result = _alembic("downgrade", "base", DATABASE_URL=PROBE)

    assert result.returncode != 0
    assert "refusing to downgrade" in result.stderr + result.stdout


def test_the_cli_lets_an_upgrade_through() -> None:
    # Same non-existent database: the barrier must not fire, so the
    # command gets far enough to fail on the connection instead.
    result = _alembic("upgrade", "head", DATABASE_URL=PROBE)

    assert "refusing to downgrade" not in result.stderr + result.stdout


def test_the_cli_lets_a_scratch_downgrade_through() -> None:
    result = _alembic(
        "downgrade",
        "base",
        DATABASE_URL="postgresql+asyncpg://itgraph:itgraph"
        "@localhost:5433/itgraph_no_such_db_test",
    )

    assert "refusing to downgrade" not in result.stderr + result.stdout
