"""Dumps of the working database, and the pruning that keeps them bounded.

Two kinds, because the two halves of this database are worth different
amounts. The inventory is a few hundred rows of manual review that no
amount of re-collecting reconstructs, so it is dumped often and kept
deep. The raw layer is large and, at the cost of hours and of load on the
account, re-fetchable — so full dumps are rarer and fewer are kept.

Every dump is read back with ``pg_restore --list`` before it counts. A
file that cannot be restored is not a backup, and the failure has to
surface when it is written rather than when it is needed.

Each tier also keeps a ``…-LATEST.dump`` symlink to its newest verified
dump, so that a restore — or the deploy playbook, which ships one of
these to a new host — names a stable path instead of the operator
picking a timestamped file by eye. Picking by eye is not a cosmetic
risk: choosing yesterday's dump for a migration silently loses a day of
metric snapshots, and snapshots are the one thing here that cannot be
re-collected.
"""

import logging
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from itgraph.config import settings

__all__ = [
    "Backup",
    "BackupError",
    "BackupKind",
    "due_kinds",
    "is_due",
    "latest_link",
    "prune",
    "run_backup",
    "take_backup",
]

logger = logging.getLogger(__name__)

# Everything the operator decided by hand. `backfill_state` rides along
# because losing the cursors means walking every channel's history again
# — the one operation this project treats as expensive.
INVENTORY_TABLES = ("channels", "backfill_state")

# UTC, and said so in the name: a dump taken at 13:08 local is stamped
# 10:08, and without the Z that reads as a machine with the wrong clock.
# UTC also spares the ordering an hour that repeats every autumn.
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# What the stable pointer is called, per tier: `full-LATEST.dump`,
# `inventory-LATEST.dump`. Not a timestamp, deliberately — the whole
# point is a name that does not change.
LATEST = "LATEST"


class BackupError(RuntimeError):
    """A dump could not be taken, or could not be read back."""


@dataclass(frozen=True, slots=True)
class BackupKind:
    """One tier of backup: what it covers, how often, how many stay."""

    name: str
    subdirectory: str
    tables: tuple[str, ...] | None  # None means the whole database
    keep: int
    interval: timedelta


def inventory_kind() -> BackupKind:
    return BackupKind(
        name="inventory",
        subdirectory="daily",
        tables=INVENTORY_TABLES,
        keep=settings.backup_keep_inventory,
        # Under a day on purpose: at exactly 24h a run that starts a
        # few minutes early skips, and the daily rhythm drifts later
        # until it falls off the end of the day.
        interval=timedelta(hours=settings.backup_inventory_interval_hours),
    )


def full_kind() -> BackupKind:
    return BackupKind(
        name="full",
        subdirectory="weekly",
        tables=None,
        keep=settings.backup_keep_full,
        interval=timedelta(days=settings.backup_full_interval_days),
    )


def _docker(*args: str) -> list[str]:
    return [str(settings.docker_binary), *args]


def _exec_in_postgres(*args: str) -> list[str]:
    # -i so the container's stdin is connected; pg_restore reads the
    # archive from it when verifying.
    return _docker("exec", "-i", settings.postgres_container, *args)


def existing(directory: Path, kind: BackupKind) -> list[Path]:
    """Dumps of this kind, newest first.

    A file whose name carries no readable timestamp is ignored rather
    than allowed to raise: this directory belongs to the operator, and
    something they dropped in it must not stop the next backup.

    Symlinks are skipped before that check and without a word, because
    the ``LATEST`` pointer is one and matches the glob. Warning about it
    on every run would be noise that teaches the operator to skim past
    this log line — which is the line that reports the genuinely odd
    file this function exists to tolerate.
    """
    if not directory.is_dir():
        return []
    dated = []
    for path in directory.glob(f"{kind.name}-*.dump"):
        if path.is_symlink():
            continue
        moment = _timestamp_of(path)
        if moment is None:
            logger.warning("ignoring %s: no timestamp in the name", path.name)
            continue
        dated.append((moment, path))
    return [path for _, path in sorted(dated, reverse=True)]


def is_due(
    root: Path, kind: BackupKind, *, now: datetime | None = None
) -> bool:
    """Whether this tier is older than its interval.

    Asks the files on disk rather than the clock, so the schedule is a
    floor and not an appointment: a laptop asleep at the scheduled moment
    catches up on its next run instead of missing the window. That is
    also what makes it safe to run this often.
    """
    newest = existing(root / kind.subdirectory, kind)
    if not newest:
        return True
    taken_at = _timestamp_of(newest[0])
    if taken_at is None:  # pragma: no cover - `existing` filtered these
        return True
    return (now or datetime.now(UTC)) - taken_at >= kind.interval


def due_kinds(root: Path, *, now: datetime | None = None) -> list[BackupKind]:
    """Which tiers this run should take, cheapest first."""
    return [
        kind
        for kind in (inventory_kind(), full_kind())
        if is_due(root, kind, now=now)
    ]


def _timestamp_of(path: Path) -> datetime | None:
    """The moment encoded in a dump's name, or ``None`` if there is none."""
    _, _, stamp = path.stem.partition("-")
    try:
        return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _pg_dump_command(kind: BackupKind) -> list[str]:
    url = make_url(str(settings.database_url))
    args = [
        "pg_dump",
        "--username",
        url.username or "postgres",
        "--dbname",
        url.database or "postgres",
        "--format=custom",
    ]
    for table in kind.tables or ():
        args += ["--table", table]
    return _exec_in_postgres(*args)


def _verify(path: Path) -> int:
    """Read the archive back. Returns its entry count."""
    with path.open("rb") as archive:
        result = subprocess.run(
            _exec_in_postgres("pg_restore", "--list"),
            stdin=archive,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise BackupError(
            f"{path.name} is not a readable archive: {result.stderr.strip()}"
        )
    entries = [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith(";")
    ]
    if not entries:
        raise BackupError(
            f"{path.name} restored to an empty table of contents"
        )
    return len(entries)


@dataclass(frozen=True, slots=True)
class Backup:
    """A dump that exists on disk and has been read back."""

    path: Path
    kind: str
    size: int
    entries: int


def take_backup(
    kind: BackupKind, root: Path, *, now: datetime | None = None
) -> Backup:
    """Write one dump and verify it. Raises ``BackupError`` on failure."""
    directory = root / kind.subdirectory
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime(STAMP_FORMAT)
    path = directory / f"{kind.name}-{stamp}.dump"

    logger.info("dumping %s to %s", kind.name, path)

    # Dump to a scratch name and rename only once the archive has been
    # read back. Two things follow, and both matter: a failed or killed
    # run never leaves a truncated file that later looks like a backup,
    # and the cleanup can only ever delete a file this call created —
    # writing straight to `path` would delete an existing dump of the
    # same second on the way out.
    handle, scratch_name = tempfile.mkstemp(
        dir=directory, prefix=f".{kind.name}-{stamp}.", suffix=".partial"
    )
    scratch = Path(scratch_name)
    try:
        with os.fdopen(handle, "wb") as target:
            result = subprocess.run(
                _pg_dump_command(kind),
                stdout=target,
                stderr=subprocess.PIPE,
                text=False,
                check=False,
            )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise BackupError(f"pg_dump failed: {detail}")

        entries = _verify(scratch)
        size = scratch.stat().st_size
        scratch.replace(path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise

    # Only now, past the verification: a pointer to an archive that does
    # not restore would be worse than no pointer at all.
    _point_latest_at(path, kind)

    logger.info("%s: %d bytes, %d entries", path.name, size, entries)
    return Backup(path=path, kind=kind.name, size=size, entries=entries)


def latest_link(directory: Path, kind: BackupKind) -> Path:
    """Where this tier's stable pointer lives."""
    return directory / f"{kind.name}-{LATEST}.dump"


def _point_latest_at(path: Path, kind: BackupKind) -> None:
    """Repoint this tier's ``LATEST`` at a dump that has been verified.

    **Relative, never absolute.** This directory gets copied and rsynced
    — that is what it is for — and an absolute target breaks the moment
    it is read from another path or another machine.

    A reader that *sends* this file elsewhere has to resolve the link
    itself. ``rsync -a`` copies a symlink as a symlink and the copy then
    dangles at a name that exists only here; the deploy playbook passes
    ``copy_links`` for exactly this reason.

    Replaced through a temporary name so the pointer is never briefly
    absent: ``symlink_to`` refuses an existing path, and unlinking first
    would leave a window in which a restore finds nothing.

    A failure here does **not** fail the backup: the dump is written and
    verified, and losing a convenience pointer must not turn a good
    backup into a missing one. But the stale link is removed rather than
    left, because absent makes the next restore stop and say so, while
    stale makes it quietly ship last week's data.
    """
    link = latest_link(path.parent, kind)
    scratch = path.parent / f".{kind.name}-{LATEST}.dump.new"
    try:
        scratch.unlink(missing_ok=True)
        scratch.symlink_to(path.name)
        scratch.replace(link)
    except OSError as exc:
        scratch.unlink(missing_ok=True)
        link.unlink(missing_ok=True)
        logger.warning(
            "could not point %s at %s (%s); the dump itself is fine, "
            "but a restore will have to name the file",
            link.name,
            path.name,
            exc,
        )


def prune(root: Path, kind: BackupKind) -> list[Path]:
    """Delete all but the newest ``kind.keep`` dumps. Returns what went."""
    surplus = existing(root / kind.subdirectory, kind)[kind.keep :]
    for path in surplus:
        logger.info("pruning %s", path.name)
        path.unlink(missing_ok=True)
    return surplus


def run_backup(
    root: Path | None = None,
    *,
    kinds: Sequence[BackupKind] | None = None,
    now: datetime | None = None,
) -> list[Backup]:
    """Take every due backup, then prune. Pruning follows a good dump only.

    An empty ``kinds`` never happens by accident: the caller either lets
    ``due_kinds`` decide or names the tier explicitly.
    """
    root = root or settings.backup_dir
    due = list(kinds) if kinds is not None else due_kinds(root, now=now)

    taken = []
    for kind in due:
        backup = take_backup(kind, root, now=now)
        taken.append(backup)
        prune(root, kind)
    return taken
