"""SEC-01 -- POST /dms/query runs under enforce_manifest.

Assertions are on the HTTP envelope (R-0001, CLAUDE.md section 8), not SQL.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms.answer_engine import MetricPlan, route_to_metric
from CortexOS.execution.manifest import (
    JwksCache,
    ManifestVerifier,
    canonical_manifest_bytes,
)
from cortex_contract.execution import Manifest

WIDE_SESSION = "sec01-http-warehouse"


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

    from CortexOS.dms.warehouse_db import close_cached_connections
    from CortexOS.execution import warehouse as warehouse_mod
    from CortexOS.execution import submit as submit_mod
    from CortexOS.dms import warehouse_db as warehouse_db_mod

    src = warehouse_mod.DEFAULT_DB
    isolated = tmp_path / "dms_demo.duckdb"
    if src.exists():
        import shutil

        shutil.copy2(src, isolated)
        wal = Path(str(src) + ".wal")
        if wal.exists():
            shutil.copy2(wal, Path(str(isolated) + ".wal"))
    monkeypatch.setattr(warehouse_mod, "DEFAULT_DB", isolated)
    monkeypatch.setattr(warehouse_db_mod, "DEFAULT_DB", isolated)
    monkeypatch.setattr(submit_mod, "DEFAULT_DB", isolated)
    close_cached_connections()

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


def test_in_manifest_question_still_answers(dms_http) -> None:
    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)
    assert body["badge"] == "governed_metric", body
    assert body["route"] == "sql"
    assert body["grant_kind"] == "session"
    assert body["rows"], body
    assert float(body["rows"][0]["revenue_myr"]) > 0
    assert body["answer"]


def test_out_of_manifest_table_is_path_not_allowed_on_envelope(dms_http, monkeypatch) -> None:
    """Plan under-reports tables so grounding would pass; enforce_manifest must not.

    A session granted only ``transactions`` must refuse SQL that reads
    ``suppliers``, with PathNotAllowed on the POST /dms/query envelope.
    """
    real = route_to_metric

    def underreport(question: str) -> MetricPlan | None:
        plan = real(question)
        if plan is None:
            return None
        return MetricPlan(
            plan.metric_id, plan.slots, plan.reason, tables=("transactions",)
        )

    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", underreport)
    body = _ask(
        dms_http,
        "Which suppliers have a risk score above 0.7?",
        WIDE_SESSION,
    )
    blob = str(body)
    assert body.get("rows") in ([], None), body
    assert body.get("sql_used") in (None, ""), body
    assert "PathNotAllowed" in blob, body
    assert body["badge"] in ("refused", "abstain"), body
    assert "80375993" not in blob.replace(",", "")
