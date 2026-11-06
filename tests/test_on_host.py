"""`scripts/on-host.sh` against a stub ssh: nothing leaves the machine.

The stub runs the remote command locally, with a fake `sudo` and `k3s` on the
PATH that record their arguments — so what is checked is what the host would
actually receive, quoting and all.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "on-host.sh"

# Marks the start of each invocation, so that separate kubectl calls stay
# separate. An `exec` reads standard input, which is what `-i` passes through.
FAKE_K3S = """#!/usr/bin/env bash
echo '---' >> "$HOME/k3s-args.txt"
for arg in "$@"; do printf '[%s]\\n' "$arg"; done >> "$HOME/k3s-args.txt"
case " $* " in
  *" exec "*)
    cat > "$HOME/stdin.txt"
    printf 'the result\\n'
    printf 'a diagnostic\\n' >&2
    exit "${FAKE_EXIT:-0}"
    ;;
  *" create "*)
    cat > "$HOME/created.txt"
    if [ -n "${FAKE_CREATE_FAILS:-}" ]; then exit 1; fi
    printf 'job.batch/itgraph-console-fake\\n'
    exit 0
    ;;
  *" get configmap "*) printf 'the-manifest'; exit 0 ;;
  *" wait "*) exit "${FAKE_WAIT_EXIT:-0}" ;;
  *" get "*) printf '%s' "${FAKE_PODS:-}"; exit 0 ;;
esac
exit 0
"""

FAKE_SUDO = """#!/usr/bin/env bash
printf '%s\\n' "$1" >> "$HOME/sudo-flags.txt"
shift
exec "$@"
"""

FAKE_SSH = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$HOME/ssh-args.txt"
exec bash -c "${@: -1}"
"""

# A wait that never succeeds would otherwise sleep through thirty tries.
FAKE_SLEEP = """#!/usr/bin/env bash
exit 0
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
    _executable(bin_dir / "sudo", FAKE_SUDO)
    _executable(bin_dir / "ssh", FAKE_SSH)
    _executable(bin_dir / "sleep", FAKE_SLEEP)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "ITGRAPH_SSH": "someone@host.invalid",
    }


def run(
    env: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def calls_of(env: dict[str, str]) -> list[list[str]]:
    """Each kubectl call, as the arguments the host received."""
    text = (Path(env["HOME"]) / "k3s-args.txt").read_text(encoding="utf-8")
    calls = []
    for block in text.split("---\n"):
        if block.strip():
            calls.append([line[1:-1] for line in block.splitlines()])
    return calls


def exec_call(env: dict[str, str]) -> list[str]:
    [call] = [c for c in calls_of(env) if "exec" in c]
    return call


def test_arguments_arrive_intact(env: dict[str, str]) -> None:
    done = run(env, "yt", "mark", "--note", "it's a 'note' with spaces")

    assert done.returncode == 0, done.stderr
    call = exec_call(env)
    assert call[call.index("--") + 1 :] == [
        "itgraph",
        "yt",
        "mark",
        "--note",
        "it's a 'note' with spaces",
    ]


def test_it_runs_as_the_operator_through_sudo_and_without_a_terminal(
    env: dict[str, str],
) -> None:
    run(env, "yt", "channels")

    args = (Path(env["HOME"]) / "ssh-args.txt").read_text().splitlines()
    assert args[0] == "-T"
    assert args[1] == "someone@host.invalid"
    flags = (Path(env["HOME"]) / "sudo-flags.txt").read_text().splitlines()
    assert set(flags) == {"-n"}


def test_the_job_comes_from_what_the_deploy_left_and_goes_afterwards(
    env: dict[str, str],
) -> None:
    done = run(env, "yt", "channels")

    assert done.returncode == 0, done.stderr
    calls = calls_of(env)
    assert any("jsonpath={.data.plain\\.json}" in call for call in calls)
    created = (Path(env["HOME"]) / "created.txt").read_text()
    assert created == "the-manifest"
    deleted = [call for call in calls if "delete" in call]
    assert len(deleted) == 1
    assert "job.batch/itgraph-console-fake" in deleted[0]
    # Created, waited for, used and deleted, in that order.
    order = [
        next(
            word
            for word in ("create", "wait", "exec", "delete")
            if word in call
        )
        for call in calls
        if {"create", "wait", "exec", "delete"} & set(call)
    ]
    assert order == ["create", "wait", "exec", "delete"]


def test_a_failed_command_still_deletes_its_job(env: dict[str, str]) -> None:
    done = run({**env, "FAKE_EXIT": "3"}, "yt", "channels")

    assert done.returncode == 3
    assert [call for call in calls_of(env) if "delete" in call]


def test_standard_input_reaches_the_command(env: dict[str, str]) -> None:
    run(env, "yt", "review-apply", "-", stdin="a whole\nreview file\n")

    received = (Path(env["HOME"]) / "stdin.txt").read_text()
    assert received == "a whole\nreview file\n"
    assert "-i" in exec_call(env)


def test_output_and_errors_stay_apart(env: dict[str, str]) -> None:
    done = run(env, "yt", "channels")

    assert done.stdout == "the result\n"
    assert done.stderr == "a diagnostic\n"


def test_failure_is_reported_as_failure(env: dict[str, str]) -> None:
    done = run({**env, "FAKE_EXIT": "3"}, "yt", "channels")

    assert done.returncode == 3


@pytest.mark.parametrize(
    ("flag", "variant"),
    [("--session", "session"), ("--backups", "backups")],
)
def test_a_flag_picks_what_the_job_holds(
    env: dict[str, str], flag: str, variant: str
) -> None:
    done = run(env, flag, "resolve")

    assert done.returncode == 0, done.stderr
    calls = calls_of(env)
    assert any(f"jsonpath={{.data.{variant}\\.json}}" in c for c in calls)
    call = exec_call(env)
    assert call[call.index("--") + 1 :] == ["itgraph", "resolve"]


def test_a_flag_alone_is_a_usage_error(env: dict[str, str]) -> None:
    done = run(env, "--session")

    assert done.returncode == 64
    assert "usage" in done.stderr


def test_a_pod_that_never_starts_fails_saying_so_and_goes(
    env: dict[str, str],
) -> None:
    done = run({**env, "FAKE_WAIT_EXIT": "1"}, "yt", "channels")

    assert done.returncode == 1
    assert "did not start" in done.stderr
    assert [call for call in calls_of(env) if "delete" in call]
    assert not [call for call in calls_of(env) if "exec" in call]


def test_a_job_that_cannot_be_made_runs_nothing(env: dict[str, str]) -> None:
    done = run({**env, "FAKE_CREATE_FAILS": "1"}, "yt", "channels")

    assert done.returncode != 0
    assert not [call for call in calls_of(env) if "exec" in call]


def test_the_namespace_can_be_named(env: dict[str, str]) -> None:
    run({**env, "ITGRAPH_NAMESPACE": "other"}, "yt", "channels")

    for call in calls_of(env):
        assert call[:3] == ["kubectl", "-n", "other"]


def test_loop_stop_scales_to_zero_and_waits_for_the_pod(
    env: dict[str, str],
) -> None:
    done = run(env, "--loop", "stop")

    assert done.returncode == 0, done.stderr
    calls = calls_of(env)
    assert "scale" in calls[0]
    assert "--replicas=0" in calls[0]
    assert "deploy/itgraph-watch" in calls[0]
    assert "wait" in calls[1]
    assert "--for=delete" in calls[1]


def test_loop_stop_on_a_stopped_loop_succeeds(env: dict[str, str]) -> None:
    # `wait` by label fails when nothing matches; no pod left means stopped.
    done = run(
        {**env, "FAKE_WAIT_EXIT": "1", "FAKE_PODS": ""}, "--loop", "stop"
    )

    assert done.returncode == 0, done.stderr


def test_loop_stop_fails_while_the_pod_remains(env: dict[str, str]) -> None:
    done = run(
        {**env, "FAKE_WAIT_EXIT": "1", "FAKE_PODS": "pod/itgraph-watch-x"},
        "--loop",
        "stop",
    )

    assert done.returncode != 0


def test_loop_start_scales_to_one_and_waits_for_the_rollout(
    env: dict[str, str],
) -> None:
    done = run(env, "--loop", "start")

    assert done.returncode == 0, done.stderr
    calls = calls_of(env)
    assert "--replicas=1" in calls[0]
    assert "rollout" in calls[1]


def test_an_unknown_loop_argument_is_a_usage_error(
    env: dict[str, str],
) -> None:
    done = run(env, "--loop", "sideways")

    assert done.returncode == 64
    assert "usage" in done.stderr


def test_no_arguments_is_a_usage_error(env: dict[str, str]) -> None:
    done = run(env)

    assert done.returncode == 64


def test_no_destination_fails_without_connecting(
    env: dict[str, str],
) -> None:
    without = {k: v for k, v in env.items() if k != "ITGRAPH_SSH"}

    done = run(without, "yt", "channels")

    assert done.returncode == 1
    assert "ITGRAPH_SSH" in done.stderr
    assert not (Path(env["HOME"]) / "ssh-args.txt").exists()
