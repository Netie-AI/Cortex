"""FOLLOWUP-03 — follow-ups must not widen past the prior turn grant set."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from CortexOS.dms.answer_engine import ABSTAIN, answer, clear_session
from CortexOS.execution.manifest import VerifiedManifest
from packages.cortex_contract.execution import Manifest

TOP5 = "Top 5 selling SKUs by revenue"


@pytest.fixture
def sales_only_verified() -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-grant-bound",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates={"transactions": "TRUE"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="test-signature-not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


@pytest.fixture
def session(request) -> str:
    sid = f"grant-{request.node.name}"
    clear_session(sid)
    return sid


def _assert_abstain_envelope(result: dict) -> None:
    assert result["badge"] == "abstain"
    assert result["route"] == ABSTAIN
    assert result["rows"] == []
    assert result.get("sql_used") is None


def test_aggregate_follow_up_stays_inside_transactions_grant(
    session: str, sales_only_verified: VerifiedManifest
) -> None:
    """Literal aggregates over prior rows do not touch new tables."""
    first = answer(TOP5, session_id=session, verified=sales_only_verified)
    assert first["badge"] == "certified"
    assert first["rows"]

    follow = answer(
        "what is the average of them",
        session_id=session,
        verified=sales_only_verified,
    )

    assert follow["route"] != ABSTAIN
    assert follow["badge"] == "session"
    assert "avg_sales_value_myr" in (follow["rows"] or [{}])[0]
    assert follow["answer"]


def test_low_stock_follow_up_abstains_when_inventory_not_granted(
    session: str, sales_only_verified: VerifiedManifest
) -> None:
    """Prior on transactions only; inventory follow-up must not silently widen."""
    first = answer(TOP5, session_id=session, verified=sales_only_verified)
    assert first["badge"] == "certified"

    follow = answer(
        "which of those are low stock?",
        session_id=session,
        verified=sales_only_verified,
    )

    _assert_abstain_envelope(follow)
    assert "inventory" in (follow.get("assumptions") or "").lower()


def test_recognised_follow_up_does_not_fall_through_to_fresh_router(
    session: str, sales_only_verified: VerifiedManifest
) -> None:
    """Ambiguous arithmetic over a listing abstains instead of re-routing fresh."""
    answer(TOP5, session_id=session, verified=sales_only_verified)

    follow = answer("+ 2000", session_id=session, verified=sales_only_verified)

    _assert_abstain_envelope(follow)
    assert "ambiguous" in (follow.get("assumptions") or "").lower()
