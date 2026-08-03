"""The exclusive claim on the session file, against a real Postgres.

The one module in the suite that opts out of the `no_session_lease`
fixture: everywhere else the lease is neutralized so tests do not reach
for the working database, and here it is the subject.

Every lease is given an explicit `url` and `session_path` — the first so
it lands on the throwaway database, the second so a test's key cannot
collide with a real one.
"""

from pathlib import Path

import pytest

from itgraph.db.session_lease import (
    LeaseLostError,
    SessionBusyError,
    SessionLease,
    lease_ids,
    session_lease,
)


@pytest.fixture(autouse=True)
def no_session_lease() -> None:
    """Override conftest's neutralizer. Here the real lease is the point."""


def test_the_key_is_stable_for_one_path() -> None:
    assert lease_ids("/tmp/a.session") == lease_ids("/tmp/a.session")


def test_two_session_files_are_two_locks() -> None:
    """Different files must not block each other.

    Two sessions is not the failure this guards against — two processes
    on *one* session is. A shared key would refuse a legitimate second
    account for no reason.
    """
    assert lease_ids("/tmp/a.session") != lease_ids("/tmp/b.session")


def test_a_relative_and_an_absolute_path_are_one_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same file named two ways is the same resource.

    A lease keyed on the string as typed would let `itgraph.session` and
    `./itgraph.session` be held at once, which is the exact situation the
    lock exists to make impossible.
    """
    monkeypatch.chdir(tmp_path)
    assert lease_ids("itgraph.session") == lease_ids(
        tmp_path / "itgraph.session"
    )


async def test_a_second_lease_is_refused(
    database_url: str, tmp_path: Path
) -> None:
    session = tmp_path / "itgraph.session"

    async with session_lease("watch", url=database_url, session_path=session):
        second = SessionLease(
            "backfill", url=database_url, session_path=session
        )
        with pytest.raises(SessionBusyError) as caught:
            await second.acquire()

    # The refusal names the holder, so the operator knows what to stop.
    assert "watch" in str(caught.value)


async def test_the_refusal_names_the_command_not_just_the_fact(
    database_url: str, tmp_path: Path
) -> None:
    session = tmp_path / "itgraph.session"

    async with session_lease(
        "metadata", url=database_url, session_path=session
    ):
        second = SessionLease("add", url=database_url, session_path=session)
        with pytest.raises(SessionBusyError) as caught:
            await second.acquire()

    message = str(caught.value)
    assert "metadata" in message
    assert "pid=" in message


async def test_a_different_session_file_is_not_blocked(
    database_url: str, tmp_path: Path
) -> None:
    # Both held at once: a second account's session is a second resource
    # entirely, and must not be refused because the first is busy.
    async with (
        session_lease(
            "watch", url=database_url, session_path=tmp_path / "one.session"
        ),
        session_lease(
            "backfill", url=database_url, session_path=tmp_path / "two.session"
        ),
    ):
        pass


async def test_the_lease_is_released_on_exit(
    database_url: str, tmp_path: Path
) -> None:
    session = tmp_path / "itgraph.session"

    async with session_lease("watch", url=database_url, session_path=session):
        pass

    # Taking it again must simply work — no cleanup, no stale record.
    async with session_lease(
        "backfill", url=database_url, session_path=session
    ):
        pass


async def test_the_lease_is_released_when_the_body_raises(
    database_url: str, tmp_path: Path
) -> None:
    session = tmp_path / "itgraph.session"

    with pytest.raises(RuntimeError):
        async with session_lease(
            "watch", url=database_url, session_path=session
        ):
            raise RuntimeError("the command failed")

    async with session_lease(
        "backfill", url=database_url, session_path=session
    ):
        pass


async def test_a_held_lease_verifies(
    database_url: str, tmp_path: Path
) -> None:
    async with session_lease(
        "watch", url=database_url, session_path=tmp_path / "itgraph.session"
    ) as lease:
        await lease.verify()


async def test_verifying_an_unheld_lease_raises(
    database_url: str, tmp_path: Path
) -> None:
    """A holder that cannot confirm its lease has to stop.

    Reconnecting and assuming it survived is the behaviour that would put
    two writers on one session file, so the check raises rather than
    repairing.
    """
    lease = SessionLease(
        "watch", url=database_url, session_path=tmp_path / "itgraph.session"
    )
    with pytest.raises(LeaseLostError):
        await lease.verify()


async def test_a_dropped_connection_frees_the_lease(
    database_url: str, tmp_path: Path
) -> None:
    """No PID file, nothing to clean up — the point of an advisory lock.

    A killed process is simulated by dropping the connection without ever
    calling `pg_advisory_unlock`, which is what an unclean exit does. The
    lock must go with the connection: that is the whole reason this is an
    advisory lock and not a lockfile, whose equivalent test would need a
    stale-PID rule to pass.
    """
    session = tmp_path / "itgraph.session"
    abandoned = SessionLease("watch", url=database_url, session_path=session)
    await abandoned.acquire()
    assert abandoned._connection is not None
    await abandoned._connection.close()

    async with session_lease(
        "backfill", url=database_url, session_path=session
    ):
        pass
