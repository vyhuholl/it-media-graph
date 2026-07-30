"""Operator-supplied channel references, read before anything is spent.

The door for a hand-written list: the forms a person actually pastes —
a bare name, an ``@name``, a ``t.me`` link — turned into the bare
usernames a lookup takes. Pure: no Telethon, no database, no network.

What counts as a username is **not** decided here. That rule lives in
:mod:`itgraph.derive.references`, where every username in the project
already crosses the same boundary, and it is imported rather than
restated — a list that `add` accepted and `derive` would have dropped
would be two different projects sharing a table.

What is decided here is what happens to a bad entry. Derivation drops one
silently, which is right for parsing a message: a stray path fragment is
noise, not a mistake anyone made. An operator's list is the opposite —
every line was typed on purpose, so a line that cannot work is reported
by name and by number, and reported *together with the others*. A file
with three bad lines should take one run to find all three.

All of which happens before the first request. A malformed entry at line
90 must not cost 89 lookups to discover.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from itgraph.derive.references import normalize_username, parse_tme_link

__all__ = ["EntryError", "InvalidEntry", "parse_entries", "read_entries"]

# What a username has to look like, in words, for an error message. The
# rule itself is `_USERNAME` in `derive/references`; this only has to be
# true of it, and is checked against it by test.
_SHAPE = (
    "4-32 characters of letters, digits or underscore, starting with a letter"
)


@dataclass(frozen=True, slots=True)
class InvalidEntry:
    """One entry that cannot be a public username, and why."""

    where: str
    entry: str
    reason: str

    def __str__(self) -> str:
        return f"{self.where}: {self.entry!r} — {self.reason}"


class EntryError(ValueError):
    """Entries that cannot be looked up, reported together.

    Carries every bad entry rather than the first, because the caller
    stops on it: finding the second mistake should not need another run.
    """

    def __init__(self, invalid: Sequence[InvalidEntry]) -> None:
        listed = "\n  ".join(str(item) for item in invalid)
        super().__init__(
            f"{len(invalid)} entr{'y' if len(invalid) == 1 else 'ies'} "
            f"cannot be looked up:\n  {listed}"
        )
        self.invalid = tuple(invalid)


def _parse_one(entry: str) -> str:
    """One entry as a bare username. Raises ``ValueError`` with the reason.

    A link goes through the same reader that parses links out of message
    text, so `t.me/name`, `t.me/s/name` and `t.me/name/123` all mean the
    channel they name — the post id in the third form is deliberately
    dropped, since the operator is adding a channel and pasted whatever
    was in their clipboard.
    """
    text = entry.strip()
    if not text:
        raise ValueError("empty")

    lowered = text.lower()
    if "joinchat" in lowered or "/+" in lowered or lowered.startswith("+"):
        raise ValueError(
            "an invite link, not a public username — private channels are "
            "out of scope for this command"
        )

    if "/" in text or "t.me" in lowered:
        reference = parse_tme_link(text)
        if reference is None:
            raise ValueError("not a link to a public channel")
        if reference.username is None:
            raise ValueError(
                "a link by channel id — this command resolves public "
                "usernames only"
            )
        return reference.username

    username = normalize_username(text)
    if username is None:
        raise ValueError(f"not a username ({_SHAPE})")
    return username


def _collect(labelled: Iterable[tuple[str, str]]) -> list[str]:
    """Parse every entry, then raise once if any failed.

    De-duplicated case-insensitively — parsing lowercases, so equal names
    are already equal strings — preserving first-seen order, so a run
    bounded by ``--limit`` covers the same entries in the same order when
    it is repeated.
    """
    usernames: dict[str, None] = {}
    invalid: list[InvalidEntry] = []
    for where, entry in labelled:
        try:
            usernames[_parse_one(entry)] = None
        except ValueError as exc:
            invalid.append(InvalidEntry(where, entry.strip(), str(exc)))
    if invalid:
        raise EntryError(invalid)
    return list(usernames)


def parse_entries(entries: Iterable[str]) -> list[str]:
    """Usernames given as arguments, in the forms a person pastes."""
    return _collect(
        (f"argument {position}", entry)
        for position, entry in enumerate(entries, start=1)
    )


def read_entries(path: Path) -> list[str]:
    """Usernames from a file: one per line, ``#`` comments and blanks skipped.

    Comments are what make a list maintainable — a name commented out
    with the reason beside it is the difference between a working file
    and a note somewhere else about which lines to ignore.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return _collect(
        (f"line {number}", line)
        for number, line in enumerate(lines, start=1)
        if line.strip() and not line.lstrip().startswith("#")
    )
