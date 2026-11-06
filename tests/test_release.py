"""`deploy/release.sh` in a throwaway repository, against stubs of its tools.

A repository of its own, because comparing two archives byte for byte means
packing them for real. The stubs stand in for `docker` and `gh`, the registry
being a directory of digests. The `gh` stub refuses outside a repository, as the
real one does: a release published from a temporary directory failed exactly so.
"""

import hashlib
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "deploy" / "release.sh"
IMAGE = "ghcr.example/fake/it-media-graph"
DIGEST = "sha256:" + "1" * 64
OTHER_DIGEST = "sha256:" + "2" * 64
TAG = "v1.2.3"

FAKE_DOCKER = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$STUB_DIR/calls"
key() { printf '%s' "$1" | tr '/:' '__'; }
case "$*" in
  "buildx imagetools inspect "*)
    if [ -n "${IMAGE_APPEARS_AFTER:-}" ]; then
      count=$(cat "$STUB_DIR/inspects" 2>/dev/null || echo 0)
      count=$((count + 1))
      echo "$count" > "$STUB_DIR/inspects"
      if [ "$count" -le "$IMAGE_APPEARS_AFTER" ]; then exit 1; fi
    fi
    file="$STUB_DIR/registry/$(key "$4")"
    [ -f "$file" ] || { echo "ERROR: $4: not found" >&2; exit 1; }
    cat "$file"
    ;;
  "buildx imagetools create "*)
    from="$STUB_DIR/registry/$(key "$6")"
    if [ -n "${RETAG_DIGEST:-}" ]; then
      printf '%s\\n' "$RETAG_DIGEST" > "$STUB_DIR/registry/$(key "$5")"
    else
      cp "$from" "$STUB_DIR/registry/$(key "$5")"
    fi
    ;;
  *) echo "docker stub: unexpected: $*" >&2; exit 70 ;;
esac
"""

FAKE_GH = """#!/usr/bin/env bash
printf 'gh %s\\n' "$*" >> "$STUB_DIR/calls"
[ "$1 $2" = "release create" ] || { echo "unexpected: $*" >&2; exit 70; }
# The real gh finds the repository through git, and refuses outside one.
git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "failed to run git: not a git repository" >&2
  exit 1
}
mkdir -p "$STUB_DIR/release"
cp "$4" "$STUB_DIR/release/"
cp "$5" "$STUB_DIR/release/"
"""


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Fake Name",
            "GIT_AUTHOR_EMAIL": "fake@example.invalid",
            "GIT_COMMITTER_NAME": "Fake Name",
            "GIT_COMMITTER_EMAIL": "fake@example.invalid",
            "GIT_AUTHOR_DATE": "2026-11-06T09:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-11-06T09:00:00+00:00",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


class Stage:
    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        self.stubs = tmp_path / "stubs"
        (self.stubs / "registry").mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        executable(bin_dir / "docker", FAKE_DOCKER)
        executable(bin_dir / "gh", FAKE_GH)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_DIR": str(self.stubs),
            "RELEASE_IMAGE": IMAGE,
            "RELEASE_WAIT_TRIES": "3",
            "RELEASE_WAIT_SECONDS": "0",
            "TMPDIR": str(tmp_path),
        }
        role = self.repo / "deploy" / "ansible" / "itgraph" / "tasks"
        role.mkdir(parents=True)
        (role / "main.yml").write_text("---\n", encoding="utf-8")
        (self.repo / "deploy" / "ansible" / "render.yml").write_text(
            "---\n", encoding="utf-8"
        )
        git(self.repo, "init", "-q", "-b", "master")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "a commit")
        self.sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/master", "HEAD")
        git(self.repo, "tag", TAG)

    def publish_image(self, digest: str = DIGEST) -> None:
        name = f"{IMAGE}:{self.sha}".replace("/", "_").replace(":", "_")
        (self.stubs / "registry" / name).write_text(digest + "\n", "utf-8")

    def run(
        self, tag: str = TAG, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), tag],
            cwd=self.repo,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    @property
    def calls(self) -> str:
        path = self.stubs / "calls"
        return path.read_text("utf-8") if path.exists() else ""

    def released(self, name: str) -> Path:
        return self.stubs / "release" / name


@pytest.fixture
def stage(tmp_path: Path) -> Stage:
    return Stage(tmp_path)


def test_a_tag_on_master_publishes_the_role_and_retags_the_image(
    stage: Stage,
) -> None:
    stage.publish_image()

    done = stage.run()

    assert done.returncode == 0, done.stderr
    archive = stage.released("itgraph-ansible.tar.gz")
    with tarfile.open(archive) as packed:
        names = packed.getnames()
    assert "itgraph/tasks/main.yml" in names
    # One directory named after the role, and nothing beside it.
    assert all(name.startswith("itgraph") for name in names)
    sums = stage.released("SHA256SUMS").read_text()
    assert sums.split()[1] == "itgraph-ansible.tar.gz"
    assert sums.split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()
    retagged = f"{IMAGE}:{TAG}".replace("/", "_").replace(":", "_")
    assert (stage.stubs / "registry" / retagged).read_text().strip() == DIGEST
    assert f"--tag {IMAGE}:{TAG} {IMAGE}:{stage.sha}" in stage.calls
    # Copied by digest, never built.
    assert "build " not in stage.calls.replace("buildx", "")


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-rc1", "latest"])
def test_a_tag_of_any_other_form_is_refused(stage: Stage, tag: str) -> None:
    git(stage.repo, "tag", tag)
    stage.publish_image()

    done = stage.run(tag)

    assert done.returncode == 1
    assert "not a version tag" in done.stderr
    assert stage.calls == ""


def test_a_commit_off_master_is_refused(stage: Stage) -> None:
    git(stage.repo, "commit", "-q", "--allow-empty", "-m", "off master")
    git(stage.repo, "tag", "v2.0.0")
    stage.publish_image()

    done = stage.run("v2.0.0")

    assert done.returncode == 1
    assert "not on master" in done.stderr
    assert stage.calls == ""


def test_an_image_that_appears_late_is_waited_for(stage: Stage) -> None:
    stage.publish_image()

    done = stage.run(IMAGE_APPEARS_AFTER="2")

    assert done.returncode == 0, done.stderr
    assert stage.released("itgraph-ansible.tar.gz").exists()


def test_an_image_that_never_appears_publishes_nothing(stage: Stage) -> None:
    done = stage.run()

    assert done.returncode == 1
    assert "has no image" in done.stderr
    assert "gh " not in stage.calls
    assert "imagetools create" not in stage.calls


def test_a_moved_digest_publishes_nothing(stage: Stage) -> None:
    stage.publish_image()

    done = stage.run(RETAG_DIGEST=OTHER_DIGEST)

    assert done.returncode == 1
    assert "has the digest" in done.stderr
    assert "gh " not in stage.calls


def test_two_packs_of_one_commit_are_byte_identical(stage: Stage) -> None:
    stage.publish_image()
    assert stage.run().returncode == 0
    first = stage.released("itgraph-ansible.tar.gz").read_bytes()
    stage.released("itgraph-ansible.tar.gz").unlink()

    assert stage.run().returncode == 0

    assert stage.released("itgraph-ansible.tar.gz").read_bytes() == first


def test_it_publishes_from_the_repository(stage: Stage) -> None:
    """The stub refuses outside one, as the real `gh` does."""
    stage.publish_image()

    done = stage.run()

    assert done.returncode == 0, done.stderr
    assert "gh release create" in stage.calls


def test_the_stub_would_catch_publishing_from_elsewhere(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable(bin_dir / "gh", FAKE_GH)
    (tmp_path / "stubs").mkdir()

    done = subprocess.run(
        [str(bin_dir / "gh"), "release", "create", "v1.0.0", "a", "b"],
        cwd=tmp_path,
        env={
            **os.environ,
            "STUB_DIR": str(tmp_path / "stubs"),
            "GIT_DIR": str(tmp_path / "no-such-repository"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 1
    assert "not a git repository" in done.stderr


WORKFLOWS = REPO_ROOT / ".github" / "workflows"


@pytest.mark.parametrize("name", ["image.yml", "release.yml"])
def test_no_workflow_names_a_secret_but_its_own_token(name: str) -> None:
    text = (WORKFLOWS / name).read_text()
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}


@pytest.mark.parametrize("name", ["image.yml", "release.yml"])
def test_every_action_is_pinned_by_commit(name: str) -> None:
    text = (WORKFLOWS / name).read_text()
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action


def test_a_release_runs_on_a_version_tag_alone_and_only_after_the_tests() -> (
    None
):
    # `on` is a boolean to YAML.
    workflow = yaml.safe_load((WORKFLOWS / "release.yml").read_text())
    assert workflow[True] == {"push": {"tags": ["v*"]}}
    assert workflow["jobs"]["release"]["needs"] == "test"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["release"]["permissions"] == {
        "contents": "write",
        "packages": "write",
    }
    steps = workflow["jobs"]["test"]["steps"]
    assert any(step.get("run") == "make test" for step in steps)


def test_a_push_to_master_only_builds_the_image() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "image.yml").read_text())
    assert workflow[True] == {"push": {"branches": ["master"]}}
    steps = workflow["jobs"]["build"]["steps"]
    assert not any("make test" in step.get("run", "") for step in steps)
