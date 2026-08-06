"""P0-DEMO-02 — the router may not answer from a source the session never bound.

The demo-fatal shape: a buyer uploads three spreadsheets, asks about one of
them, and the keyword cascade in ``route_to_metric`` matches on "total" /
"revenue" / "amount" and compiles a metric over ``transactions`` — Netie's own
synthetic demo warehouse. The number is confident, badged ``governed_metric``,
and about data the customer has never seen.

The manifest already knows which tables the session may read. These tests
assert the router consults it *before* answering, and that the refusal arrives
as a governed abstain naming what it can answer over — not as an exception the
ask route turns into an HTTP error.

Assertions are on the rendered answer text and the returned rows (CLAUDE.md
§8). SQL assertions would certify a broken feature as working.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cortex_contract.execution import Manifest, canonical_manifest_bytes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms.answer_engine import ABSTAIN, answer, clear_session
from CortexOS.execution.manifest import JwksCache, ManifestVerifier, VerifiedManifest


def _verified(session_id: str, granted: dict[str, str]) -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=session_id,
        org_id="acme",
        pool_id="pool-upload",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=granted,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="test-signature-not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


@pytest.fixture
def upload_only() -> VerifiedManifest:
    """A session bound to an uploaded workbook — the demo warehouse is NOT granted."""
    return _verified("sess-upload", {"q3_sales_export": "TRUE"})


@pytest.fixture
def session(request) -> str:
    sid = f"grounding-{request.node.name}"
    clear_session(sid)
    return sid


def _assert_abstained(result: dict) -> None:
    """The customer-visible envelope of a refusal, not the SQL behind it."""
    assert result["route"] == ABSTAIN
    assert result["badge"] == "abstain"
    assert result["rows"] == []
    assert result.get("sql_used") is None
    assert result.get("total_count") == 0


# ── the three measured questions from the ticket ─────────────────────────────
@pytest.mark.parametrize(
    "question",
    [
        "what is the total amount in the Q3 sales export",
        "total revenue in my uploaded file",
        "top 3 customers by amount",
    ],
)
def test_ungranted_source_abstains_instead_of_answering_from_demo_warehouse(
    question: str, session: str, upload_only: VerifiedManifest
) -> None:
    """No number about ``transactions`` may reach a session that never bound it."""
    result = answer(question, session_id=session, verified=upload_only)

    _assert_abstained(result)
    # The whole point: no figure from the demo warehouse in the rendered text.
    assert "80,375,993" not in result["answer"]


def test_abstain_names_the_sources_it_can_answer_over(
    session: str, upload_only: VerifiedManifest
) -> None:
    """Step 3 — an abstain that does not say what IS answerable is a dead end."""
    result = answer(
        "what is the total amount in the Q3 sales export",
        session_id=session,
        verified=upload_only,
    )

    _assert_abstained(result)
    assert "q3_sales_export" in result["answer"].lower()


def test_ungranted_table_is_named_so_the_refusal_is_diagnosable(
    session: str, upload_only: VerifiedManifest
) -> None:
    """The operator has to be able to see which table was refused, and why."""
    result = answer(
        "total revenue in my uploaded file",
        session_id=session,
        verified=upload_only,
    )

    _assert_abstained(result)
    assert "transactions" in (result.get("assumptions") or "").lower()


# ── the control: granting the table must still answer (R-0005) ───────────────
def test_granted_source_still_answers_normally(session: str) -> None:
    """A control that refuses legitimate work is a failure, not a win.

    Same question shape, but the session actually bound ``transactions`` — the
    governed answer must survive unchanged.
    """
    verified = _verified("sess-sales", {"transactions": "TRUE"})

    result = answer("Top 5 selling SKUs by revenue", session_id=session, verified=verified)

    assert result["route"] != ABSTAIN
    assert result["badge"] == "certified"
    assert result["rows"]


def test_multi_table_grant_answers_when_every_read_table_is_granted(
    session: str,
) -> None:
    """Grounding is a subset test, not an exact match — extra grants are fine."""
    verified = _verified(
        "sess-wide", {"transactions": "TRUE", "inventory": "TRUE", "suppliers": "TRUE"}
    )

    result = answer("Top 5 selling SKUs by revenue", session_id=session, verified=verified)

    assert result["route"] != ABSTAIN
    assert result["rows"]


# ── the artifact the customer receives (CLAUDE.md §8 Phase 0) ────────────────
# Cortex-side assertions are necessary and insufficient. Before this fix the
# refusal left the answer path as PathNotAllowed and the ask route turned it
# into an HTTP 4xx — so a buyer saw an error, not an answer that explains
# itself. These assert the response an HTTP consumer actually gets.
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig", "kid": kid, "x": _b64u(raw)}


@pytest.fixture
def http(tmp_path: Path):
    """A live ask route with a session bound to an uploaded workbook only."""
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
    from CortexOS.execution.session_manifests import (
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from CortexOS.execution.submit import set_verifier_for_tests

    issuer = Ed25519PrivateKey.generate()
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer)]})
    verifier = ManifestVerifier(cache)
    set_verifier_for_tests(verifier)
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))

    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-http-upload",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates={"q3_sales_export": "TRUE"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="",
    )
    manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
    get_session_registry().bind(verifier.verify(manifest))
    clear_session("sess-http-upload")

    yield TestClient(create_app())

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()


def test_ask_route_returns_a_governed_abstain_not_an_http_error(http) -> None:
    """200 with an abstain envelope — a refusal the buyer can read and act on."""
    resp = http.post(
        "/v1/contract/ask",
        json={
            "session_id": "sess-http-upload",
            "question": "what is the total amount in the Q3 sales export",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows"] == []
    assert body.get("sql_used") is None
    assert "q3_sales_export" in body["answer"].lower()
