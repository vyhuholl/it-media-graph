"""Running the deploy role against `fake_kubectl`, and rendering it.

No network and no cluster. Every value the role is given comes from the
synthetic sets under `deploy/ansible/inputs/`.
"""

import json
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent
ANSIBLE_DIR = REPO_ROOT / "deploy" / "ansible"
INPUTS = ANSIBLE_DIR / "inputs"
FAKE_KUBECTL = Path(__file__).parent / "fake_kubectl.py"
BIN = Path(sys.executable).parent


def ansible_environment(home: Path) -> dict[str, str]:
    return {
        **os.environ,
        "ANSIBLE_ROLES_PATH": str(ANSIBLE_DIR),
        "ANSIBLE_HOME": str(home / "ansible-home"),
        "ANSIBLE_LOCAL_TEMP": str(home / "ansible-tmp"),
        "ANSIBLE_REMOTE_TEMP": str(home / "ansible-remote"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_PYTHON_INTERPRETER": sys.executable,
        "ANSIBLE_LOCALHOST_WARNING": "False",
        "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False",
        "ANSIBLE_DEPRECATION_WARNINGS": "False",
        "ANSIBLE_PIPELINING": "True",
    }


def render_set(
    name: str, out: Path, *, extra: Mapping[str, Any] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Every stream of one input set, as parsed objects."""
    out.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [
            str(BIN / "ansible-playbook"),
            str(ANSIBLE_DIR / "render.yml"),
            "-e",
            f"inputs={INPUTS / (name + '.yml')}",
            "-e",
            f"out={out}",
            "-e",
            json.dumps(extra or {}),
        ],
        cwd=out,
        env=ansible_environment(out),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return {
        path.stem: [
            document
            for document in yaml.safe_load_all(path.read_text("utf-8"))
            if document
        ]
        for path in sorted(out.glob("*.yaml"))
    }


@dataclass(slots=True)
class Run:
    """What one run of the role did."""

    returncode: int
    output: str
    calls: list[dict[str, Any]]

    @property
    def failed(self) -> bool:
        return self.returncode != 0

    def called(self, *words: str) -> list[dict[str, Any]]:
        """The calls whose arguments hold every one of these, in any place."""
        return [
            call
            for call in self.calls
            if all(word in call["args"] for word in words)
        ]

    def joined(self) -> list[str]:
        return [" ".join(call["args"]) for call in self.calls]

    def changing(self) -> list[str]:
        """The calls that would change a cluster."""
        return [
            " ".join(call["args"])
            for call in self.calls
            if set(call["args"])
            & {
                "apply",
                "create",
                "delete",
                "patch",
                "scale",
                "annotate",
                "label",
            }
        ]


def rule(
    match: str,
    stdout: str = "",
    *,
    rc: int = 0,
    stderr: str = "",
    times: int | None = None,
) -> dict[str, Any]:
    made: dict[str, Any] = {
        "match": match,
        "stdout": stdout,
        "rc": rc,
        "stderr": stderr,
    }
    if times is not None:
        made["times"] = times
    return made


def run_role(
    tmp_path: Path,
    tasks_from: str = "main",
    *,
    inputs: str = "a",
    rules: Iterable[Mapping[str, Any]] = (),
    extra: Mapping[str, Any] | None = None,
    arguments: Sequence[str] = (),
) -> Run:
    """Run one of the role's task files against the stub."""
    state = tmp_path / "kubectl"
    state.mkdir(parents=True, exist_ok=True)
    (state / "rules.json").write_text(json.dumps(list(rules)), "utf-8")
    (state / "calls.jsonl").write_text("", "utf-8")
    (state / "counts.json").unlink(missing_ok=True)

    playbook = tmp_path / "play.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Run the role against the stub",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars_files": [
                        (
                            inputs
                            if inputs.startswith("/")
                            else str(INPUTS / inputs)
                        )
                        + ".yml"
                    ],
                    "tasks": [
                        {
                            "name": "Run it",
                            "ansible.builtin.include_role": {
                                "name": "itgraph",
                                "tasks_from": tasks_from,
                            },
                        }
                    ],
                }
            ]
        ),
        "utf-8",
    )
    extra_vars = {
        "itgraph_kubectl": [sys.executable, str(FAKE_KUBECTL)],
        "__itgraph_settle_seconds": 0,
        "__itgraph_shot_poll": 0,
        "__itgraph_lock_poll": 0,
        "__itgraph_jobs_delay": 0,
        "__itgraph_stop_delay": 0,
        "__itgraph_route_delay": 0,
        "__itgraph_polls_delay": 0,
        **(extra or {}),
    }
    done = subprocess.run(
        [
            str(BIN / "ansible-playbook"),
            str(playbook),
            "-e",
            json.dumps(extra_vars),
            *arguments,
        ],
        cwd=tmp_path,
        env={
            **ansible_environment(tmp_path),
            "FAKE_KUBECTL_DIR": str(state),
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    calls = [
        json.loads(line)
        for line in (state / "calls.jsonl").read_text("utf-8").splitlines()
    ]
    return Run(done.returncode, done.stdout + done.stderr, calls)
