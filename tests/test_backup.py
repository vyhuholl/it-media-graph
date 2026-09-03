"""Dumps, the schedule that decides which to take, and the pruning.

No Docker and no Postgres: ``subprocess.run`` is replaced, so what is
under test is the decision-making, not pg_dump.
"""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from itgraph.config import settings
from itgraph.db import backup as backup_module
from itgraph.db.backup import (
    STAMP_FORMAT,
    BackupError,
    due_kinds,
    existing,
    full_kind,
    inventory_kind,
    is_due,
    is_empty_database,
    latest_link,
    prune,
    run_backup,
    take_backup,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)

# pg_restore --list output: comment lines start with ';', entries do not.
LISTING = (
    "; Archive created at 2026-07-22\n2660; 0 16385 TABLE DATA channels\n"
)


def dump_at(root: Path, kind_name: str, subdir: str, when: datetime) -> Path:
    directory = root / subdir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind_name}-{when.strftime(STAMP_FORMAT)}.dump"
    path.write_bytes(b"PGDMP-pretend")
    return path


def test_a_stray_file_does_not_stop_the_backup(tmp_path: Path) -> None:
    """The backup directory belongs to the operator, not to this module."""
    good = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    stray = tmp_path / "daily" / "inventory-notes.dump"
    stray.write_bytes(b"whatever this is")

    assert existing(tmp_path / "daily", inventory_kind()) == [good]
    assert not is_due(tmp_path, inventory_kind(), now=NOW)
    assert stray.exists()


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record commands; write a plausible archive, list a plausible one."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        calls.append(command)
        if "pg_restore" in command:
            return subprocess.CompletedProcess(command, 0, LISTING, "")
        kwargs["stdout"].write(b"PGDMP-pretend")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(backup_module.subprocess, "run", run)
    return calls


def test_a_tier_with_no_dumps_is_due(tmp_path: Path) -> None:
    assert is_due(tmp_path, inventory_kind(), now=NOW)
    assert is_due(tmp_path, full_kind(), now=NOW)


def test_a_fresh_dump_is_not_due_again(tmp_path: Path) -> None:
    dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))

    assert not is_due(tmp_path, inventory_kind(), now=NOW)


def test_a_stale_dump_is_due(tmp_path: Path) -> None:
    dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=21))

    assert is_due(tmp_path, inventory_kind(), now=NOW)


def test_the_full_tier_runs_on_its_own_slower_clock(tmp_path: Path) -> None:
    # A day-old full dump is fresh; a day-old inventory dump is not.
    dump_at(tmp_path, "inventory", "daily", NOW - timedelta(days=1))
    dump_at(tmp_path, "full", "weekly", NOW - timedelta(days=1))

    names = [kind.name for kind in due_kinds(tmp_path, now=NOW)]
    assert names == ["inventory"]


def test_a_missed_week_is_caught_up_not_skipped(tmp_path: Path) -> None:
    """The schedule is a floor, not an appointment.

    A machine asleep on the appointed day must take the dump on its next
    run rather than wait for the day to come round again.
    """
    dump_at(tmp_path, "full", "weekly", NOW - timedelta(days=30))

    names = [kind.name for kind in due_kinds(tmp_path, now=NOW)]
    assert "full" in names


def test_pruning_keeps_the_newest(tmp_path: Path) -> None:
    kept_or_not = [
        dump_at(tmp_path, "full", "weekly", NOW - timedelta(days=days))
        for days in range(10)
    ]

    removed = prune(tmp_path, full_kind())

    survivors = sorted(p.name for p in (tmp_path / "weekly").iterdir())
    assert len(survivors) == full_kind().keep
    # The four newest are the four smallest day offsets.
    assert survivors == sorted(p.name for p in kept_or_not[: full_kind().keep])
    assert len(removed) == len(kept_or_not) - full_kind().keep


def test_the_inventory_dump_names_its_tables(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    take_backup(inventory_kind(), tmp_path, now=NOW)

    dump_command = fake_run[0]
    assert "--table" in dump_command
    assert "channels" in dump_command
    # The cursor table rides along; losing it costs a re-walk of history.
    assert "backfill_state" in dump_command


def test_the_full_dump_names_no_tables(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    take_backup(full_kind(), tmp_path, now=NOW)

    assert "--table" not in fake_run[0]


def test_a_dump_is_read_back_before_it_counts(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    result = take_backup(inventory_kind(), tmp_path, now=NOW)

    assert any("pg_restore" in command for command in fake_run)
    assert result.entries == 1
    assert result.path.exists()


def test_a_failed_dump_leaves_no_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written file would be pruned and counted as a real backup."""

    def failing(command: list[str], **kwargs: Any) -> Any:
        kwargs["stdout"].write(b"PGD")  # truncated
        return subprocess.CompletedProcess(command, 1, b"", b"disk full")

    monkeypatch.setattr(backup_module.subprocess, "run", failing)

    with pytest.raises(BackupError, match="disk full"):
        take_backup(inventory_kind(), tmp_path, now=NOW)

    assert list((tmp_path / "daily").iterdir()) == []


def test_a_failed_dump_does_not_destroy_an_existing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cleanup may only remove what this call created.

    Two dumps in the same second collide on the name. Writing straight to
    it and unlinking on failure would delete a good backup — a failed
    dump destroying a real one is the whole failure this module exists
    to prevent.
    """
    victim = dump_at(tmp_path, "inventory", "daily", NOW)
    original = victim.read_bytes()

    def failing(command: list[str], **kwargs: Any) -> Any:
        kwargs["stdout"].write(b"PGD")
        return subprocess.CompletedProcess(command, 1, b"", b"boom")

    monkeypatch.setattr(backup_module.subprocess, "run", failing)

    with pytest.raises(BackupError):
        take_backup(inventory_kind(), tmp_path, now=NOW)

    assert victim.exists()
    assert victim.read_bytes() == original
    # And nothing half-written was left lying about either.
    assert [p.name for p in (tmp_path / "daily").iterdir()] == [victim.name]


def test_an_unreadable_archive_is_not_a_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(command: list[str], **kwargs: Any) -> Any:
        if "pg_restore" in command:
            return subprocess.CompletedProcess(command, 1, "", "corrupt")
        kwargs["stdout"].write(b"garbage")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(backup_module.subprocess, "run", run)

    with pytest.raises(BackupError, match="not a readable archive"):
        take_backup(inventory_kind(), tmp_path, now=NOW)


def test_an_empty_archive_is_not_a_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dump of a database that should have tables and does not.

    Restorable and worthless: the archive reads back fine and contains
    nothing. `is_empty_database` is what separates this from a database
    that legitimately has nothing in it yet.
    """

    def run(command: list[str], **kwargs: Any) -> Any:
        if "pg_restore" in command:
            return subprocess.CompletedProcess(
                command, 0, "; Archive created at 2026-07-22\n", ""
            )
        kwargs["stdout"].write(b"PGDMP-pretend")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(backup_module.subprocess, "run", run)

    with pytest.raises(BackupError, match="empty table of contents"):
        take_backup(full_kind(), tmp_path, now=NOW)


def test_nothing_is_pruned_when_the_dump_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pruning follows a good dump, never a failed one.

    Otherwise a machine that cannot reach Postgres would delete its way
    through the backups it already has, one run at a time.
    """
    for days in range(10):
        dump_at(tmp_path, "full", "weekly", NOW - timedelta(days=days))

    def failing(command: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(
            command, 1, b"", b"no such container"
        )

    monkeypatch.setattr(backup_module.subprocess, "run", failing)

    with pytest.raises(BackupError):
        run_backup(tmp_path, kinds=[full_kind()], now=NOW)

    assert len(list((tmp_path / "weekly").iterdir())) == 10


# --- the LATEST pointer ---------------------------------------------


def test_a_dump_gets_a_stable_name_to_restore_from(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    """So a restore names a path instead of picking a file by eye."""
    backup = take_backup(full_kind(), tmp_path, now=NOW)

    link = latest_link(tmp_path / "weekly", full_kind())
    assert link.is_symlink()
    assert link.resolve() == backup.path.resolve()


def test_the_pointer_is_relative(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    """This directory is rsynced and copied — that is what it is for.

    An absolute target breaks the moment the backups are read from
    another path or another machine, which is exactly the moment a
    restore is happening.
    """
    take_backup(full_kind(), tmp_path, now=NOW)

    link = latest_link(tmp_path / "weekly", full_kind())
    assert not Path(os.readlink(link)).is_absolute()

    moved = tmp_path.parent / "carried-elsewhere"
    shutil.copytree(tmp_path, moved, symlinks=True)
    assert (moved / "weekly" / "full-LATEST.dump").exists()


def test_a_later_dump_repoints_it(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    take_backup(full_kind(), tmp_path, now=NOW)
    second = take_backup(full_kind(), tmp_path, now=NOW + timedelta(days=7))

    link = latest_link(tmp_path / "weekly", full_kind())
    assert link.resolve() == second.path.resolve()


def test_each_tier_gets_its_own_pointer(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    take_backup(inventory_kind(), tmp_path, now=NOW)
    take_backup(full_kind(), tmp_path, now=NOW)

    assert (tmp_path / "daily" / "inventory-LATEST.dump").is_symlink()
    assert (tmp_path / "weekly" / "full-LATEST.dump").is_symlink()


def test_the_pointer_is_not_a_dump(
    tmp_path: Path, fake_run: list[list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """It matches the glob, so it has to be excluded deliberately.

    Silently, too: a warning on every run is noise that teaches the
    operator to skim past the line that reports a genuinely odd file.
    """
    take_backup(full_kind(), tmp_path, now=NOW)

    with caplog.at_level("WARNING"):
        found = existing(tmp_path / "weekly", full_kind())

    assert len(found) == 1
    assert not found[0].is_symlink()
    assert "LATEST" not in caplog.text


def test_pruning_leaves_the_pointer_alone(
    tmp_path: Path, fake_run: list[list[str]]
) -> None:
    """It is not a dump, so it must not count toward `keep` either."""
    for week in range(6):
        take_backup(full_kind(), tmp_path, now=NOW + timedelta(days=7 * week))
    newest = take_backup(
        full_kind(), tmp_path, now=NOW + timedelta(days=7 * 6)
    )
    prune(tmp_path, full_kind())

    link = latest_link(tmp_path / "weekly", full_kind())
    assert link.is_symlink()
    assert link.resolve() == newest.path.resolve()
    assert len(existing(tmp_path / "weekly", full_kind())) == (
        full_kind().keep
    )


def test_a_failed_dump_does_not_move_the_pointer(
    tmp_path: Path, fake_run: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pointer to an archive that does not restore is worse than none."""
    good = take_backup(full_kind(), tmp_path, now=NOW)

    def failing(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(args, 1, b"", b"disk full")

    monkeypatch.setattr(backup_module.subprocess, "run", failing)
    with pytest.raises(BackupError):
        take_backup(full_kind(), tmp_path, now=NOW + timedelta(days=7))

    link = latest_link(tmp_path / "weekly", full_kind())
    assert link.resolve() == good.path.resolve()


def test_an_unwritable_pointer_does_not_lose_the_backup(
    tmp_path: Path, fake_run: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dump is verified and on disk; that is the backup.

    Failing the run because a convenience pointer could not be written
    would turn a cosmetic problem into a missing backup. The stale link
    goes, though — absent makes the next restore stop and say so, while
    stale makes it quietly ship last week's data.
    """
    take_backup(full_kind(), tmp_path, now=NOW)

    def refuse(self: Path, target: Any) -> None:
        raise OSError("symlinks not supported here")

    monkeypatch.setattr(Path, "symlink_to", refuse)
    second = take_backup(full_kind(), tmp_path, now=NOW + timedelta(days=7))

    assert second.path.exists()
    assert not latest_link(tmp_path / "weekly", full_kind()).exists()


# --- a database with nothing in it yet --------------------------------


def counting(answer: str, code: int = 0) -> Any:
    """A psql that answers the table count with ``answer``."""

    def run(command: list[str], **kwargs: Any) -> Any:
        assert "psql" in command
        return subprocess.CompletedProcess(command, code, answer, "")

    return run


def test_a_database_without_tables_is_recognised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_module.subprocess, "run", counting("0\n"))

    assert is_empty_database()


def test_one_table_is_enough_to_need_a_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_module.subprocess, "run", counting("1\n"))

    assert not is_empty_database()


def test_an_unanswerable_question_does_not_skip_the_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container down, database missing, psql absent — all the same.

    Answering "empty" here would skip the dump on a guess, which is how
    the one backup that mattered goes missing. Answering "not empty"
    sends the caller to pg_dump, which fails with the real reason.
    """
    monkeypatch.setattr(backup_module.subprocess, "run", counting("", code=2))

    assert not is_empty_database()


def test_the_count_asks_the_database_the_url_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same database pg_dump would have dumped, not a default."""
    seen: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "0\n", "")

    monkeypatch.setattr(backup_module.subprocess, "run", run)
    is_empty_database()

    name = make_url(str(settings.database_url)).database
    assert seen[0][seen[0].index("--dbname") + 1] == name
