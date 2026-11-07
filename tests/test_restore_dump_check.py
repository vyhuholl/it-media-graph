"""The restore's check that a file is a dump, run as the database's pod runs it.

`pg_restore --list` reads a dump's table of contents and stops. Through
`kubectl exec` the rest of a real dump, unread, stalled the stream until kubectl
gave up, and the check called the newest full dump not a dump. So the script
reads its input to the end, and still answers as pg_restore did.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

VARS = (
    Path(__file__).parents[1]
    / "deploy"
    / "ansible"
    / "itgraph"
    / "vars"
    / "main.yml"
)

# Many times a pipe's buffer, as a dump is: written whole only if read whole.
DUMP_SIZE = 8 << 20


def check(tmp_path: Path, pg_restore_rc: int) -> tuple[bool, int]:
    """Run the role's script on a large input, with a pg_restore that reads its start alone."""
    script = yaml.safe_load(VARS.read_text())["__itgraph_list_dump"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "pg_restore"
    fake.write_text(
        f"#!/bin/sh\nhead -c 4096 >/dev/null\nexit {pg_restore_rc}\n"
    )
    fake.chmod(0o755)

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
