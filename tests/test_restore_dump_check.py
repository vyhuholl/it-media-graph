"""The restore's scripts that read a dump, run as the database's pod runs them.

`pg_restore --list` reads a dump's table of contents and stops. Through
`kubectl exec` the rest of a real dump, unread, stalled the stream until kubectl
gave up, and the check called the newest full dump not a dump. A full restore
can stop short of a pipe's end too: a dump of a neutralised copy left 150 kB of
343 unread, and the stream hung with the restore done inside the pod. So every
script reads its input to the end, and still answers as pg_restore did.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

ROLE = Path(__file__).parents[1] / "deploy" / "ansible" / "itgraph"
VARS = ROLE / "vars" / "main.yml"
RESTORE = ROLE / "tasks" / "restore.yml"

# Many times a pipe's buffer, as a dump is: written whole only if read whole.
DUMP_SIZE = 8 << 20


def restore_script(task: str, variable: str) -> str:
    """A script of the restore's tasks, with the names it is rendered with."""
    for play in yaml.safe_load(RESTORE.read_text()):
        for found in play.get("block", []):
            if found.get("name") == task:
                script: str = found["vars"][variable]
                return script.replace(
                    "{{ itgraph_db_user }}", "itgraph"
                ).replace("{{ itgraph_db_name }}", "itgraph")
    raise AssertionError(f"no task {task!r} in the restore")


SCRIPTS = {
    "check": yaml.safe_load(VARS.read_text())["__itgraph_list_dump"],
    "replace": restore_script(
        "Replace the data with the dump's", "__itgraph_replace"
    ),
    "count": restore_script(
        "Compare every table's rows with the dump's", "__itgraph_count"
    ),
}


def check(
    tmp_path: Path, pg_restore_rc: int, script: str = SCRIPTS["check"]
) -> tuple[bool, int]:
    """Run a role's script on a large input, with a pg_restore that reads its start alone."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "pg_restore"
    fake.write_text(
        f"#!/bin/sh\nhead -c 4096 >/dev/null\nexit {pg_restore_rc}\n"
    )
    fake.chmod(0o755)
    for tool in ("dropdb", "createdb", "psql"):
        stub = bin_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", "-euo", "pipefail", "-c", script],
        stdin=subprocess.PIPE,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert proc.stdin is not None
    try:
        proc.stdin.write(b"\0" * DUMP_SIZE)
        proc.stdin.close()
        written = True
    except BrokenPipeError:
        written = False
    return written, proc.wait(timeout=30)


def test_a_dump_is_read_to_its_end(tmp_path: Path) -> None:
    written, rc = check(tmp_path, pg_restore_rc=0)

    assert written, "the check left the rest of the dump unread"
    assert rc == 0


@pytest.mark.parametrize("pg_restore_rc", [1, 2])
def test_what_is_no_dump_still_fails_as_pg_restore_said(
    tmp_path: Path, pg_restore_rc: int
) -> None:
    written, rc = check(tmp_path, pg_restore_rc=pg_restore_rc)

    assert written
    assert rc == pg_restore_rc


@pytest.mark.parametrize("name", ["replace", "count"])
def test_a_restore_reads_the_dump_to_its_end(
    tmp_path: Path, name: str
) -> None:
    written, rc = check(tmp_path, pg_restore_rc=0, script=SCRIPTS[name])

    assert written, f"the {name} step left the rest of the dump unread"
    assert rc == 0


@pytest.mark.parametrize("name", ["replace", "count"])
def test_a_failed_restore_still_fails_as_pg_restore_said(
    tmp_path: Path, name: str
) -> None:
    written, rc = check(tmp_path, pg_restore_rc=1, script=SCRIPTS[name])

    assert written
    assert rc == 1
