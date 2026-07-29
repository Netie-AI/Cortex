"""C3 — signature, issuer and validity-window checks on a session manifest.

The enforcement half (AST path checks, predicate injection) lives in
``test_manifest_enforcement.py``. This module asks one question: do we believe
this manifest came from an issuer we trust, right now, unmodified?
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.execution.manifest import (
    JwksCache,
    ManifestExpired,
    ManifestMalformed,
    ManifestNotYetValid,
    ManifestSignatureInvalid,
    ManifestUnknownIssuer,
    ManifestVerifier,
)
from packages.cortex_contract.execution import Manifest, canonical_manifest_bytes


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey, **extra: object) -> dict[str, object]:
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
        **extra,
    }


@pytest.fixture()
def issuer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def cache(tmp_path: Path, issuer: Ed25519PrivateKey) -> JwksCache:
    c = JwksCache(path=tmp_path / "jwks.json")
    c.install({"keys": [_jwk("int-1", issuer)]})
    return c


@pytest.fixture()
def verifier(cache: JwksCache) -> ManifestVerifier:
    return ManifestVerifier(cache)


def _manifest(
    private: Ed25519PrivateKey | None = None,
    *,
    kid: str | None = "int-1",
    issued_delta: timedelta = timedelta(0),
    lifetime: timedelta = timedelta(minutes=5),
) -> Manifest:
    now = datetime.now(timezone.utc) + issued_delta
    manifest = Manifest(
        session_id="sess-1",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id=kid,
        allowed_paths=["/data/pool/acme/*.parquet"],
        row_predicates={"orders": "tenant_id = 'acme'"},
        issued_at=now.isoformat(),
        expires_at=(now + lifetime).isoformat(),
        signature="",
    )
    if private is not None:
        manifest.signature = _b64u(private.sign(canonical_manifest_bytes(manifest)))
    return manifest


# ── happy path ───────────────────────────────────────────────────────────────


def test_valid_manifest_verifies(verifier: ManifestVerifier, issuer: Ed25519PrivateKey) -> None:
    verified = verifier.verify(_manifest(issuer))
    assert verified.issuer_kid == "int-1"
    assert verified.allowed_paths == ("/data/pool/acme/*.parquet",)
    assert verified.row_predicates == {"orders": "tenant_id = 'acme'"}


def test_canonical_bytes_exclude_the_signature(issuer: Ed25519PrivateKey) -> None:
    """Otherwise signing would have to sign its own output."""
    assert b"signature" not in canonical_manifest_bytes(_manifest(issuer))


def test_canonical_bytes_are_sorted_and_omit_absent_fields(issuer: Ed25519PrivateKey) -> None:
    payload = json.loads(canonical_manifest_bytes(_manifest(issuer)))
    assert list(payload) == sorted(payload)
    # Nothing absent is serialised, so a 1.0.0 signer produces the same bytes.
    assert all(v is not None and v != [] and v != {} for v in payload.values())


# ── rejection ────────────────────────────────────────────────────────────────


def test_tampered_allowed_paths_breaks_the_signature(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    widened = _manifest(issuer).model_copy(
        update={"allowed_paths": ["/data/pool/acme/*.parquet", "/data/pool/rival/*.parquet"]}
    )
    with pytest.raises(ManifestSignatureInvalid):
        verifier.verify(widened)


def test_predicate_removal_breaks_the_signature(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    stripped = _manifest(issuer).model_copy(update={"row_predicates": {}})
    with pytest.raises(ManifestSignatureInvalid):
        verifier.verify(stripped)


def test_signature_from_another_key_is_rejected(verifier: ManifestVerifier) -> None:
    with pytest.raises(ManifestSignatureInvalid):
        verifier.verify(_manifest(Ed25519PrivateKey.generate()))


def test_unknown_issuer_key_id_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    with pytest.raises(ManifestUnknownIssuer):
        verifier.verify(_manifest(issuer, kid="int-99"))


def test_missing_issuer_key_id_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    with pytest.raises(ManifestMalformed):
        verifier.verify(_manifest(issuer, kid=None))


def test_expired_manifest_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    with pytest.raises(ManifestExpired):
        verifier.verify(
            _manifest(issuer, issued_delta=timedelta(hours=-2), lifetime=timedelta(minutes=1))
        )


def test_issued_in_the_future_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    with pytest.raises(ManifestNotYetValid):
        verifier.verify(_manifest(issuer, issued_delta=timedelta(hours=1)))


def test_small_clock_skew_is_tolerated(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    """A minute of drift between two hosts must not look like an attack."""
    assert verifier.verify(_manifest(issuer, issued_delta=timedelta(seconds=60)))


def test_expires_before_issued_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    with pytest.raises(ManifestMalformed):
        verifier.verify(_manifest(issuer, lifetime=timedelta(seconds=-30)))


def test_naive_timestamp_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    """No timezone means the same manifest expires at different moments per host."""
    naive = _manifest(issuer).model_copy(update={"expires_at": "2030-01-01T00:00:00"})
    with pytest.raises(ManifestMalformed):
        verifier.verify(naive)


def test_signature_that_is_not_base64url_is_rejected(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey
) -> None:
    bad = _manifest(issuer).model_copy(update={"signature": "not base64!!"})
    with pytest.raises((ManifestMalformed, ManifestSignatureInvalid)):
        verifier.verify(bad)


def test_key_outside_its_own_validity_window_is_rejected(
    tmp_path: Path, issuer: Ed25519PrivateKey
) -> None:
    """Intermediates are short-lived; an expired one must stop working even warm."""
    expired_at = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer, exp=expired_at)]})
    with pytest.raises(ManifestUnknownIssuer):
        ManifestVerifier(cache).verify(_manifest(issuer))


# ── outage behaviour ─────────────────────────────────────────────────────────


def test_warm_cache_verifies_with_openvault_unreachable(
    tmp_path: Path, issuer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OpenVault outage must not take the appliance down."""
    path = tmp_path / "jwks.json"
    JwksCache(path=path).install({"keys": [_jwk("int-1", issuer)]})

    import CortexOS.integrations.openvault_client as ovc

    monkeypatch.setattr(ovc, "get_json", lambda *a, **k: None)

    cold = JwksCache(path=path)
    assert cold.refresh() is False  # OpenVault is down
    assert cold.known_kids == ("int-1",)  # the cache survived the failed refresh
    assert ManifestVerifier(cold).verify(_manifest(issuer)).issuer_kid == "int-1"


def test_verify_never_opens_a_socket(
    verifier: ManifestVerifier, issuer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verify path that can block on a socket is one an attacker can stall."""
    import urllib.request

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("verify() opened a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    assert verifier.verify(_manifest(issuer)).issuer_kid == "int-1"


def test_failed_refresh_does_not_erase_working_keys(
    cache: JwksCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    import CortexOS.integrations.openvault_client as ovc

    monkeypatch.setattr(ovc, "get_json", lambda *a, **k: {"keys": []})
    assert cache.refresh() is False
    assert cache.known_kids == ("int-1",)


def test_unsupported_key_types_are_skipped_not_fatal(
    tmp_path: Path, issuer: Ed25519PrivateKey
) -> None:
    """An RSA key added for something unrelated must not stop manifests verifying."""
    cache = JwksCache(path=tmp_path / "jwks.json")
    installed = cache.install(
        {"keys": [{"kty": "RSA", "kid": "rsa-1", "n": "x", "e": "AQAB"}, _jwk("int-1", issuer)]}
    )
    assert installed == 1
    assert cache.known_kids == ("int-1",)


def test_stale_cache_still_verifies(tmp_path: Path, issuer: Ed25519PrivateKey) -> None:
    """Stale means 'refresh wanted', never 'stop verifying'."""
    cache = JwksCache(path=tmp_path / "jwks.json", ttl_s=0)
    cache.install({"keys": [_jwk("int-1", issuer)]})
    assert cache.is_stale is True
    assert ManifestVerifier(cache).verify(_manifest(issuer)).issuer_kid == "int-1"
