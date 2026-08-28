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


def test_provenance_from_flat_refused_is_abstain_not_session():
    """F40 / Cortex#11 — route=refused must not fall through to SESSION."""
    prov = _provenance_from_flat(
        {
            "route": "refused",
            "badge": "refused",
            "layer": "refused",
            "assumptions": "manifest refused the read",
        }
    )
    assert prov.badge == Badge.ABSTAIN
    assert prov.layer == "refused"
    assert prov.assumptions == "manifest refused the read"


def test_provenance_from_flat_refused_route_with_session_badge():
    """The live bug: engine route=refused, badge still session."""
    prov = _provenance_from_flat(
        {"route": "refused", "badge": "session", "layer": "refused"}
    )
    assert prov.badge == Badge.ABSTAIN
    assert prov.badge != Badge.SESSION


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


def test_enrich_answer_never_stamps_session_on_refused():
    verified = SimpleNamespace(manifest={"tables": {}})
    data = _enrich_answer(
        {
            "answer": "I can't answer that",
            "route": "refused",
            "badge": "session",
            "layer": "refused",
            "audit_id": "aud_refused",
            "sql_used": "SELECT 1",
        },
        session_id="ses_1",
        verified=verified,
    )
    prov = data["provenance"]
    badge = prov.badge if isinstance(prov, Provenance) else Provenance.model_validate(prov).badge
    assert badge == Badge.ABSTAIN
    assert data.get("drillthrough_token") is None
    assert data.get("sql_used") is None


def test_abstain_refused_emits_refused_fields():
    from CortexOS.dms.answer_engine import _abstain_refused

    data = _abstain_refused("q", "aud_z", reason="manifest refused")
    assert data["route"] == "refused"
    assert data["layer"] == "refused"
    assert data["badge"] == "refused"


def test_provenance_unknown_badge_is_abstain_not_session():
    """F40 class: catalog / document / empty / sql_not_analyzable must not SESSION."""
    for raw in ("catalog", "document", "sql_not_analyzable", "refused_by_manifest", ""):
        prov = _provenance_from_flat({"badge": raw, "route": "sql", "layer": "engine"})
        assert prov.badge == Badge.ABSTAIN, raw
        assert prov.badge != Badge.SESSION, raw


def test_provenance_mapped_certified_stays_certified():
    prov = _provenance_from_flat({"badge": "certified", "route": "sql", "layer": "engine"})
    assert prov.badge == Badge.CERTIFIED


def test_enrich_answer_fills_contributing_sources_from_granted_tables():
    verified = SimpleNamespace(manifest={"tables": {}})
    data = _enrich_answer(
        {
            "answer": "Revenue is 10",
            "route": "sql",
            "badge": "governed_metric",
            "layer": "engine",
            "audit_id": "aud_src",
            "sql_used": "SELECT 1",
            "granted_sources": ["transactions"],
            "rows": [{"revenue_myr": 10}],
        },
        session_id="ses_1",
        verified=verified,
    )
    srcs = data["contributing_sources"]
    assert srcs
    assert srcs[0]["ref_id"] == "transactions"
    assert srcs[0]["kind"] == "table"
    assert srcs[0]["row_count"] == 1


def test_enrich_answer_sources_empty_on_abstain():
    verified = SimpleNamespace(manifest={"tables": {}})
    data = _enrich_answer(
        {
            "answer": "I can't answer that",
            "route": "abstain",
            "badge": "abstain",
            "granted_sources": ["transactions"],
            "audit_id": "aud_abs",
        },
        session_id="ses_1",
        verified=verified,
    )
    assert data["contributing_sources"] == []


def test_contract_ask_requires_grounding():
    import inspect

    from CortexOS.api import contract_routes

    src = inspect.getsource(contract_routes.contract_ask)
    assert "require_grounding=True" in src
