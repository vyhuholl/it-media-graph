"""The exclusive claim on one Telethon session file.

Until the watch loop existed, every command was a one-shot that could
assume it alone held the session, and that assumption was true because
nothing ran long enough to overlap. A daemon breaks it. Two processes on
one session file is not a race that one of them loses — it is a corrupted
SQLite session and, at worst, an authorization Telegram decides to revoke
because the same session key is being used from two places at once.

The lock is a Postgres advisory lock, and each of its properties is doing
work here:

- **Session-scoped**, so a killed process releases it when its connection
  dies. There is no PID file to go stale and nothing to clean up by hand
  after an unclean exit, which is the failure mode a lockfile is famous
  for.
- **Non-blocking** (``pg_try_advisory_lock``), so a second command
  refuses immediately and says who holds it, rather than hanging on a
  lock the operator did not know existed.
- **In the database**, not on the filesystem. ``flock`` would also
  survive a kill, but it is local to one machine, and the processes in
  this project will not all stay on one machine.

Keyed by the *session file*, not globally: two different session files
are two different resources and must not block each other, while two
commands on one file must.

The holder is not recorded in a table. Postgres already knows which
backend holds an advisory lock, and a backend's ``application_name`` is
writable — so the holder writes its identity there and the refusing
process reads it out of ``pg_stat_activity``. Nothing to migrate, nothing
to clean up, and no way for the record to disagree with the lock.
"""

import hashlib
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from itgraph.config import settings

__all__ = [
    "LeaseLostError",
    "SessionBusyError",
    "SessionLease",
    "lease_ids",
    "session_lease",
]

logger = logging.getLogger(__name__)

# Postgres truncates `application_name` at 63 bytes. Everything the
# refusal message wants to say has to fit.
MAX_APPLICATION_NAME = 63


class SessionBusyError(RuntimeError):
    """Another process holds the session lease."""


class LeaseLostError(RuntimeError):
    """The lease could no longer be confirmed.

    Raised at the point of checking, never swallowed. A long-running
    holder that cannot confirm its lease has to stop: the alternative is
    carrying on using a session file that something else may now also be
    using, which is the single outcome this module exists to prevent.
    """


def _key_half(data: bytes) -> int:
    """Four bytes as a **non-negative** 31-bit integer.

    ``pg_advisory_lock`` takes either one ``bigint`` or two ``int4``. The
    two-argument form is used here because both halves are then ours to
    choose, and the ``pg_locks`` row can be matched on exactly rather
    than by unpacking a bigint into the halves Postgres split it into.

    The top bit is dropped for a reason worth stating, because it is a
    genuine trap: the lock functions take *signed* ``int4``, while the
    ``pg_locks`` columns that report the same key are ``oid``, which is
    *unsigned*. A negative key is therefore taken happily and then
    reappears in the catalog as its two's-complement — so the lock works
    and every attempt to look up who holds it fails on a range error.
    Staying inside 31 bits makes the two representations identical, which
    is cheaper than converting at each of the two query sites and
    impossible to forget at a third.
    """
    return int.from_bytes(data[:4], "big", signed=False) & 0x7FFFFFFF


def lease_ids(session_path: Path | str) -> tuple[int, int]:
    """The ``(classid, objid)`` pair identifying one session file's lease.

    The class half is a constant standing for "an itgraph session lease",
    so this project's locks cannot collide with any other advisory lock
    taken against the same database. The object half is derived from the
    resolved path, so two session files are two locks.

    Derived by hash rather than assigned by hand because the path is what
    identifies the resource, and a hash of it needs no registry to stay
    consistent between processes that never talk to each other.
    """
    resolved = str(Path(session_path).expanduser().resolve())
    return (
        _key_half(hashlib.blake2b(b"itgraph.session-lease").digest()),
        _key_half(hashlib.blake2b(resolved.encode()).digest()),
    )


def _identity(command: str) -> str:
    """How this process names itself to whoever is refused."""
    name = f"itgraph {command} pid={os.getpid()}@{socket.gethostname()}"
    return name[:MAX_APPLICATION_NAME]


class SessionLease:
    """One process's exclusive claim on the session file.

    Owns a connection of its own and keeps it for the process's lifetime.
    That is not an optimization to avoid — it is the requirement. An
    advisory lock belongs to the database session that took it, and a
    pooled connection returned between statements takes the lock with it.
    A lease held on a borrowed connection would be released silently, at
    a moment nothing observes, leaving two writers on one session file:
    the one bug in this design that could cost the account.
    """

    def __init__(
        self,
        command: str,
        *,
        url: str | None = None,
        session_path: Path | str | None = None,
    ) -> None:
        self._command = command
        self._url = url or str(settings.database_url)
        self._path = (
            session_path
            if session_path is not None
            else settings.telegram_session
        )
        self._classid, self._objid = lease_ids(self._path)
        # `NullPool` states structurally what this class requires: the
        # connection below is this process's own, not one borrowed from a
        # pool that could hand it back while the lock is still wanted.
        self._engine = create_async_engine(self._url, poolclass=NullPool)
        self._connection: AsyncConnection | None = None

    async def _holder(self, connection: AsyncConnection) -> str | None:
        """Who holds this lease, as they named themselves.

        Read only when acquisition has already failed, which means the
        lock *is* held — so whatever backend appears here is the current
        holder and not a leftover. That is why no separate record is
        needed: the catalog cannot go stale in the direction that would
        mislead.
        """
        row = (
            await connection.execute(
                text(
                    "SELECT activity.application_name, activity.backend_start "
                    "FROM pg_locks lock "
                    "JOIN pg_stat_activity activity ON activity.pid = lock.pid "
                    "WHERE lock.locktype = 'advisory' "
                    "AND lock.classid = :classid AND lock.objid = :objid "
                    "AND lock.objsubid = 2 AND lock.granted"
                ),
                {"classid": self._classid, "objid": self._objid},
            )
        ).first()
        if row is None:
            return None
        name, since = row
        return f"{name or 'an unnamed process'} (since {since:%Y-%m-%d %H:%M})"

    async def acquire(self) -> None:
        """Take the lease, or raise naming who has it.

        Never waits. A held lease means another collector is running, and
        the operator needs to be told that in a sentence rather than left
        looking at a command that has stopped producing output.
        """
        connection = await self._engine.connect()
        try:
            # Named before the lock is taken, so the identity is already
            # in place for anyone the next attempt refuses.
            await connection.execute(
                text("SELECT set_config('application_name', :name, false)"),
                {"name": _identity(self._command)},
            )
            taken = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:classid, :objid)"),
                {"classid": self._classid, "objid": self._objid},
            )
            if not taken:
                holder = await self._holder(connection)
                raise SessionBusyError(
                    f"the Telegram session {self._path} is in "
                    f"use by {holder or 'another process'}.\n"
                    "One process at a time may hold it — two would corrupt "
                    "the session file. Stop the running command (or the "
                    "`itgraph watch` loop) and try again."
                )
            # A session-level advisory lock outlives the transaction that
            # took it — unlike `pg_advisory_xact_lock`, which is exactly
            # why that one is not used here. Committing therefore keeps
            # the lock while leaving the connection merely idle rather
            # than idle-in-transaction, which a server-side timeout would
            # eventually kill.
            await connection.commit()
        except BaseException:
            await connection.close()
            raise
        self._connection = connection
        logger.debug(
            "session lease taken (%d, %d)", self._classid, self._objid
        )

    async def verify(self) -> None:
        """Confirm the lease still exists, or raise.

        A long-running holder calls this periodically. If the dedicated
        connection has dropped, the lock went with it and something else
        may already hold it — so this raises rather than reconnecting.
        Reconnecting and assuming the lease survived is precisely the
        behaviour that would put two writers on one session file.
        """
        if self._connection is None:
            raise LeaseLostError("the session lease was never taken")
        try:
            held = await self._connection.scalar(
                text(
                    "SELECT count(*) > 0 FROM pg_locks "
                    "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                    "AND classid = :classid AND objid = :objid "
                    "AND objsubid = 2 AND granted"
                ),
                {"classid": self._classid, "objid": self._objid},
            )
        except Exception as exc:
            raise LeaseLostError(
                "lost the connection holding the Telegram session lease; "
                "stopping rather than continuing to use the session"
            ) from exc
        if not held:
            raise LeaseLostError(
                "the Telegram session lease is no longer held; stopping "
                "rather than continuing to use the session"
            )

    async def release(self) -> None:
        """Give up the lease and the connection it lives on.

        Closing the connection would release the lock on its own; the
        explicit unlock is here so that a release during a process's life
        is visible as an intention rather than as a side effect of
        cleanup.
        """
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:classid, :objid)"),
                    {"classid": self._classid, "objid": self._objid},
                )
            except Exception:
                # Deliberately everything. The connection is being closed
                # either way, and closing it releases the lock — so a
                # failure here changes no outcome, and letting it
                # propagate would turn a clean shutdown into a traceback.
                logger.warning(
                    "could not release the session lease explicitly; "
                    "closing the connection releases it anyway",
                    exc_info=True,
                )
            finally:
                await connection.close()
        await self._engine.dispose()


@asynccontextmanager
async def session_lease(
    command: str,
    *,
    url: str | None = None,
    session_path: Path | str | None = None,
) -> AsyncIterator[SessionLease]:
    """Hold the session lease for the duration of a command."""
    lease = SessionLease(command, url=url, session_path=session_path)
    await lease.acquire()
    try:
        yield lease
    finally:
        await lease.release()
