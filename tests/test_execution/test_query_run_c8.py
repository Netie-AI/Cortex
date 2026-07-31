"""C8 — durable query_run persistence for submit and ask telemetry."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.api.contract_routes import contract_ask
from CortexOS.execution.manifest import JwksCache, ManifestVerifier
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import reset_session_registry_for_tests
from CortexOS.execution.submit import set_verifier_for_tests, submit_request
from CortexOS.execution.telemetry import (
    clear_runs_for_tests,
    get_run,
    init,
)
from packages.cortex_contract.answer import AskRequest
from packages.cortex_contract.execution import Manifest, PoolSpec, SubmitRequest, canonical_manifest_bytes


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
def isolated_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "query_run.db"
    monkeypatch.setattr("CortexOS.execution.telemetry.DB_PATH", db)
    monkeypatch.setattr("CortexOS.execution.telemetry._initialized", False)
    init()
    clear_runs_for_tests()


@pytest.fixture()
def issuer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def verifier(
    tmp_path: Path,
    issuer: Ed25519PrivateKey,
    isolated_telemetry: None,
) -> ManifestVerifier:
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
) -> Manifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=session_id,
        org_id="acme",
        pool_id=pool_id,
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates={
            "inventory": "1=1",
            "suppliers": "1=1",
            "orders": "tenant_id = 'acme'",
        },
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="",
    )
    manifest.signature = _b64u(private.sign(canonical_manifest_bytes(manifest)))
    return manifest


def test_submit_bind_persists_query_run(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    result = submit_request(
        SubmitRequest(
            pool=PoolSpec(id="pool-a"),
            plan={"kind": "session_bind"},
            body={},
            manifest=_signed(issuer),
        )
    )
    assert result.ok and result.run_id

    row = get_run(result.run_id)
    assert row is not None
    assert row["run_id"] == result.run_id
    assert row["kind"] == "session_bind"
    assert row["status"] == "bound"
    assert row["session_id"] == "sess-1"
    assert row["pool_id"] == "pool-a"
    assert row["issuer_kid"] == "int-1"
    assert row["recorded_at"]


def test_ask_persists_query_run(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    bind = submit_request(
        SubmitRequest(
            pool=PoolSpec(id="pool-a"),
            plan={"kind": "session_bind"},
            body={},
            manifest=_signed(issuer),
        )
    )
    assert bind.ok

    async def _ask() -> object:
        return await contract_ask(
            AskRequest(question="How many SKUs do we have in inventory?", session_id="sess-1")
        )

    answer = asyncio.run(_ask())
    audit_id = answer.audit_id
    assert audit_id

    row = get_run(audit_id)
    assert row is not None
    assert row["run_id"] == audit_id
    assert row["kind"] == "ask"
    assert row["session_id"] == "sess-1"
    assert row["pool_id"] == "pool-a"
    assert row["issuer_kid"] == "int-1"
    assert row["status"] in {"certified", "governed_metric", "query_skill", "session", "generated"}
