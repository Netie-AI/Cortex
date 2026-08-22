"""ANS-04 — a named subject the semantic layer does not define must abstain.

Measured: answer("top 3 customers by amount") returned badge=governed_metric,
SKU rows, rendered as "Top 3 sales". There is no customers table. A correct
grant does not make that substitution honest.

Assertions are on rendered text and rows, not SQL (R-0001).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms.answer_engine import (
    _answerable_entities,
    answer,
    route_to_metric,
    undefined_subject,
)
from CortexOS.dms.query_service import answer_question
from CortexOS.execution.manifest import (
    JwksCache,
    ManifestVerifier,
    canonical_manifest_bytes,
)
from packages.cortex_contract.execution import Manifest

WIDE_SESSION = "ans04-http-warehouse"
CUSTOMER_QUESTIONS = [
    "top 3 customers by amount",
    "which customers spent the most",
    "how many customers do we have",
]


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


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


@pytest.fixture
def dms_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from CortexOS.dms.answer_engine import clear_session
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
    from CortexOS.execution.session_manifests import (
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from CortexOS.execution.submit import set_verifier_for_tests
    from packs.dms.security.rate_limit import reset_limiter

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    import netie.config

    netie.config._cached_config = None

    issuer = Ed25519PrivateKey.generate()
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer)]})
    verifier = ManifestVerifier(cache)
    set_verifier_for_tests(verifier)
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    reset_limiter(10_000)

    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=WIDE_SESSION,
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates={"transactions": "TRUE"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="",
    )
    manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
    get_session_registry().bind(verifier.verify(manifest))
    clear_session(WIDE_SESSION)

    client = TestClient(create_app())
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


def _ask(client, question: str, session_id: str) -> dict[str, Any]:
    resp = client.post("/dms/query", json={"question": question, "session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_unknown_subject_abstain(body: dict[str, Any], *, subject: str = "customer") -> None:
    assert body["badge"] == "abstain", body
    assert body["layer"] == "abstain"
    assert body["route"] == "needs_clarification"
    assert body["rows"] == []
    assert body.get("sql_used") is None
    text = (body.get("answer") or "").lower()
    assert subject in text
    assert "top 3 sales" not in text
    assert "sku-" not in text
    for name in _answerable_entities():
        assert name.lower() in text, name
    for row in body.get("rows") or []:
        assert "sku" not in {str(k).lower() for k in row}


def test_route_to_metric_does_not_consume_customers_as_skus() -> None:
    assert undefined_subject("top 3 customers by amount") == "customers"
    assert route_to_metric("top 3 customers by amount") is None


def test_answer_top_customers_abstains_on_text_and_rows() -> None:
    body = answer("top 3 customers by amount")
    _assert_unknown_subject_abstain(body)


@pytest.mark.parametrize("question", CUSTOMER_QUESTIONS)
def test_customer_subject_questions_abstain(question: str) -> None:
    body = answer_question(question)
    _assert_unknown_subject_abstain(body)


def test_bound_grant_does_not_substitute_customers_for_skus(dms_http) -> None:
    """The substitution survived a correct grant. Binding transactions is not permission to invent customers."""
    body = _ask(dms_http, "top 3 customers by amount", WIDE_SESSION)
    _assert_unknown_subject_abstain(body)
    assert body["grant_kind"] == "session"


def test_unbound_still_abstains(dms_http) -> None:
    body = _ask(dms_http, "total revenue in my uploaded file", "demo-unbound")
    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert body["grant_kind"] == "none"
    assert "80375993" not in str(body).replace(",", "")


def test_bound_demo_table_still_answers_r0005(dms_http) -> None:
    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)
    assert body["badge"] == "governed_metric"
    assert body["route"] == "sql"
    assert body["grant_kind"] == "session"
    assert body["rows"]
    assert float(body["rows"][0]["revenue_myr"]) > 0
    assert any(ch.isdigit() for ch in (body.get("answer") or ""))


def test_known_sku_rank_still_answers() -> None:
    body = answer_question("Top 5 selling SKUs by revenue")
    assert body["badge"] in ("certified", "governed_metric")
    assert body["rows"]
    assert "sku" in body["rows"][0]
    text = body.get("answer") or ""
    assert "SKU-" in text
    assert "can't answer" not in text.lower()
