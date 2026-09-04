"""SPACE-01: a named Space is grounded only by that Space's signed grant.

A turn that names space_id must not inherit a session-wide binding, and must
not inherit a grant minted for a different Space. Entitlement is decided by
the signer before minting; the engine enforces that the bound manifest names
the Space the turn asked for.

Assertions are on the served HTTP envelope (R-0001): POST /dms/query and
POST /v1/contract/ask.
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

from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
from CortexOS.execution.manifest import (
    JwksCache,
    ManifestVerifier,
    VerifiedManifest,
    canonical_manifest_bytes,
)
from CortexOS.execution.session_manifests import (
    SessionExpired,
    SessionManifestRegistry,
    SessionUnbound,
    SpaceUnbound,
    get_session_registry,
    reset_session_registry_for_tests,
)

SESSION = "space01-sess"
SA = ["transactions"]
REVENUE_Q = "what is our total revenue"
OUTSIDE_SA_Q = "Which SKUs are below reorder level?"


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


def _verified(
    session_id: str,
    predicates: dict[str, str],
    *,
    space_id: str | None = None,
    now: datetime | None = None,
    lifetime: timedelta = timedelta(minutes=5),
    issuer_kid: str = "int-1",
) -> VerifiedManifest:
    when = now or datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=session_id,
        org_id="acme",
        space_id=space_id,
        pool_id="default",
        issuer_key_id=issuer_kid,
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=predicates,
        issued_at=when.isoformat(),
        expires_at=(when + lifetime).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid=issuer_kid, verified_at=when)


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


def _ask_dms(client, question: str, session_id: str, space_id: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"question": question, "session_id": session_id}
    if space_id is not None:
        payload["space_id"] = space_id
    resp = client.post("/dms/query", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ask_contract(client, question: str, session_id: str, space_id: str | None):
    payload: dict[str, Any] = {"question": question, "session_id": session_id}
    if space_id is not None:
        payload["space_id"] = space_id
    return client.post("/v1/contract/ask", json=payload)


def _assert_answered_over_sa(body: dict[str, Any], *, dms_grant: bool) -> None:
    assert _badge(body) not in {"abstain", "refused", "blocked"}, body
    rows = body.get("rows") or []
    assert rows, "a granted Space turn must still return rows"
    assert float(rows[0].get("revenue_myr") or 0) > 0
    assert body.get("answer")
    assert body.get("sql_used")
    if dms_grant:
        assert body["grant_kind"] == "session"
        assert body["granted_sources"] == SA
    else:
        members = [
            str(s.get("member") or s.get("ref_id") or "").lower()
            for s in (body.get("contributing_sources") or [])
            if isinstance(s, dict)
        ]
        assert any("transactions" in m for m in members) or rows


def _assert_space_refused(body: dict[str, Any], space: str) -> None:
    assert _badge(body) == "abstain", body
    text = (body.get("answer") or "").lower()
    assert f"space '{space}'" in text or f"space {space}" in text
    assert "grants" in text
    assert "entitled" in text
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert body.get("grant_kind", "none") == "none"
    assert body.get("granted_sources", []) == []
    assert "try:" not in text


# -- HTTP: named Space grant -------------------------------------------------
def test_space_alpha_grant_answers_over_sa(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id="alpha")

    dms = _ask_dms(dms_http, REVENUE_Q, SESSION, "alpha")
    _assert_answered_over_sa(dms, dms_grant=True)

    contract = _ask_contract(dms_http, REVENUE_Q, SESSION, "alpha")
    assert contract.status_code == 200, contract.text
    _assert_answered_over_sa(contract.json(), dms_grant=False)


def test_same_session_space_beta_is_refused(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id="alpha")

    dms = _ask_dms(dms_http, REVENUE_Q, SESSION, "beta")
    _assert_space_refused(dms, "beta")

    contract = _ask_contract(dms_http, REVENUE_Q, SESSION, "beta")
    assert contract.status_code == 409, contract.text
    detail = contract.json()["detail"]
    assert detail["code"] == "space_unbound"
    assert "beta" in str(detail["message"]).lower()


def test_session_wide_binding_never_widens_to_named_space(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id=None)

    dms = _ask_dms(dms_http, REVENUE_Q, SESSION, "alpha")
    _assert_space_refused(dms, "alpha")

    contract = _ask_contract(dms_http, REVENUE_Q, SESSION, "alpha")
    assert contract.status_code == 409, contract.text
    detail = contract.json()["detail"]
    assert detail["code"] == "space_unbound"
    assert "alpha" in str(detail["message"]).lower()


def test_space_alpha_question_outside_sa_is_path_not_allowed_class(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id="alpha")

    dms = _ask_dms(dms_http, OUTSIDE_SA_Q, SESSION, "alpha")
    assert _badge(dms) in {"abstain", "refused"}
    assert dms.get("rows") in ([], None)
    assert dms.get("sql_used") is None
    blob = str(dms).lower()
    assert "pathnotallowed" in blob or "inventory" in blob or "ungranted" in blob
    assert "80375993" not in blob.replace(",", "")

    contract = _ask_contract(dms_http, OUTSIDE_SA_Q, SESSION, "alpha")
    assert contract.status_code == 200, contract.text
    body = contract.json()
    assert _badge(body) == "abstain"
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    blob = str(body).lower()
    assert "pathnotallowed" in blob or "inventory" in blob or "ungranted" in blob


def test_mcp_unbound_space_returns_refusal_not_500(dms_http) -> None:
    dms_http.bind_session(SESSION, {"transactions": "TRUE"}, space_id="alpha")
    resp = dms_http.post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {
                "question": REVENUE_Q,
                "session_id": SESSION,
                "space_id": "beta",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["badge"] == "abstain", result
    assert result.get("rows") in ([], None)
    assert "beta" in (result.get("answer") or "").lower()


def test_explicit_verified_grant_does_not_widen_to_another_space() -> None:
    reset_session_registry_for_tests()
    alpha = _verified(SESSION, {"transactions": "TRUE"}, space_id="alpha")
    get_session_registry().bind(alpha)
    try:
        with pytest.raises(UngroundedSession, match="grant is bound to Space 'alpha', not 'beta'"):
            resolve_product_grant(SESSION, alpha, space_id="beta")
    finally:
        reset_session_registry_for_tests()


# -- registry: (session, space) lookup, no fallback, expiry, clear -----------
def test_registry_space_lookup_does_not_fall_back() -> None:
    reg = SessionManifestRegistry()
    alpha = _verified(SESSION, {"transactions": "TRUE"}, space_id="alpha")
    wide = _verified(SESSION, {"inventory": "TRUE"}, space_id=None)
    reg.bind(alpha)
    assert reg.resolve(SESSION, space_id="alpha").manifest.space_id == "alpha"
    with pytest.raises(SpaceUnbound) as missing:
        reg.resolve(SESSION, space_id="beta")
    assert missing.value.code == "space_unbound"
    assert "beta" in str(missing.value)
    # Session-wide bind is latest for the session key, but must not serve alpha.
    reg.bind(wide)
    latest = reg.resolve(SESSION)
    assert (latest.manifest.space_id or "").strip() == ""
    assert "inventory" in latest.row_predicates
    still_alpha = reg.resolve(SESSION, space_id="alpha")
    assert still_alpha.manifest.space_id == "alpha"
    assert "transactions" in still_alpha.row_predicates
    with pytest.raises(SpaceUnbound):
        reg.resolve("other-sess", space_id="alpha")


def test_registry_space_expiry_evicts() -> None:
    reg = SessionManifestRegistry()
    now = datetime.now(timezone.utc)
    alpha = _verified(
        SESSION,
        {"transactions": "TRUE"},
        space_id="alpha",
        now=now,
        lifetime=timedelta(minutes=5),
    )
    reg.bind(alpha, now=now)
    later = now + timedelta(minutes=6)
    with pytest.raises(SessionExpired):
        reg.resolve(SESSION, space_id="alpha", now=later)
    with pytest.raises(SpaceUnbound):
        reg.resolve(SESSION, space_id="alpha", now=now)


def test_registry_clear_drops_both_indexes() -> None:
    reg = SessionManifestRegistry()
    other = "space01-other"
    reg.bind(_verified(SESSION, {"transactions": "TRUE"}, space_id="alpha"))
    reg.bind(_verified(other, {"inventory": "TRUE"}, space_id="alpha"))
    reg.clear(SESSION)
    with pytest.raises(SessionUnbound):
        reg.resolve(SESSION)
    with pytest.raises(SpaceUnbound):
        reg.resolve(SESSION, space_id="alpha")
    assert reg.resolve(other, space_id="alpha").manifest.session_id == other
    reg.clear()
    with pytest.raises(SpaceUnbound):
        reg.resolve(other, space_id="alpha")
    with pytest.raises(SessionUnbound):
        reg.resolve(other)
