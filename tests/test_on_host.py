"""`scripts/on-host.sh` against a stub ssh: nothing leaves the machine.

The stub runs the remote command locally, with a fake `k3s` on the PATH
that records its arguments — so what is checked is what the host would
actually receive, quoting and all.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "on-host.sh"

# Marks the start of each invocation, so the two kubectl calls of a
# `--loop` form stay two calls. An `exec` reads standard input, which is
# what `-i` passes through.
FAKE_K3S = """#!/usr/bin/env bash
echo '---' >> "$HOME/k3s-args.txt"
for arg in "$@"; do printf '[%s]\\n' "$arg"; done >> "$HOME/k3s-args.txt"
case " $* " in *" exec "*) cat > "$HOME/stdin.txt" ;; esac
exit "${FAKE_EXIT:-0}"
"""

FAKE_SSH = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$HOME/ssh-args.txt"
exec bash -c "${@: -1}"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    _executable(bin_dir / "k3s", FAKE_K3S)
    _executable(bin_dir / "ssh", FAKE_SSH)
    inventory = tmp_path / "inventory.ini"
    inventory.write_text(
        "# the collection host\n[collector]\n"
        "itgraph ansible_host=host.invalid ansible_user=root\n",
        encoding="utf-8",
    )
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "ITGRAPH_INVENTORY": str(inventory),
    }


def _run(
    env: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(env: dict[str, str]) -> list[list[str]]:
    """The recorded invocations, as argument lists."""
    calls: list[list[str]] = []
    current: list[str] = []
    for line in (Path(env["HOME"]) / "k3s-args.txt").read_text().splitlines():
        if line == "---":
            if current:
                calls.append(current)
            current = []
        else:
            current.append(line[1:-1])
    calls.append(current)
    return calls


def test_arguments_arrive_intact(env: dict[str, str]) -> None:
    result = _run(env, "yt", "mark", "@fake", "--note", 'it\'s "two" words')

    assert result.returncode == 0, result.stderr
    assert _calls(env) == [
        [
            "kubectl",
            "-n",
            "itgraph",
            "exec",
            "-i",
            "deploy/itgraph-console",
            "-c",
            "itgraph",
            "--",
            "itgraph",
            "yt",
            "mark",
            "@fake",
            "--note",
            'it\'s "two" words',
        ]
    ]


def test_it_connects_to_the_inventory_host_without_a_terminal(
    env: dict[str, str],
) -> None:
    _run(env, "yt", "channels")

    args = (Path(env["HOME"]) / "ssh-args.txt").read_text().splitlines()
    assert args[:2] == ["-T", "root@host.invalid"]


def test_standard_input_reaches_the_command(env: dict[str, str]) -> None:
    review = "# itgraph yt review-export\nchannel_id\tdecision\n"

    _run(env, "yt", "review-apply", "-", stdin=review)

    assert (Path(env["HOME"]) / "stdin.txt").read_text() == review


def test_failure_is_reported_as_failure(env: dict[str, str]) -> None:
    result = _run({**env, "FAKE_EXIT": "3"}, "yt", "refresh")

    assert result.returncode == 3


def test_loop_stop_scales_to_zero_and_waits_for_the_pod(
    env: dict[str, str],
) -> None:
    result = _run(env, "--loop", "stop")

    assert result.returncode == 0, result.stderr
    calls = _calls(env)
    assert calls[0] == [
        "kubectl",
        "-n",
        "itgraph",
        "scale",
        "deploy/itgraph-watch",
        "--to=0",
    ]
    assert calls[1][:2] == ["kubectl", "-n"]
    assert calls[1][3:8] == [
        "wait",
        "--for=delete",
        "pod",
        "-l",
        "app=itgraph-watch",
    ]


def test_loop_start_scales_to_one_and_waits_for_the_rollout(
    env: dict[str, str],
) -> None:
    result = _run(env, "--loop", "start")

    assert result.returncode == 0, result.stderr
    calls = _calls(env)
    assert calls[0] == [
        "kubectl",
        "-n",
        "itgraph",
        "scale",
        "deploy/itgraph-watch",
        "--to=1",
    ]
    assert calls[1][3:6] == ["rollout", "status", "deploy/itgraph-watch"]


def test_an_unknown_loop_argument_is_a_usage_error(
    env: dict[str, str],
) -> None:
    result = _run(env, "--loop", "toggle")

    assert result.returncode == 64
    assert "stop|start" in result.stderr
    assert not (Path(env["HOME"]) / "ssh-args.txt").exists()


def test_no_inventory_fails_without_connecting(
    env: dict[str, str], tmp_path: Path
) -> None:
    result = _run(
        {**env, "ITGRAPH_INVENTORY": str(tmp_path / "missing.ini")},
        "yt",
        "channels",
    )

    assert result.returncode == 1
    assert "inventory.ini.example" in result.stderr
    assert not (Path(env["HOME"]) / "ssh-args.txt").exists()


def test_an_inventory_without_the_host_fails(
    env: dict[str, str], tmp_path: Path
) -> None:
    empty = tmp_path / "empty.ini"
    empty.write_text("[collector]\n", encoding="utf-8")

    result = _run({**env, "ITGRAPH_INVENTORY": str(empty)}, "yt", "channels")

    assert result.returncode == 1
    assert "ansible_host" in result.stderr
