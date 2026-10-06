"""Shared SUGGEST (#308) fixtures: a bound /v1/contract/ask client on the DMS pack."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes

SESSION = "suggest-308"
SPACE = "alpha"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig", "kid": kid, "x": _b64u(raw)}


@pytest.fixture
def ask_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    from CortexOS.suggest import ask as seam
    from CortexOS.suggest import freeroute_rephrase
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.delenv(seam.ENABLED_ENV, raising=False)
    monkeypatch.delenv(freeroute_rephrase.MODEL_ENV, raising=False)
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

    def _bind(grant: dict[str, str]) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=SESSION,
            org_id="acme",
            space_id=SPACE,
            pool_id="default",
            issuer_key_id="int-1",
            allowed_paths=["/data/pool/acme/**"],
            row_predicates=grant,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            signature="",
        )
        manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
        get_session_registry().bind(verifier.verify(manifest))
        clear_session(SESSION, space_id=SPACE)

    client = TestClient(create_app())

    def _post(question: str):
        resp = client.post(
            "/v1/contract/ask",
            json={"question": question, "session_id": SESSION, "space_id": SPACE},
        )
        assert resp.status_code == 200, resp.text
        return resp

    client.bind = _bind  # type: ignore[attr-defined]
    client.ask = _post  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


@pytest.fixture
def deterministic(monkeypatch: pytest.MonkeyPatch):
    """Fixed uuid4 and drillthrough token, so two asks can be compared byte for byte."""
    from CortexOS.execution import drillthrough

    counter = [0]

    def _uuid4() -> uuid.UUID:
        counter[0] += 1
        return uuid.UUID(int=counter[0])

    monkeypatch.setattr(uuid, "uuid4", _uuid4)
    monkeypatch.setattr(drillthrough, "mint_token", lambda **kw: "dt-fixed")
    return lambda: counter.__setitem__(0, 0)
