"""What names a person, and that it is removed: the collector's rule.

The table of cases is `people_cases.py`, shared with the migration's copy
of the rule and with the restore's read-back.
"""

import json
from typing import Any

import pytest
from fakes import FakeMessage
from people_cases import (
    ALL,
    CASES,
    ONE_OF_EACH,
    UNTOUCHED,
    USER,
    Case,
    names_nobody,
)

from itgraph.tg.payload import encode_payload, without_people


@pytest.mark.parametrize("case", ALL, ids=[case.name for case in ALL])
def test_each_case_comes_out_as_it_should(case: Case) -> None:
    assert without_people(case.received) == case.cleared


@pytest.mark.parametrize("case", ALL, ids=[case.name for case in ALL])
def test_removing_twice_changes_nothing(case: Case) -> None:
    assert without_people(case.cleared) == case.cleared


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_every_case_that_names_someone_changes(case: Case) -> None:
    """A case that comes out equal would prove nothing about the rule."""
    assert case.received != case.cleared
    assert str(USER) in json.dumps(case.received) or "Fake" in json.dumps(
        case.received
    )


@pytest.mark.parametrize(
    "case", UNTOUCHED, ids=[case.name for case in UNTOUCHED]
)
def test_what_names_nobody_is_returned_equal(case: Case) -> None:
    assert without_people(case.received) == case.received


def test_no_person_is_left_in_any_case() -> None:
    for case in CASES:
        cleared = json.dumps(without_people(case.received))
        assert str(USER) not in cleared, case.name
        assert "PeerUser" not in cleared, case.name
        assert "A Fake" not in cleared, case.name
        assert "Another Fake" not in cleared, case.name


def test_the_input_is_not_changed_in_place() -> None:
    [case] = [c for c in CASES if c.name == "a post's latest commenters"]
    before = json.dumps(case.received, sort_keys=True)

    without_people(case.received)

    assert json.dumps(case.received, sort_keys=True) == before


def test_a_bot_with_its_flag_unset_is_a_person() -> None:
    """Only a bot that says it is one stays; a missing flag is not `True`."""
    users: list[Any] = [
        {"_": "User", "id": 1, "bot": True},
        {"_": "User", "id": 2, "bot": False},
        {"_": "User", "id": 3},
    ]

    assert without_people({"users": users}) == {
        "users": [{"_": "User", "id": 1, "bot": True}]
    }


@pytest.mark.parametrize(
    ("kind", "payload"), ONE_OF_EACH, ids=[kind for kind, _ in ONE_OF_EACH]
)
def test_each_type_and_field_alone_is_removed(
    kind: str, payload: dict[str, Any]
) -> None:
    cleared = without_people(payload)

    assert cleared != payload, kind
    assert names_nobody(cleared), kind
    assert "A Fake Name" not in json.dumps(cleared), kind


def test_scalars_and_empty_containers_pass_through() -> None:
    for value in (None, 0, 1.5, "text", True, [], {}):
        assert without_people(value) == value


def test_encode_payload_applies_it() -> None:
    [case] = [c for c in CASES if c.name == "who wrote it"]

    assert encode_payload(FakeMessage(case.received)) == case.cleared
