"""C7-02: enforce_manifest runs on every L2 candidate before EXPLAIN.

Served envelope assertions (R-0001): POST /dms/query and POST /v1/contract/ask.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms import l2_generation
from CortexOS.dms.l2_generation import L2_MANIFEST_REASON_PREFIX
from CortexOS.dms.sql_validate_gate import MANIFEST_VIOLATION_PREFIX
from CortexOS.execution.manifest import (
    JwksCache,
    ManifestVerifier,
    VerifiedManifest,
    canonical_manifest_bytes,
)
from CortexOS.execution.session_manifests import (
    get_session_registry,
    reset_session_registry_for_tests,
)

SESSION = "c7-02-sess"
REVENUE_Q = "what is our total revenue"
L2_Q = "correlate hazmat dwell time with rainfall percentiles"
OUTSIDE_SQL = "SELECT sku FROM inventory LIMIT 5"


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


def _verified(predicates: dict[str, str]) -> VerifiedManifest:
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="c7-02-unit",
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=predicates,
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=when)


class _OutsideGrantPort:
    def is_configured(self) -> bool:
        return True

    def retrieve_schema(self, question: str) -> dict:
        del question
        return {"tables": {"inventory": {}}}

    def generate_candidates(self, question, schema, *, prior_violations=None):
        del question, schema, prior_violations
        return [OUTSIDE_SQL]

    def record_validated(self, question, sql):
        raise AssertionError("manifest-refused L2 must not promote")


@pytest.fixture
def dms_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from bench.accuracy import _ensure_db_loaded
    from CortexOS.api.app import create_app
    from CortexOS.dms.answer_engine import clear_session
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
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

    def _bind(
        session_id: str,
        row_predicates: dict[str, str],
        *,
        space_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=session_id,
            org_id="acme",
            space_id=space_id,
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
        clear_session(session_id, space_id=space_id)

    client = TestClient(create_app())
    client.bind_session = _bind  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


def _badge(body: dict[str, Any]) -> str:
    raw = body.get("badge")
    if raw:
        return str(raw)
    prov = body.get("provenance") or {}
    if isinstance(prov, dict):
        return str(prov.get("badge") or "")
    return ""


def _force_l2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _OutsideGrantPort())
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr(
        "packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False
    )


def test_attempt_l2_manifest_skips_explain_and_signals_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    calls: list[str] = []

    def _boom(con, sql, params=None):
        del con, params
        calls.append(sql)
        return True, "ok"

    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.explain_dry_run", _boom
    )
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(
        l2_generation, "resolve_l2_generation", lambda: _OutsideGrantPort()
    )
    out = l2_generation.attempt_l2(
        L2_Q, verified=_verified({"transactions": "TRUE"})
    )
    assert out is not None
    assert out.sql is None
    assert out.refused is True
    assert (out.reason or "").startswith(L2_MANIFEST_REASON_PREFIX)
    assert "PathNotAllowed" in (out.reason or "")
    assert "path_not_allowed" in (out.reason or "")
    assert out.violations
    assert any(MANIFEST_VIOLATION_PREFIX in v for v in out.violations)
    assert calls == []


def test_l2_outside_grant_served_refused(dms_http, monkeypatch: pytest.MonkeyPatch):
    _force_l2(monkeypatch)
    dms_http.bind_session(SESSION, {"transactions": "TRUE"})

    resp = dms_http.post(
        "/dms/query", json={"question": L2_Q, "session_id": SESSION}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("route") == "refused"
    assert body.get("layer") == "refused"
    assert _badge(body) == "refused"
    assert _badge(body).lower() != "session"
    text = body.get("answer") or ""
    assert "can't" in text.lower() or "cannot" in text.lower()
    assert "PathNotAllowed" in text or "path_not_allowed" in text
    assert L2_MANIFEST_REASON_PREFIX in (body.get("assumptions") or text)
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert "80375993" not in str(body).replace(",", "")

    contract = dms_http.post(
        "/v1/contract/ask", json={"question": L2_Q, "session_id": SESSION}
    )
    assert contract.status_code == 200, contract.text
    cbody = contract.json()
    badge = _badge(cbody).lower()
    assert badge in {"refused", "abstain"}
    assert badge != "session"
    layer = str(cbody.get("layer") or "").lower()
    prov = cbody.get("provenance") or {}
    if isinstance(prov, dict):
        layer = str(prov.get("layer") or layer).lower()
        pb = str(prov.get("badge") or badge).lower()
        assert pb != "session"
    assert layer in {"refused", "abstain"}
    ctext = cbody.get("answer") or ""
    assert "PathNotAllowed" in ctext or "path_not_allowed" in ctext or "L2_MANIFEST" in ctext
    assert cbody.get("rows") in ([], None)
    assert cbody.get("sql_used") is None


def test_same_grant_l1_revenue_still_answers(dms_http, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(
        l2_generation, "resolve_l2_generation", lambda: _OutsideGrantPort()
    )
    dms_http.bind_session(SESSION, {"transactions": "TRUE"})

    resp = dms_http.post(
        "/dms/query", json={"question": REVENUE_Q, "session_id": SESSION}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _badge(body) not in {"abstain", "refused", "blocked"}
    rows = body.get("rows") or []
    assert rows, "L1 revenue over the granted table must still return rows"
    assert float(rows[0].get("revenue_myr") or 0) > 0
    assert body.get("answer")
    assert body.get("sql_used")

    contract = dms_http.post(
        "/v1/contract/ask", json={"question": REVENUE_Q, "session_id": SESSION}
    )
    assert contract.status_code == 200, contract.text
    cbody = contract.json()
    assert _badge(cbody).lower() not in {"abstain", "refused", "blocked"}
    crows = cbody.get("rows") or []
    assert crows
    assert cbody.get("answer")


def test_contract_ask_sku_count_on_inventory_grant(dms_http):
    """POST /v1/contract/ask: L1 sku_count under a bound inventory grant."""
    dms_http.bind_session(SESSION, {"inventory": "TRUE"})
    contract = dms_http.post(
        "/v1/contract/ask",
        json={"question": "how many skus", "session_id": SESSION},
    )
    assert contract.status_code == 200, contract.text
    cbody = contract.json()
    assert _badge(cbody).lower() not in {"abstain", "refused", "blocked"}
    crows = cbody.get("rows") or []
    assert crows, cbody.get("answer")
    assert "sku_count" in crows[0]
    n = int(crows[0]["sku_count"])
    assert n > 0
    text = cbody.get("answer") or ""
    assert text.strip()
    assert str(n) in text.replace(",", "")
    assert cbody.get("sql_used")
    assert "inventory" in str(cbody.get("sql_used")).lower()
    assert cbody.get("audit_id") or (cbody.get("provenance") or {}).get("audit_id")
