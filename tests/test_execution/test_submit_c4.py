"""C4 — submit seam, session bind, pool checks, count-path predicates."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from CortexOS.execution.manifest import (
    JwksCache,
    ManifestMalformed,
    ManifestVerifier,
    enforce_manifest,
)
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import (
    SessionUnbound,
    get_session_registry,
    reset_session_registry_for_tests,
)
from CortexOS.execution.submit import (
    PoolMismatch,
    execute_count,
    execute_sql,
    set_verifier_for_tests,
    submit_request,
    verify_and_check_pool,
)
from CortexOS.execution.telemetry import clear_runs_for_tests, recent_runs
from packages.cortex_contract.execution import (
    Manifest,
    PoolSpec,
    SubmitRequest,
    canonical_manifest_bytes,
)


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


@pytest.fixture()
def issuer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def verifier(tmp_path: Path, issuer: Ed25519PrivateKey) -> ManifestVerifier:
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer)]})
    v = ManifestVerifier(cache)
    set_verifier_for_tests(v)
    reset_session_registry_for_tests()
    clear_runs_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    yield v
    set_verifier_for_tests(None)
    reset_session_registry_for_tests()


def _signed(
    private: Ed25519PrivateKey,
    *,
    session_id: str = "sess-1",
    pool_id: str | None = "pool-a",
    predicates: dict[str, str] | None = None,
    lifetime: timedelta = timedelta(minutes=5),
) -> Manifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=session_id,
        org_id="acme",
        pool_id=pool_id,
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=predicates
        or {
            "inventory": "1=1",
            "suppliers": "1=1",
            "orders": "tenant_id = 'acme'",
        },
        issued_at=now.isoformat(),
        expires_at=(now + lifetime).isoformat(),
        signature="",
    )
    manifest.signature = _b64u(private.sign(canonical_manifest_bytes(manifest)))
    return manifest


def test_verifier_requires_pool_id(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    m = _signed(issuer, pool_id=None)
    # model may still carry None; verifier must refuse
    with pytest.raises(ManifestMalformed):
        verifier.verify(m)


def test_pool_mismatch_refused(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    m = _signed(issuer, pool_id="pool-a")
    with pytest.raises(PoolMismatch):
        verify_and_check_pool(m, "pool-b")


def test_session_bind_then_resolve(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    m = _signed(issuer)
    result = submit_request(
        SubmitRequest(
            pool=PoolSpec(id="pool-a"),
            plan={"kind": "session_bind"},
            body={},
            manifest=m,
        )
    )
    assert result.ok and result.status == "bound" and result.run_id
    bound = get_session_registry().resolve("sess-1")
    assert bound.issuer_kid == "int-1"
    runs = recent_runs(5)
    assert runs and runs[0]["status"] == "bound"


def test_ask_unbound_http(verifier: ManifestVerifier) -> None:
    from CortexOS.api.contract_routes import contract_ask
    from packages.cortex_contract.answer import AskRequest

    async def _run() -> None:
        with pytest.raises(HTTPException) as caught:
            await contract_ask(AskRequest(question="hi", session_id="nobody"))
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "session_unbound"

    import asyncio

    asyncio.run(_run())


def test_session_registry_unbound(verifier: ManifestVerifier) -> None:
    with pytest.raises(SessionUnbound):
        get_session_registry().resolve("missing")


def test_jwks_refresh_installs_keys(tmp_path: Path, issuer: Ed25519PrivateKey, monkeypatch) -> None:
    cache = JwksCache(path=tmp_path / "jwks.json")
    document = {"keys": [_jwk("int-1", issuer)]}

    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.get_json",
        lambda *_a, **_k: document,
    )
    assert cache.refresh() is True
    assert "int-1" in cache.known_kids



def test_submit_sql_and_count_apply_predicate(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey, tmp_path: Path
) -> None:
    import duckdb

    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE orders (id INTEGER, tenant_id VARCHAR)")
    con.execute("INSERT INTO orders VALUES (1, 'acme'), (2, 'other')")
    con.close()

    m = _signed(issuer, predicates={"orders": "tenant_id = 'acme'"})
    verified = verify_and_check_pool(m, "pool-a")
    get_session_registry().bind(verified)

    rows, _, _ = execute_sql(verified, "SELECT id, tenant_id FROM orders", db_path=db)
    assert {r["tenant_id"] for r in rows} == {"acme"}

    # Count path must also see the predicate (not the unfiltered 2).
    n = execute_count(verified, "SELECT id FROM orders LIMIT 10", db_path=db)
    assert n == 1

    # Direct enforce still wraps.
    rewritten = enforce_manifest("SELECT id FROM orders", verified)
    assert "tenant_id" in rewritten.lower()


def test_tampered_signature_fails_submit(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    m = _signed(issuer)
    m.signature = _b64u(b"\x00" * 64)
    result = submit_request(
        SubmitRequest(
            pool=PoolSpec(id="pool-a"),
            plan={"kind": "session_bind"},
            body={},
            manifest=m,
        )
    )
    assert result.ok is False
    assert result.status == "manifest_signature_invalid"


def test_wrong_pool_on_submit(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    m = _signed(issuer, pool_id="pool-a")
    result = submit_request(
        SubmitRequest(
            pool=PoolSpec(id="other"),
            plan={"kind": "session_bind"},
            body={},
            manifest=m,
        )
    )
    assert result.ok is False
    assert result.status == "pool_mismatch"
