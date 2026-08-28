"""Unbound Cortex sessions must abstain. Not answer-with-demo-label.

A session with no binding used to answer from the demo warehouse under
``create_app()`` PACK=dms:

    POST /dms/query session_id=demo-unbound
    "total revenue in my uploaded file" -> badge governed_metric
    rows [{"revenue_myr": 80375993.99}]
    grant_kind local-self-issued
    granted_sources all six demo tables

The grounding gate compared SQL tables to the grant. A self-issued grant
contained every demo table, so it never fired. Honesty on grant_kind is not
a fix. Unbound fails closed *before* that grant is used as permission.

Assertions are on the HTTP envelope (R-0001). In-process ``answer_question``
callers are unchanged (require_grounding defaults off) so the corpus stays
legitimate.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms.answer_engine import route_to_metric
from CortexOS.execution.manifest import (
    JwksCache,
    ManifestVerifier,
    canonical_manifest_bytes,
)
from cortex_contract.execution import Manifest

DEMO_WAREHOUSE_REVENUE = "80375993.99"
DEMO_WAREHOUSE_REVENUE_GROUPED = "80,375,993"

UPLOAD_QUESTIONS = [
    "total revenue in my uploaded file",
    "what is the total amount in the Q3 sales export",
]

WIDE_SESSION = "sec01-http-warehouse"
NARROW_SESSION = "sec01-http-upload"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
        "kid": kid,
        "x": _b64u(raw),
    }


@pytest.fixture
def dms_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from bench.accuracy import _ensure_db_loaded
    from CortexOS.api.app import create_app
    from CortexOS.dms.answer_engine import clear_session
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
    from CortexOS.execution.session_manifests import (
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from CortexOS.execution.submit import set_verifier_for_tests
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    import netie.config

    netie.config._cached_config = None

    _ensure_db_loaded()
    reload()

    issuer = Ed25519PrivateKey.generate()
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer)]})
    verifier = ManifestVerifier(cache)
    set_verifier_for_tests(verifier)
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    reset_limiter(10_000)

    def _bind(session_id: str, row_predicates: dict[str, str]) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=session_id,
            org_id="acme",
            pool_id="default",
            issuer_key_id="int-1",
            allowed_paths=["/data/pool/acme/**"],
            row_predicates=row_predicates,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            signature="",
        )
        manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
        get_session_registry().bind(verifier.verify(manifest))
        clear_session(session_id)

    _bind(WIDE_SESSION, {"transactions": "TRUE"})
    _bind(NARROW_SESSION, {"q3_sales_export": "TRUE"})

    client = TestClient(create_app())
    client.bind_session = _bind  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


def _ask(client, question: str, session_id: str) -> dict[str, Any]:
    resp = client.post("/dms/query", json={"question": question, "session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_no_demo_warehouse_number(body: dict[str, Any]) -> None:
    text = body.get("answer") or ""
    assert DEMO_WAREHOUSE_REVENUE not in text
    assert DEMO_WAREHOUSE_REVENUE_GROUPED not in text
    assert body.get("rows") in ([], None), body.get("rows")


# -- Cortex#14 step 1: the plan states the tables it will read ---------------
def test_route_to_metric_states_the_tables_its_plan_will_read() -> None:
    """Do not recover tables only by re-parsing compiled SQL if the plan can state them."""
    plan = route_to_metric("what is our total revenue")
    assert plan is not None
    assert plan.metric_id == "revenue_total"
    assert "transactions" in plan.tables


def test_upload_phrasing_still_routes_to_revenue_total_with_stated_tables() -> None:
    """Cortex#36 'total' abstain is not this fix. The plan still forms; unbound abstains later."""
    plan = route_to_metric("total revenue in my uploaded file")
    assert plan is not None
    assert plan.metric_id == "revenue_total"
    assert plan.tables == ("transactions",)


# -- the old test, rewritten: unbound must abstain ---------------------------
def test_unbound_session_still_answers_and_says_the_grant_is_self_issued(dms_http) -> None:
    """Retired: an unbound session must not answer from the demo warehouse.

    This used to assert the opposite — that an unbound session still answers,
    with ``grant_kind == "local-self-issued"`` — and that certified an open P0.
    """
    body = _ask(dms_http, "total revenue in my uploaded file", "demo-unbound")

    assert body["badge"] == "abstain", body
    assert body["route"] == "needs_clarification"
    assert body["rows"] == []
    assert body.get("sql_used") is None
    assert body["grant_kind"] == "none"
    assert body.get("granted_sources") == []
    _assert_no_demo_warehouse_number(body)
    assert "80375993" not in str(body).replace(",", "")


@pytest.mark.parametrize("question", UPLOAD_QUESTIONS)
def test_unbound_live_questions_abstain(dms_http, question: str) -> None:
    body = _ask(dms_http, question, "demo-unbound")
    assert body["badge"] == "abstain", body
    assert body["rows"] == []
    assert body.get("sql_used") is None
    _assert_no_demo_warehouse_number(body)


def test_mcp_unbound_session_abstains(dms_http) -> None:
    resp = dms_http.post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {
                "question": "total revenue in my uploaded file",
                "session_id": "demo-unbound",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["badge"] == "abstain", result
    assert result["rows"] == []
    assert "80375993" not in str(result).replace(",", "")


# -- R-0005: a bound demo-table session still answers ------------------------
def test_granted_session_still_answers_a_governed_metric(dms_http) -> None:
    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)

    assert body["badge"] == "governed_metric"
    assert body["route"] == "sql"
    assert body["grant_kind"] == "session"
    assert body["granted_sources"] == ["transactions"]
    assert body["rows"], "a granted governed metric must still return rows"
    assert float(body["rows"][0]["revenue_myr"]) > 0
    assert body["answer"]


def test_granted_session_still_answers_a_certified_query(dms_http) -> None:
    body = _ask(dms_http, "Top 5 selling SKUs by revenue", WIDE_SESSION)

    assert body["badge"] == "certified"
    assert body["grant_kind"] == "session"
    assert len(body["rows"]) == 5
    assert "SKU-" in body["answer"]


def test_bound_session_abstain_names_the_sources_it_can_answer_over(dms_http) -> None:
    body = _ask(dms_http, "what is the total amount in the Q3 sales export", NARROW_SESSION)

    assert body["badge"] == "abstain"
    assert body["grant_kind"] == "session"
    assert body.get("granted_sources") == ["q3_sales_export"]
    assert "q3_sales_export" in body["answer"].lower()
    _assert_no_demo_warehouse_number(body)
