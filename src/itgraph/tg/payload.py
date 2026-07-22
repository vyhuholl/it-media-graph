"""Telethon objects as JSON-safe payloads.

The only place in the project that touches payload *shape*. "Verbatim"
means no field is dropped, renamed or interpreted — not that the bytes
survive literally: ``jsonb`` cannot hold a datetime or a byte string, so
those two types are re-encoded and nothing else is.
"""

import base64
from datetime import date, datetime
from typing import Any

__all__ = ["encode_payload", "json_safe"]


def json_safe(value: Any) -> Any:
    """A value from ``.to_dict()``, in a form ``jsonb`` accepts.

    Datetimes become ISO-8601 strings and bytes become base64; both are
    recoverable, so this is re-encoding rather than parsing. Containers
    are walked, keys are left exactly as Telethon spelled them — including
    the ``_`` type tag, which is what makes the payload self-describing.

    Raises ``TypeError`` on a type Telethon has not produced before. That
    is deliberate: guessing at a new type would quietly corrupt history
    that is expensive to fetch again, and the caller records the channel
    as failed and moves on.
    """
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    # Before the int branch: bool is a subclass of int, and datetime a
    # subclass of date.
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (str, int, float)):
        return value
    raise TypeError(
        f"no JSON encoding for {type(value).__name__}; "
        "add one to json_safe rather than dropping the field"
    )


def encode_payload(obj: Any) -> dict[str, Any]:
    """A Telethon object as the payload stored in the raw layer."""
    encoded = json_safe(obj.to_dict())
    if not isinstance(encoded, dict):  # pragma: no cover - defensive
        raise TypeError(f"{type(obj).__name__}.to_dict() is not a mapping")
    return encoded
