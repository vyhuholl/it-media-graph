"""The off-site pass: B2 over a mock transport, ``age`` mocked.

No network and no real ``age``: what is under test is the decision —
which dumps leave, what confirms a copy, and what a failure says. One
test at the bottom runs the real ``age`` where it is installed.
"""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from itgraph.config import settings
from itgraph.db import offsite as offsite_module
from itgraph.db.backup import STAMP_FORMAT, inventory_kind
from itgraph.db.offsite import OffsiteError, copy_new_dumps, remote_name

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

# Spelled out in words, like every fake credential in this suite: a real
# B2 application key is a long opaque string, and a fake that looked
# like one would cost gitleaks and a reviewer the ability to tell them
# apart.
APPLICATION_KEY = "test-b2-app-key"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The collection host: all four off-site settings in place."""
    monkeypatch.setattr(settings, "b2_key_id", "test-b2-key-id")
    monkeypatch.setattr(
        settings, "b2_application_key", SecretStr(APPLICATION_KEY)
    )
    monkeypatch.setattr(settings, "b2_bucket_name", "test-b2-bucket")
    monkeypatch.setattr(settings, "backup_age_recipient", "test-age-recipient")


@pytest.fixture
def fake_age(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic `age`: the plaintext, tagged, as the ciphertext."""

    def run(command: list[str], **kwargs: Any) -> Any:
        data = kwargs["stdin"].read()
        kwargs["stdout"].write(b"age-encrypted:" + data)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(offsite_module.subprocess, "run", run)


def dump_at(root: Path, kind_name: str, subdir: str, when: datetime) -> Path:
    directory = root / subdir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind_name}-{when.strftime(STAMP_FORMAT)}.dump"
    path.write_bytes(b"PGDMP-pretend")
    return path


class FakeB2:
    """Answers the four calls the pass makes, recording what arrives."""

    def __init__(
        self,
        *,
        remote: list[str] = (),
        upload_status: int = 200,
        upload_body: dict[str, Any] | None = None,
        confirm_checksum: bool = True,
    ) -> None:
        self.remote = sorted(remote)
        self.upload_status = upload_status
        self.upload_body = upload_body
        self.confirm_checksum = confirm_checksum
        # (name, body, checksum sent) per upload.
        self.uploads: list[tuple[str, bytes, str]] = []
        # (Content-Length, Transfer-Encoding) per upload, as sent.
        self.framing: list[tuple[str | None, str | None]] = []
        self.listings: list[str] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/b2api/v2/b2_authorize_account":
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(
                200,
                json={
                    "authorizationToken": "test-account-token",
                    "apiUrl": "https://api.test.invalid",
                    "allowed": {
                        "bucketId": "test-bucket-id",
                        "bucketName": "test-b2-bucket",
                        "capabilities": ["listFiles", "writeFiles"],
                        "namePrefix": None,
                    },
                },
            )
        if request.url.path == "/b2api/v2/b2_list_file_names":
            body: dict[str, Any] = json.loads(request.content)
            self.listings.append(body["prefix"])
            prefix = body["prefix"]
            start = body.get("startFileName")
            names = [name for name in self.remote if name.startswith(prefix)]
            if start is not None:
                names = [name for name in names if name >= start]
            limit = body["maxFileCount"]
            page, rest = names[:limit], names[limit:]
            return httpx.Response(
                200,
                json={
                    "files": [{"fileName": name} for name in page],
                    "nextFileName": rest[0] if rest else None,
                },
            )
        if request.url.path == "/b2api/v2/b2_get_upload_url":
            return httpx.Response(
                200,
                json={
                    "bucketId": "test-bucket-id",
                    "uploadUrl": (
                        "https://upload.test.invalid/b2api/v2/b2_upload_file"
                    ),
                    "authorizationToken": "test-upload-token",
                },
            )
        if request.url.path == "/b2api/v2/b2_upload_file":
            assert request.headers["Authorization"] == "test-upload-token"
            body_bytes = request.read()
            self.framing.append(
                (
                    request.headers.get("Content-Length"),
                    request.headers.get("Transfer-Encoding"),
                )
            )
            self.uploads.append(
                (
                    request.headers["X-Bz-File-Name"],
                    body_bytes,
                    request.headers["X-Bz-Content-Sha1"],
                )
            )
            if self.upload_status != 200:
                return httpx.Response(
                    self.upload_status, json=self.upload_body
                )
            confirmed = (
                hashlib.sha1(body_bytes).hexdigest()
                if self.confirm_checksum
                else "0" * 40
            )
            return httpx.Response(
                200,
                json={
                    "fileId": "test-file-id",
                    "fileName": request.headers["X-Bz-File-Name"],
                    "contentSha1": confirmed,
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")


def test_a_missing_copy_is_uploaded(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """The same dump, encrypted on the host before any byte leaves."""
    dump = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    b2 = FakeB2()

    copied = copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert copied == [dump]
    [(name, body, checksum)] = b2.uploads
    assert name == remote_name(inventory_kind(), dump)
    assert body == b"age-encrypted:PGDMP-pretend"
    assert checksum == hashlib.sha1(body).hexdigest()
    # And the ciphertext is not lying beside the dump it came from.
    assert [p.name for p in (tmp_path / "daily").iterdir()] == [dump.name]


def test_the_upload_is_streamed_with_its_length_stated(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """B2 wants a length; the body is never read whole to get one."""
    dump_at(tmp_path, "full", "weekly", NOW - timedelta(hours=2))
    b2 = FakeB2()

    copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    [(_, body, _)] = b2.uploads
    assert b2.framing == [(str(len(body)), None)]


def test_a_present_copy_is_not_uploaded_again(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    dump = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    b2 = FakeB2(remote=[remote_name(inventory_kind(), dump)])

    copied = copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert copied == []
    assert b2.uploads == []


def test_a_dump_past_the_window_is_not_uploaded(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """The window is what stops a re-upload loop.

    A local dump older than the store's retention would otherwise look
    missing on every run and be uploaded again for as long as it is
    kept locally.
    """
    dump_at(tmp_path, "full", "weekly", NOW - timedelta(days=8))
    b2 = FakeB2()

    copied = copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert copied == []
    assert b2.uploads == []


def test_listing_pages_until_the_end(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """A store holding more than one page is listed to its end."""
    b2 = FakeB2(remote=[f"inventory/pad-{i:05}.age" for i in range(1001)])

    copied = copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert copied == []
    assert b2.listings.count("inventory/") == 2


def test_an_unconfirmed_checksum_fails_naming_the_dump(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """A copy counts only once the store confirms it.

    `cap_exceeded` and a 5xx below fail the same way; what this one
    adds is that the store's answer can be wrong all by itself.
    """
    dump = dump_at(tmp_path, "full", "weekly", NOW - timedelta(hours=2))
    b2 = FakeB2(confirm_checksum=False)

    with pytest.raises(OffsiteError) as caught:
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert dump.name in str(caught.value)
    assert [p.name for p in (tmp_path / "weekly").iterdir()] == [dump.name]


def test_a_spending_cap_refusal_fails_naming_the_dump(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """The $0 caps answering, rather than anything being billed."""
    dump = dump_at(tmp_path, "full", "weekly", NOW - timedelta(hours=2))
    b2 = FakeB2(
        upload_status=403,
        upload_body={
            "status": 403,
            "code": "cap_exceeded",
            "message": "usage cap exceeded",
        },
    )

    with pytest.raises(OffsiteError) as caught:
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert dump.name in str(caught.value)
    assert "cap_exceeded" in str(caught.value)
    assert [p.name for p in (tmp_path / "weekly").iterdir()] == [dump.name]


def test_a_broken_store_fails_naming_the_dump(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    dump = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    b2 = FakeB2(
        upload_status=500,
        upload_body={
            "status": 500,
            "code": "internal_error",
            "message": "try again",
        },
    )

    with pytest.raises(OffsiteError) as caught:
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert dump.name in str(caught.value)
    assert [p.name for p in (tmp_path / "daily").iterdir()] == [dump.name]


def test_every_uncopied_dump_is_named(
    tmp_path: Path, configured: None, fake_age: None
) -> None:
    """One failure does not hide the next dump behind it."""
    first = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    second = dump_at(tmp_path, "full", "weekly", NOW - timedelta(hours=2))
    b2 = FakeB2(
        upload_status=403,
        upload_body={"status": 403, "code": "cap_exceeded", "message": ""},
    )

    with pytest.raises(OffsiteError) as caught:
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert first.name in str(caught.value)
    assert second.name in str(caught.value)


def test_a_failing_age_fails_naming_the_dump(
    tmp_path: Path, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is sent while the plaintext cannot even be encrypted."""
    dump = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    b2 = FakeB2()

    def failing(command: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(command, 1, b"", b"no such key")

    monkeypatch.setattr(offsite_module.subprocess, "run", failing)

    with pytest.raises(OffsiteError) as caught:
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert dump.name in str(caught.value)
    assert b2.uploads == []
    assert [p.name for p in (tmp_path / "daily").iterdir()] == [dump.name]


def test_a_missing_age_fails_naming_the_dump(
    tmp_path: Path, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An image without `age` says so, rather than a traceback."""
    dump = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    b2 = FakeB2()

    def missing(command: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory", "age")

    monkeypatch.setattr(offsite_module.subprocess, "run", missing)

    with pytest.raises(OffsiteError) as caught:
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert dump.name in str(caught.value)
    assert "age could not run" in str(caught.value)
    assert b2.uploads == []
    assert [p.name for p in (tmp_path / "daily").iterdir()] == [dump.name]


def test_the_logs_carry_names_never_keys(
    tmp_path: Path,
    configured: None,
    fake_age: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dump = dump_at(tmp_path, "inventory", "daily", NOW - timedelta(hours=2))
    b2 = FakeB2()

    with caplog.at_level("INFO"):
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert dump.name in caplog.text
    assert APPLICATION_KEY not in caplog.text


def test_an_unconfigured_host_makes_no_calls(
    tmp_path: Path, fake_age: None
) -> None:
    """The Mac: no off-site pass, and no request to explain."""
    b2 = FakeB2()

    with pytest.raises(OffsiteError, match="not configured"):
        copy_new_dumps(tmp_path, now=NOW, transport=b2.transport)

    assert b2.listings == []


# --- the real `age`, where it is installed ---------------------------

AGE = shutil.which("age")
AGE_KEYGEN = shutil.which("age-keygen")


@pytest.mark.skipif(
    AGE is None or AGE_KEYGEN is None, reason="age is not installed"
)
def test_a_copy_decrypts_to_the_dump(tmp_path: Path) -> None:
    """The round trip a restore depends on, with the real tool.

    Runs where `age` is installed — the operator's Mac — the way the
    database tests run where Postgres is; skipped everywhere else. The
    pass encrypts from standard input to standard output, and so does
    this, with the same command shape.
    """
    keygen = subprocess.run(
        [str(AGE_KEYGEN), "-o", str(tmp_path / "identity")],
        capture_output=True,
        text=True,
        check=True,
    )
    # `age-keygen` writes the identity to the file and says the public
    # key on standard error.
    recipient = keygen.stderr.removeprefix("Public key: ").strip()
    payload = b"PGDMP-pretend payload"

    encrypted = subprocess.run(
        [str(AGE), "-r", recipient],
        input=payload,
        capture_output=True,
        check=True,
    )
    decrypted = subprocess.run(
        [str(AGE), "-d", "-i", str(tmp_path / "identity")],
        input=encrypted.stdout,
        capture_output=True,
        check=True,
    )

    assert encrypted.stdout != payload  # it really encrypted
    assert decrypted.stdout == payload
