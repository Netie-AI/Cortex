"""Phase 0 — abstain provenance must never default to SESSION."""

from __future__ import annotations

from types import SimpleNamespace

from cortex_contract.answer import Badge, Provenance

from CortexOS.api.contract_routes import _enrich_answer, _provenance_from_flat


def test_provenance_from_flat_needs_clarification_is_abstain():
    prov = _provenance_from_flat(
        {
            "route": "needs_clarification",
            "badge": "abstain",
            "layer": "abstain",
            "assumptions": "no governed metric",
        }
    )
    assert prov.badge == Badge.ABSTAIN
    assert prov.layer == "abstain"
    assert prov.assumptions == "no governed metric"


def test_provenance_from_flat_blocked():
    prov = _provenance_from_flat({"route": "blocked", "badge": "blocked", "layer": "blocked"})
    assert prov.badge == Badge.BLOCKED


def test_enrich_answer_never_stamps_session_on_abstain():
    verified = SimpleNamespace(manifest={"tables": {}})
    data = _enrich_answer(
        {
            "answer": "I can't answer that",
            "route": "needs_clarification",
            "badge": "abstain",
            "layer": "abstain",
            "audit_id": "aud_x",
            "sql_used": None,
            "assumptions": "out of scope",
        },
        session_id="ses_1",
        verified=verified,
    )
    prov = data["provenance"]
    if isinstance(prov, Provenance):
        assert prov.badge == Badge.ABSTAIN
    else:
        assert Provenance.model_validate(prov).badge == Badge.ABSTAIN
    assert data.get("drillthrough_token") is None


def test_enrich_answer_overrides_preexisting_session_on_abstain():
    verified = SimpleNamespace(manifest={"tables": {}})
    data = _enrich_answer(
        {
            "answer": "I can't answer that",
            "route": "needs_clarification",
            "badge": "abstain",
            "provenance": Provenance(layer="engine", badge=Badge.SESSION),
            "audit_id": "aud_y",
        },
        session_id="ses_1",
        verified=verified,
    )
    prov = data["provenance"]
    badge = prov.badge if isinstance(prov, Provenance) else Provenance.model_validate(prov).badge
    assert badge == Badge.ABSTAIN
