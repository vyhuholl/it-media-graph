"""Dumps, the schedule that decides which to take, and the pruning.

No Docker and no Postgres: ``subprocess.run`` is replaced, so what is
under test is the decision-making, not pg_dump.
"""

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from itgraph.db import backup as backup_module
from itgraph.db.backup import (
    STAMP_FORMAT,
    BackupError,
    due_kinds,
    existing,
    full_kind,
    inventory_kind,
    is_due,
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
