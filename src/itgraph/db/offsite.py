"""Off-site copies of verified dumps: encrypted on the host, uploaded to
a store the host can add to but never delete from or read back.

Why the ``age`` CLI rather than a Python binding: it streams, where the
bindings hold the plaintext and the ciphertext in memory at once — twice
a 275 MB dump in a job with a memory limit — and it adds no dependency.
Why B2's native API over its S3 interface: three calls — authorize,
list, upload — against a store that checks a SHA-1 computed here, with
no SigV4 and no SDK.

The credential this module runs on has ``listFiles`` and ``writeFiles``
on the one bucket and nothing else, so the worst a taken host can do is
fill the store — and the account's caps are $0, so even that fails
rather than bills. The ``age`` recipient is public; the identity that
pairs with it lives only in the operator's password manager, which is
what makes the copies unreadable on the host that made them.

Names are ``<tier>/<dump name>.age`` and never reused: a new copy never
takes the place of an old one, and retention is the store's own
lifecycle rules, not anything run from here.
"""

import hashlib
import logging
import os
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from itgraph.config import settings
from itgraph.db.backup import (
    BackupKind,
    _timestamp_of,
    existing,
    full_kind,
    inventory_kind,
)

__all__ = [
    "CATCH_UP_WINDOW",
    "OffsiteError",
    "copy_new_dumps",
    "is_configured",
    "remote_name",
]

logger = logging.getLogger(__name__)

AUTH_URL = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"

# How long after a dump was taken it still counts as missing a copy.
# Deliberately shorter than the store's retention: a local dump older
# than what the store keeps would otherwise look missing on every run
# and be uploaded again for as long as it is kept locally.
CATCH_UP_WINDOW = timedelta(days=7)

TIMEOUT = httpx.Timeout(60.0)

# How much of a file is held at once, hashing or sending.
CHUNK = 1024 * 1024


class OffsiteError(RuntimeError):
    """A dump could not be copied off-site."""


@dataclass(frozen=True, slots=True)
class _Session:
    """What one authorized key gives: where to call, how, and where."""

    api_url: str
    token: str
    bucket_id: str


def is_configured() -> bool:
    """Whether the off-site pass is configured at all.

    None of the four settings is the operator's machine, where no pass
    runs; the config validator keeps the four all set or all unset, so
    this answer cannot be "half".
    """
    return (
        settings.b2_key_id is not None
        and settings.b2_application_key is not None
        and settings.b2_bucket_name is not None
        and settings.backup_age_recipient is not None
    )


def remote_name(kind: BackupKind, path: Path) -> str:
    """The dump's name in the store: tier, dump name, ciphertext."""
    return f"{kind.name}/{path.name}.age"


def _configuration() -> tuple[str, str, str, str]:
    """The four off-site values, or the one error that explains the lack."""
    key_id = settings.b2_key_id
    application_key = settings.b2_application_key
    bucket_name = settings.b2_bucket_name
    recipient = settings.backup_age_recipient
    if (
        key_id is None
        or application_key is None
        or bucket_name is None
        or recipient is None
    ):
        raise OffsiteError(
            "the off-site pass is not configured: b2_key_id, "
            "b2_application_key, b2_bucket_name and backup_age_recipient "
            "are all required, or all unset for no pass at all"
        )
    return (
        key_id,
        application_key.get_secret_value(),
        bucket_name,
        recipient,
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    code = body.get("code", "unknown")
    message = body.get("message", "")
    return f"HTTP {response.status_code} {code} ({message})"


def _payload(response: httpx.Response, context: str) -> dict[str, Any]:
    """The JSON of a successful answer; an ``OffsiteError`` for the rest."""
    if response.status_code // 100 != 2:
        raise OffsiteError(f"{context} failed: {_error_detail(response)}")
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise OffsiteError(f"{context} returned no JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OffsiteError(f"{context} returned no object")
    return payload


def _authorize(
    client: httpx.Client, key_id: str, application_key: str
) -> _Session:
    """Authorize and say which bucket the key is good for.

    The bucket's id comes from the authorize answer itself: a key
    restricted to one bucket names it there, and asking ``b2_list_buckets``
    instead would need a capability this key deliberately does not have.
    """
    try:
        response = client.get(AUTH_URL, auth=(key_id, application_key))
    except httpx.HTTPError as exc:
        raise OffsiteError(f"authorizing with B2: {exc}") from exc
    data = _payload(response, "authorizing with B2")
    allowed = data.get("allowed") or {}
    bucket_id = allowed.get("bucketId") if isinstance(allowed, dict) else None
    if not isinstance(bucket_id, str) or not bucket_id:
        raise OffsiteError(
            "the B2 key is not restricted to a bucket: the authorize "
            "answer named none, and listing or uploading needs one"
        )
    api_url = data.get("apiUrl")
    token = data.get("authorizationToken")
    if not api_url or not token:
        raise OffsiteError("authorizing with B2 returned no API URL or token")
    return _Session(
        api_url=str(api_url),
        token=str(token),
        bucket_id=bucket_id,
    )


def _remote_names(
    client: httpx.Client, session: _Session, kind_name: str
) -> set[str]:
    """Every name the store already holds under one tier's prefix."""
    prefix = f"{kind_name}/"
    names: set[str] = set()
    start: str | None = None
    while True:
        body: dict[str, object] = {
            "bucketId": session.bucket_id,
            "prefix": prefix,
            "maxFileCount": 1000,
        }
        if start is not None:
            body["startFileName"] = start
        try:
            response = client.post(
                f"{session.api_url}/b2api/v2/b2_list_file_names",
                json=body,
                headers={"Authorization": session.token},
            )
        except httpx.HTTPError as exc:
            raise OffsiteError(f"listing {prefix} in B2: {exc}") from exc
        data = _payload(response, f"listing {prefix} in B2")
        for entry in data.get("files") or []:
            if isinstance(entry, dict):
                names.add(str(entry.get("fileName")))
        start = data.get("nextFileName")
        if start is None:
            return names
        start = str(start)


def _chunks(path: Path) -> Iterator[bytes]:
    """The file, a chunk at a time, for hashing and for the request body."""
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            yield chunk


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    for chunk in _chunks(path):
        digest.update(chunk)
    return digest.hexdigest()


def _upload(
    client: httpx.Client,
    session: _Session,
    name: str,
    ciphertext: Path,
    checksum: str,
) -> None:
    """Upload the ciphertext and count it only once the store agrees.

    The SHA-1 travels with the upload and the answer carries the one
    the store computed; a copy that the store has not confirmed against
    a checksum computed here is not a copy.

    The body is streamed from the file with its length stated, never
    read whole: a full dump is hundreds of megabytes in a job with a
    memory limit, which is the reason `age` runs as a CLI in the first
    place. B2 wants a `Content-Length`, and httpx sends the stated one
    rather than chunking.
    """
    try:
        granted = _payload(
            client.post(
                f"{session.api_url}/b2api/v2/b2_get_upload_url",
                json={"bucketId": session.bucket_id},
                headers={"Authorization": session.token},
            ),
            f"getting an upload URL for {name}",
        )
        upload_url = granted.get("uploadUrl")
        upload_token = granted.get("authorizationToken")
        if not upload_url or not upload_token:
            raise OffsiteError(
                f"getting an upload URL for {name} returned no URL or token"
            )
        response = client.post(
            str(upload_url),
            content=_chunks(ciphertext),
            headers={
                "Authorization": str(upload_token),
                "X-Bz-File-Name": quote(name, safe="/"),
                "Content-Type": "b2/x-auto",
                "Content-Length": str(ciphertext.stat().st_size),
                "X-Bz-Content-Sha1": checksum,
            },
        )
    except httpx.HTTPError as exc:
        raise OffsiteError(f"uploading {name} to B2: {exc}") from exc
    confirmed = _payload(response, f"uploading {name} to B2")
    if confirmed.get("contentSha1") != checksum:
        raise OffsiteError(
            f"{name} was not copied: the store confirmed a different "
            "checksum than the one computed here"
        )


def _copy_one(
    client: httpx.Client,
    session: _Session,
    path: Path,
    kind: BackupKind,
    recipient: str,
) -> str:
    """Encrypt one dump beside it and upload the ciphertext.

    The temporary file is removed whatever happened — the backups volume
    is the one directory that must not grow a stray ciphertext with a
    dump's name in it, and an interrupted run must not leave one that a
    later restore could mistake for a dump.
    """
    directory = path.parent
    handle, scratch_name = tempfile.mkstemp(
        dir=directory, prefix=f".{path.name}.", suffix=".age.partial"
    )
    scratch = Path(scratch_name)
    try:
        with path.open("rb") as source, os.fdopen(handle, "wb") as sink:
            try:
                result = subprocess.run(
                    ["age", "-r", recipient],
                    stdin=source,
                    stdout=sink,
                    stderr=subprocess.PIPE,
                    text=False,
                    check=False,
                )
            except OSError as exc:
                # Missing, or not executable: the image ships `age`, so
                # this is a broken image or a host it was never meant
                # for — said as such, not as a traceback.
                raise OffsiteError(
                    f"age could not run for {path.name}: {exc}"
                ) from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise OffsiteError(f"age failed on {path.name}: {detail}")
        name = remote_name(kind, path)
        _upload(client, session, name, scratch, _sha1_of(scratch))
        return name
    finally:
        scratch.unlink(missing_ok=True)


def copy_new_dumps(
    root: Path | None = None,
    *,
    now: datetime | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[Path]:
    """Copy every local verified dump that has no off-site copy yet.

    Returns the dumps copied, oldest tier's first. Raises
    ``OffsiteError`` naming every dump that was not copied; local dumps
    and their pruning are not this module's to touch, so a failure here
    leaves them exactly as they were — and the next run catches up.
    """
    root = root or settings.backup_dir
    when = now or datetime.now(UTC)
    key_id, application_key, _bucket_name, recipient = _configuration()
    kinds = (inventory_kind(), full_kind())

    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        session = _authorize(client, key_id, application_key)
        remote: set[str] = set()
        for kind in kinds:
            remote |= _remote_names(client, session, kind.name)

        copied: list[Path] = []
        failures: list[str] = []
        for kind in kinds:
            for path in existing(root / kind.subdirectory, kind):
                taken = _timestamp_of(path)
                if (
                    taken is None
                ):  # pragma: no cover - `existing` filtered these
                    continue
                if when - taken >= CATCH_UP_WINDOW:
                    continue
                name = remote_name(kind, path)
                if name in remote:
                    continue
                try:
                    _copy_one(client, session, path, kind, recipient)
                except OffsiteError as exc:
                    failures.append(str(exc))
                    continue
                logger.info("copied %s off-site as %s", path.name, name)
                copied.append(path)

    if failures:
        raise OffsiteError("; ".join(failures))
    return copied
