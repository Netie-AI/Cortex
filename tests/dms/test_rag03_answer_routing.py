"""RAG-03: governed L0/L1/skill first, then space-scoped doc-RAG, else abstain.

Keyword RAG used to short-circuit before L0 and serve acme_agreement.txt on a
miss. Assertions are on the served HTTP envelope (R-0001): POST /dms/query.
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

from CortexOS.dms.query_service import CONTRACTS_DIR
from CortexOS.execution.manifest import (
    JwksCache,
    ManifestVerifier,
    canonical_manifest_bytes,
)

SESSION = "rag03-sess"
SOP_MISS_Q = (
    "what does the SOP-DOC-ZZ9 document say about cold-chain bay logs?"
)
METRIC_DOC_Q = "according to the document, what is our total revenue"
BLOCKED_Q = "Drop table inventory"
DOC_HIT_Q = "according to the Acme Corp agreement, what are the payment terms?"


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


def _ask_dms(
    client,
    question: str,
    session_id: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"question": question, "session_id": session_id}
    if space_id is not None:
        payload["space_id"] = space_id
    resp = client.post("/dms/query", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sources(body: dict[str, Any]) -> list[str]:
    raw = body.get("sources") or []
    return [str(s) for s in raw]


def test_sop_doc_zz9_miss_abstains_not_first_file(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id="alpha")
    body = _ask_dms(dms_http, SOP_MISS_Q, SESSION, "alpha")
    assert _badge(body) == "abstain", body
    assert body.get("layer") == "abstain"
    text = (body.get("answer") or "").lower()
    assert "can't answer" in text or "cannot answer" in text or "nothing" in text
    assert "acme_agreement.txt" not in _sources(body)
    assert "acme_agreement.txt" not in text
    assert body.get("rows") in ([], None)


def test_metric_question_with_document_word_stays_governed(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"})
    body = _ask_dms(dms_http, METRIC_DOC_Q, SESSION)
    assert _badge(body) != "document", body
    assert _badge(body) not in {"abstain", "blocked", "refused"}
    rows = body.get("rows") or []
    assert rows, f"governed metric returned no rows: {body.get('answer')!r}"
    text = body.get("answer") or ""
    assert text.strip(), "metric rendered no answer text"
    revenue = float(rows[0].get("revenue_myr") or 0)
    assert revenue > 0
    assert "80375993" in text.replace(",", "") or str(int(revenue)) in text.replace(",", "")
    assert "acme_agreement.txt" not in _sources(body)


def test_blocked_ddl_still_blocked_before_rag(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"})
    body = _ask_dms(dms_http, BLOCKED_Q, SESSION)
    assert body.get("route") == "blocked"
    assert _badge(body) == "blocked"
    assert body.get("layer") == "blocked"
    assert "not permitted" in (body.get("answer") or "").lower()
    assert body.get("rows") in ([], None)
    assert "acme_agreement.txt" not in _sources(body)


def test_space_scoped_doc_rag_after_l1_miss_on_token_hit(dms_http) -> None:
    if not CONTRACTS_DIR.is_dir() or not any(CONTRACTS_DIR.glob("*.txt")):
        pytest.skip("CONTRACTS_DIR missing")
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id="alpha")
    body = _ask_dms(dms_http, DOC_HIT_Q, SESSION, "alpha")
    assert _badge(body) == "document", body
    assert body.get("layer") == "rag"
    assert body.get("route") == "rag"
    text = body.get("answer") or ""
    assert text.strip()
    assert "acme" in text.lower() or "payment" in text.lower() or "net 30" in text.lower()
    assert "acme_agreement.txt" in _sources(body)
    assert body.get("rows") in ([], None)
