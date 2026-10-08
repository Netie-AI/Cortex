"""C-MEM (#290) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives (R-0001): ``memory_ids_read``
is on every answer and is ``[]`` when memory is off; a reused solution re-runs
its SQL under the session manifest and is marked ``reused`` without any badge
upgrade; Space B never reads Space A's solution. No model is called.
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

from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes
from CortexOS.memory import space_memory as sm
from CortexOS.memory.space_memory import Actor, reset_space_memory_for_tests

SESSION = "c-mem-290"
REVENUE_Q = "what is our total revenue"
GRANT = {"transactions": "TRUE"}
STEWARD = Actor("steward-alice", "steward")


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
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("CORTEX_DEV_MODE", "1")
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
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
    reset_space_memory_for_tests()

    def _bind(session_id: str, space_id: str) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=session_id,
            org_id="acme",
            space_id=space_id,
            pool_id="default",
            issuer_key_id="int-1",
            allowed_paths=["/data/pool/acme/**"],
            row_predicates=GRANT,
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
    reset_space_memory_for_tests()
    netie.config._cached_config = None


def _ask(client, space_id: str, **extra: Any) -> dict[str, Any]:
    resp = client.post(
        "/v1/contract/ask",
        json={"question": REVENUE_Q, "session_id": SESSION, "space_id": space_id, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _confirm_from_answer(space_id: str, body: dict[str, Any]) -> str:
    """Steward confirms the SQL they were shown, in that Space."""
    entry, _ = sm.get_space_memory().confirm_solution(
        space_id=space_id,
        question=REVENUE_Q,
        sql=body["sql_used"],
        steward=STEWARD,
        source=f"ask:{body['answer_id']}",
    )
    return entry.id


@pytest.fixture
def execute_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from CortexOS.execution import submit

    real = submit.execute_sql
    calls: list[str] = []

    def spy(verified, sql, **kwargs):  # noqa: ANN001
        calls.append(sql)
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", spy)
    return calls


def _assert_engine_answer(body: dict[str, Any]) -> None:
    assert body["rows"] and float(body["rows"][0].get("revenue_myr") or 0) > 0, body
    assert body["answer"] and body["sql_used"]


def test_contract_ask_memory_off_reports_empty_memory_ids_read(ask_http) -> None:
    ask_http.bind_session(SESSION, "alpha")
    body = _ask(ask_http, "alpha")
    _assert_engine_answer(body)
    assert "memory_ids_read" in body
    assert body["memory_ids_read"] == []
    assert body["memory_reads"] == []
    assert body["reused"] is False
    assert sm.get_space_memory().stamps(space_id="alpha") == []


def test_contract_ask_reuses_space_solution_by_rerunning_sql(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[str]
) -> None:
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    ask_http.bind_session(SESSION, "alpha")
    first = _ask(ask_http, "alpha")
    _assert_engine_answer(first)
    assert first["memory_ids_read"] == [] and first["reused"] is False
    solution_id = _confirm_from_answer("alpha", first)

    before = len(execute_spy)
    reused = _ask(ask_http, "alpha")
    assert len(execute_spy) == before + 1, "reuse must re-run the SQL, never serve stored rows"
    assert reused["reused"] is True
    assert reused["memory_ids_read"] == [solution_id]
    (read,) = reused["memory_reads"]
    assert read["id"] == solution_id and read["kind"] == "solution"
    assert read["space_id"] == "alpha" and read["version"] == 1
    assert read["source"] == f"ask:{first['answer_id']}"
    assert read["written_at"] and read["served_at"]
    assert reused["rows"] == first["rows"]
    assert reused["answer"]
    assert reused["sql_used"] == first["sql_used"]
    assert reused["provenance"]["badge"] == "session"
    assert reused["provenance"]["layer"] == "memory_reuse"
    assert "not a validation" in reused["provenance"]["assumptions"]
    assert reused["answer_id"] != first["answer_id"]
    ops = [s.served_op for s in sm.get_space_memory().stamps(space_id="alpha")]
    assert ops == ["read", "write", "read", "reuse"]


def test_must_fail_contract_ask_space_b_never_reads_space_a_solution(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    ask_http.bind_session(SESSION, "alpha")
    ask_http.bind_session(SESSION, "beta")
    alpha = _ask(ask_http, "alpha")
    _confirm_from_answer("alpha", alpha)
    assert _ask(ask_http, "alpha")["reused"] is True

    beta = _ask(ask_http, "beta")
    _assert_engine_answer(beta)
    assert beta["reused"] is False
    assert beta["memory_ids_read"] == []
    assert beta["memory_reads"] == []
    assert beta["provenance"]["layer"] != "memory_reuse"
    beta_stamps = sm.get_space_memory().stamps(space_id="beta")
    assert [s.served_op for s in beta_stamps] == ["read"]
    assert beta_stamps[0].served_entry_ids == ()


def test_contract_ask_scored_round_never_reuses(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[str]
) -> None:
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    ask_http.bind_session(SESSION, "alpha")
    _confirm_from_answer("alpha", _ask(ask_http, "alpha"))

    scored = _ask(ask_http, "alpha", scored_pack_id="curated_ceo")
    _assert_engine_answer(scored)
    assert scored["reused"] is False
    assert scored["memory_ids_read"] == []
    last = sm.get_space_memory().stamps(space_id="alpha")[-1]
    assert last.served_op == "read" and last.served_reason == sm.SCORED_ROUND_READ
