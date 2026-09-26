"""TRUST-03 (Cortex#259) — an ABSTAIN envelope never lists warehouse sources.

``_provenance_from_flat`` maps an unrecognised badge token (``document``,
``catalog``, ...) to Badge.ABSTAIN, but the enrichment step used to consult
only the raw tokens when deciding whether to fill ``contributing_sources``
and mint a ``drillthrough_token``. The customer then received an abstain
badge next to a list of warehouse tables, which contradicts itself.

Every test here asserts on the enriched envelope the customer receives, and
the confident cases prove the control does not block legitimate answers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cortex_contract.answer import Answer, Badge, Provenance

from CortexOS.api.contract_routes import _enrich_answer


def _badge_of(data: dict) -> Badge:
    prov = data["provenance"]
    if isinstance(prov, Provenance):
        return prov.badge
    return Provenance.model_validate(prov).badge


def _verified():
    return SimpleNamespace(manifest={"tables": {}})


def _flat(badge: str, **extra: object) -> dict:
    base: dict = {
        "answer": "Some prose about a document",
        "route": "sql",
        "badge": badge,
        "layer": "engine",
        "audit_id": f"aud_{badge or 'empty'}",
        "sql_used": "SELECT 1",
        "granted_sources": ["transactions", "customers"],
        "rows": [{"n": 1}],
    }
    base.update(extra)
    return base


@pytest.mark.parametrize("raw", ["document", "catalog", "sql_not_analyzable", ""])
def test_unmapped_badge_abstain_clears_sources_and_drillthrough(raw: str) -> None:
    data = _enrich_answer(_flat(raw), session_id="ses_1", verified=_verified())

    assert _badge_of(data) == Badge.ABSTAIN, raw
    assert data["contributing_sources"] == [], raw
    assert data["drillthrough_token"] is None, raw
    assert data["sql_used"] is None, raw

    # The wire artifact the customer receives must validate and agree.
    env = Answer.model_validate(data)
    assert env.provenance is not None
    assert env.provenance.badge == Badge.ABSTAIN
    assert env.contributing_sources == []
    assert env.drillthrough_token is None


def test_unmapped_badge_with_preset_sources_is_still_cleared() -> None:
    """A caller that pre-filled sources on a document answer still gets []."""
    data = _enrich_answer(
        _flat(
            "document",
            contributing_sources=[
                {"ref_id": "transactions", "container": "warehouse", "member": "transactions"}
            ],
        ),
        session_id="ses_1",
        verified=_verified(),
    )
    assert _badge_of(data) == Badge.ABSTAIN
    assert data["contributing_sources"] == []
    assert data["drillthrough_token"] is None


def test_preset_abstain_provenance_dict_clears_sources() -> None:
    """Provenance already resolved to ABSTAIN (as a dict) with confident-looking tokens."""
    flat = _flat("session")
    flat["provenance"] = {"layer": "engine", "badge": "abstain"}
    data = _enrich_answer(flat, session_id="ses_1", verified=_verified())
    assert _badge_of(data) == Badge.ABSTAIN
    assert data["contributing_sources"] == []
    assert data["drillthrough_token"] is None


def test_preset_blocked_provenance_model_clears_sources() -> None:
    flat = _flat("session")
    flat["provenance"] = Provenance(layer="blocked", badge=Badge.BLOCKED)
    data = _enrich_answer(flat, session_id="ses_1", verified=_verified())
    assert _badge_of(data) == Badge.BLOCKED
    assert data["contributing_sources"] == []
    assert data["drillthrough_token"] is None


# --- no false positive: confident answers keep sources and drillthrough ----


@pytest.mark.parametrize("raw", ["governed_metric", "certified"])
def test_confident_badge_keeps_sources_and_mints_drillthrough(raw: str) -> None:
    data = _enrich_answer(
        _flat(raw, answer="Revenue is 10"), session_id="ses_1", verified=_verified()
    )

    assert _badge_of(data) == Badge(raw)
    srcs = data["contributing_sources"]
    assert [s["ref_id"] for s in srcs] == ["transactions", "customers"]
    assert all(s["container"] == "warehouse" and s["kind"] == "table" for s in srcs)
    assert isinstance(data["drillthrough_token"], str) and data["drillthrough_token"]
    assert data["sql_used"] == "SELECT 1"

    env = Answer.model_validate(data)
    assert env.provenance is not None
    assert env.provenance.badge == Badge(raw)
    assert len(env.contributing_sources) == 2
    assert env.drillthrough_token == data["drillthrough_token"]


@pytest.mark.parametrize("raw", ["query_skill", "generated", "session"])
def test_other_mapped_badges_are_not_treated_as_abstain(raw: str) -> None:
    data = _enrich_answer(_flat(raw), session_id="ses_1", verified=_verified())
    assert _badge_of(data) not in {Badge.ABSTAIN, Badge.BLOCKED}
    assert data["contributing_sources"]
    assert data["drillthrough_token"]


def test_confident_badge_without_sql_keeps_sources_but_no_token() -> None:
    """No SQL means no drillthrough to mint, but sources still stand."""
    data = _enrich_answer(
        _flat("governed_metric", sql_used=None), session_id="ses_1", verified=_verified()
    )
    assert _badge_of(data) == Badge.GOVERNED_METRIC
    assert data["contributing_sources"]
    assert data.get("drillthrough_token") is None
