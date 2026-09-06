"""Standing / session / one-off ladder is a server policy floor."""

from __future__ import annotations

from pathlib import Path

from CortexOS.crew.approvals import (
    CIRCUIT_TRIPS,
    MODE_ONE_OFF,
    MODE_SESSION,
    MODE_STANDING,
    ApprovalBook,
)
from CortexOS.crew import policy


def _confirm_kwargs() -> dict[str, object]:
    return {"server": "uacc", "armed": True, "master_on": True}


def test_one_off_stays_confirm() -> None:
    book = ApprovalBook()
    book.set_mode(MODE_ONE_OFF)
    book.record_approve("uacc.click")
    decision, _ = policy.decide_with_approvals(
        "click", approvals=book, **_confirm_kwargs()
    )
    assert decision == policy.CONFIRM


def test_session_and_standing_skip_confirm_until_revoked() -> None:
    book = ApprovalBook()
    book.set_mode(MODE_SESSION)
    book.record_approve("uacc.click")
    decision, reason = policy.decide_with_approvals(
        "click", approvals=book, **_confirm_kwargs()
    )
    assert decision == policy.ALLOW and "session" in reason

    book.set_mode(MODE_STANDING)
    book.record_approve("uacc.click")
    decision, reason = policy.decide("click", approvals=book, **_confirm_kwargs())
    assert decision == policy.ALLOW and "standing" in reason

    book.revoke("uacc.click")
    decision, _ = policy.decide_with_approvals(
        "click", approvals=book, **_confirm_kwargs()
    )
    assert decision == policy.CONFIRM


def test_standing_persists_session_does_not(tmp_path: Path) -> None:
    path = tmp_path / "approvals.json"
    book = ApprovalBook(path)
    book.set_mode(MODE_STANDING)
    book.record_approve("uacc.click")
    book.set_mode(MODE_SESSION)
    book.record_approve("uacc.type")

    revived = ApprovalBook(path)
    assert revived.covers("click", "uacc.click") == MODE_STANDING
    assert revived.covers("type", "uacc.type") is None
    snap = revived.snapshot()
    assert snap["mode"] == MODE_SESSION
    assert snap["session"] == {}


def test_repeated_deny_trips_circuit_and_revokes_standing() -> None:
    book = ApprovalBook()
    book.set_mode(MODE_STANDING)
    book.record_approve("uacc.click")
    for _ in range(CIRCUIT_TRIPS - 1):
        book.record_deny("uacc.click")
        assert book.covers("click", "uacc.click") == MODE_STANDING
    snap = book.record_deny("uacc.click")
    assert snap["mode"] == MODE_ONE_OFF
    assert "uacc.click" in snap["circuit"]
    assert "uacc.click" not in snap["allow"]
    assert book.covers("click", "uacc.click") is None
    decision, reason = policy.decide_with_approvals(
        "click", approvals=book, **_confirm_kwargs()
    )
    assert decision == policy.CONFIRM
    assert "circuit-breaker" in reason


def test_agent_grants_only_tighten_crew_floor() -> None:
    book = ApprovalBook()
    book.set_mode(MODE_STANDING)
    book.record_approve("uacc.click")
    denied, reason = policy.decide_with_approvals(
        "click",
        approvals=book,
        denied=frozenset({"click"}),
        **_confirm_kwargs(),
    )
    assert denied == policy.DENY and "grant" in reason

    off, _ = policy.decide_with_approvals(
        "click",
        server="uacc",
        armed=True,
        master_on=False,
        approvals=book,
    )
    assert off == policy.DENY

    allowed, _ = policy.decide_with_approvals(
        "click",
        approvals=book,
        allowed=frozenset({"screenshot"}),
        **_confirm_kwargs(),
    )
    assert allowed == policy.DENY


def test_login_wall_is_never_auto_allowed() -> None:
    book = ApprovalBook()
    book.set_mode(MODE_STANDING)
    book.record_approve("uacc.type_text")
    decision, _ = policy.decide_with_approvals(
        "type_text",
        server="uacc",
        armed=True,
        master_on=True,
        approvals=book,
        args={"password": "x"},
    )
    assert decision == policy.CONFIRM
