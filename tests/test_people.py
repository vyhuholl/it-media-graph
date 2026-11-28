"""What names a person, and that it is removed: the collector's rule.

The table of cases is `people_cases.py`, shared with the migration's copy
of the rule and with the restore's read-back.
"""

import json

import pytest
from people_cases import (
    ALL,
    CASES,
    USER,
    Case,
)

from itgraph.tg.payload import without_people


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


def test_no_person_is_left_in_any_case() -> None:
    for case in CASES:
        cleared = json.dumps(without_people(case.received))
        assert str(USER) not in cleared, case.name
        assert "PeerUser" not in cleared, case.name
        assert "A Fake" not in cleared, case.name
        assert "Another Fake" not in cleared, case.name


def test_scalars_and_empty_containers_pass_through() -> None:
    for value in (None, 0, 1.5, "text", True, [], {}):
        assert without_people(value) == value
