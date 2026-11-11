"""Maker-checker rules for alert case decisions (api/casework.py)."""

import pytest

from api import casework
from api.casework import User

ANALYST = User("a", "Analyst A", "Analyst", ("analyst",))
REVIEWER = User("r", "Reviewer R", "Reviewer", ("reviewer",))
LEAD = User("l", "Lead L", "Team lead", ("analyst", "reviewer"))
STEWARD = User("s", "Steward S", "Steward", ("steward",))


@pytest.fixture
def conn(tmp_path):
    return casework.connect(tmp_path / "cases.sqlite")


def test_happy_path_propose_then_approve(conn):
    case = casework.propose(conn, "ALT-1", ANALYST, "Confirmed fraud", "evidence")
    assert case["status"] == casework.PENDING and case["maker_id"] == "a"
    case = casework.approve(conn, "ALT-1", REVIEWER, "agree")
    assert case["status"] == casework.CLOSED and case["checker_id"] == "r"
    actions = [e["action"] for e in casework.audit_trail(conn, "alert", "ALT-1")]
    assert actions == ["Approved disposition", "Proposed disposition"]  # newest first


def test_maker_cannot_check_own_proposal_even_with_both_roles(conn):
    casework.propose(conn, "ALT-2", LEAD, "False positive", "evidence")
    with pytest.raises(casework.SegregationOfDutiesError):
        casework.approve(conn, "ALT-2", LEAD, "self approval")
    with pytest.raises(casework.SegregationOfDutiesError):
        casework.return_case(conn, "ALT-2", LEAD, "self return")
    assert casework.get_case(conn, "ALT-2")["status"] == casework.PENDING


def test_roles_are_enforced(conn):
    with pytest.raises(casework.RoleError):
        casework.propose(conn, "ALT-3", REVIEWER, "False positive", "x")
    casework.propose(conn, "ALT-3", ANALYST, "False positive", "x")
    for user in (ANALYST, STEWARD):
        with pytest.raises(casework.RoleError):
            casework.approve(conn, "ALT-3", user, "x")


def test_state_machine_and_notes(conn):
    with pytest.raises(casework.StateError):
        casework.approve(conn, "ALT-4", REVIEWER, "nothing proposed yet")
    with pytest.raises(casework.CaseError):
        casework.propose(conn, "ALT-4", ANALYST, "Confirmed fraud", "   ")
    with pytest.raises(casework.CaseError):
        casework.propose(conn, "ALT-4", ANALYST, "Not a disposition", "x")
    casework.propose(conn, "ALT-4", ANALYST, "Confirmed fraud", "x")
    with pytest.raises(casework.StateError):
        casework.propose(conn, "ALT-4", ANALYST, "Confirmed fraud", "again")
    casework.return_case(conn, "ALT-4", REVIEWER, "need more evidence")
    assert casework.get_case(conn, "ALT-4")["status"] == casework.RETURNED
    casework.propose(conn, "ALT-4", ANALYST, "False positive", "customer confirmed")
    casework.approve(conn, "ALT-4", REVIEWER, "ok")
    with pytest.raises(casework.StateError):
        casework.approve(conn, "ALT-4", REVIEWER, "closed already")
    assert len(casework.audit_trail(conn, "alert", "ALT-4")) == 4
