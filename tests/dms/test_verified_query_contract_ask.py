"""VERIFIED-QUERY (#309) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives (R-0001). With
``CORTEX_VERIFIED_QUERY`` off the answer carries no ``verified_query_id`` key
and has exactly the 1.4.0 field set. With it on, a steward-confirmed query is
bound and re-run through ``run_gate`` + ``execute_sql`` under the session
manifest (a spy proves the re-run, and rows are checked against the warehouse);
the answer is ``reused`` with ``verified_query_id`` and keeps the session badge.

Must-fails here: unconfirmed, scored pack, Space B, revoked, and a bound value
cannot widen beyond the grant. No live model: the FreeRoute test replaces
``freeroute.complete`` with a mock whose ``served_*`` copy the recorded C-STAMP
OpenVault response (tests/dms/fixtures/c_stamp_289).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import Answer
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms import verified_query_ask as vqa
from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes
from CortexOS.memory import space_memory as sm
from CortexOS.memory import verified_query as vq
from CortexOS.memory.space_memory import Actor, reset_space_memory_for_tests

ROOT = Path(__file__).resolve().parents[2]
SESSION = "vq-309"
STEWARD = Actor("steward-alice", "steward")
OPEN_GRANT = {"transactions": "TRUE"}
LOC3_GRANT = {"transactions": "location_id = 'LOC-003'"}
VQ_Q = "What was the total quantity_kg moved for SKU-00296 in June 2026?"
VQ_SQL = (
    "SELECT SUM(quantity_kg) AS total_kg, COUNT(*) AS txn_count FROM transactions "
    "WHERE sku = 'SKU-00296' AND timestamp >= '2026-06-01' AND timestamp < '2026-07-01'"
)
ASK = "total quantity_kg moved for SKU-00109 in June 2026"
LOC_Q = "What was the total quantity_kg moved at LOC-003 in June 2026?"
LOC_SQL = (
    "SELECT SUM(quantity_kg) AS total_kg, COUNT(*) AS txn_count FROM transactions "
    "WHERE location_id = 'LOC-003' AND timestamp >= '2026-06-01' AND timestamp < '2026-07-01'"
)
C_STAMP = ROOT / "tests" / "dms" / "fixtures" / "c_stamp_289" / "resp_body.json"


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
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    for env in (vqa.ENABLED_ENV, vqa.MODEL_MATCH_ENV, sm.ENABLED_ENV, sm.SCORED_ROUND_ENV):
        monkeypatch.delenv(env, raising=False)
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
    vq.reset_verified_queries_for_tests()

    def _bind(space_id: str, grant: dict[str, str] = OPEN_GRANT) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=SESSION,
            org_id="acme",
            space_id=space_id,
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
        clear_session(SESSION, space_id=space_id)

    client = TestClient(create_app())
    client.bind_session = _bind  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    reset_space_memory_for_tests()
    vq.reset_verified_queries_for_tests()
    netie.config._cached_config = None


def _post(client, space_id: str, question: str = ASK, **extra: Any):
    resp = client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SESSION, "space_id": space_id, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp


def _ask(client, space_id: str, question: str = ASK, **extra: Any) -> dict[str, Any]:
    return _post(client, space_id, question, **extra).json()


def _confirm(space_id: str, question: str = VQ_Q, sql: str = VQ_SQL) -> vq.VerifiedQuery:
    lib = vq.get_verified_queries()
    proposed = lib.propose(space_id=space_id, question=question, sql=sql, actor="analyst", source="ask:a1")
    return lib.confirm(space_id=space_id, query_id=proposed.id, steward=STEWARD)


def _warehouse(sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


@pytest.fixture
def execute_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    from CortexOS.execution import submit

    real = submit.execute_sql
    calls: list[tuple[str, Any]] = []

    def spy(verified, sql, **kwargs):  # noqa: ANN001
        calls.append((sql, kwargs.get("params")))
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", spy)
    return calls


def _vq_runs(calls: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [c for c in calls if "$1" in c[0]]


def _assert_not_verified(body: dict[str, Any]) -> None:
    assert "verified_query_id" not in body
    assert body["provenance"]["layer"] != vqa.LAYER
    assert body["reused"] is False


# -- off: unchanged on the wire ---------------------------------------------
def test_flag_off_answer_is_the_1_4_0_wire_shape(ask_http, monkeypatch: pytest.MonkeyPatch) -> None:
    ask_http.bind_session("alpha")
    _confirm("alpha")

    def untouched() -> None:
        raise AssertionError("verified-query library touched with the flag off")

    monkeypatch.setattr(vqa, "get_verified_queries", untouched)
    resp = _post(ask_http, "alpha")
    assert b"verified_query_id" not in resp.content
    body = resp.json()
    spec = json.loads((ROOT / "contract" / "openapi-1.4.0.json").read_text(encoding="utf-8"))
    assert set(body) == set(spec["components"]["schemas"]["Answer"]["properties"])
    assert list(body) == [f for f in Answer.model_fields if f != "verified_query_id"]
    _assert_not_verified(body)
    assert body["memory_ids_read"] == []
    assert [s.served_op for s in sm.get_space_memory().stamps(space_id="alpha")] == [
        "vq_propose", "write", "vq_confirm",
    ], "no read or reuse with the flag off"


def test_answer_serialization_omits_unset_verified_query_id() -> None:
    base = {"answer": "a", "audit_id": "x", "route": "r", "provenance": {"layer": "l", "badge": "session"}}
    assert "verified_query_id" not in Answer.model_validate(base).model_dump_json()
    assert "verified_query_id" not in Answer.model_validate(base).model_dump()
    got = Answer.model_validate({**base, "verified_query_id": "vq_1"})
    assert got.model_dump()["verified_query_id"] == "vq_1"
    assert Answer.model_validate_json(got.model_dump_json()).verified_query_id == "vq_1"


# -- on: re-run, never cached -------------------------------------------------
def test_verified_query_reruns_bound_sql_on_current_data(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    query = _confirm("alpha")

    body = _ask(ask_http, "alpha")
    runs = _vq_runs(execute_spy)
    assert len(runs) == 1, "a verified query is re-run, never served from storage"
    sql, params = runs[0]
    assert "SKU-00109" not in sql and params[0] == "SKU-00109"
    assert body["verified_query_id"] == query.id
    assert body["reused"] is True
    assert body["memory_ids_read"] == [query.entry_id]
    (read,) = body["memory_reads"]
    assert read["id"] == query.entry_id and read["kind"] == "solution" and read["space_id"] == "alpha"
    assert read["source"] == f"verified_query:{query.id};ask:a1"
    assert body["provenance"]["badge"] == "session" and body["provenance"]["layer"] == vqa.LAYER
    assert "not a validation" in body["provenance"]["assumptions"]
    assert body["served_reason"] == f"verified_query:{query.id} (no model called)"
    assert body["served_provider"] is None and body["served_model"] is None
    assert "'SKU-00109'" in body["sql_used"]

    expected = _warehouse(
        "SELECT SUM(quantity_kg), COUNT(*) FROM transactions WHERE sku = ? "
        "AND timestamp >= DATE '2026-06-01' AND timestamp < DATE '2026-07-01'",
        ["SKU-00109"],
    )
    assert expected[0][1] > 0
    assert [(r["total_kg"], r["txn_count"]) for r in body["rows"]] == expected
    assert body["answer"]


def test_verified_query_never_serves_a_cached_answer(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.execution import submit

    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    first = _ask(ask_http, "alpha")
    assert first["verified_query_id"] == query.id

    real = submit.execute_sql

    def moved_on(verified, sql, **kwargs):  # noqa: ANN001
        rows, q_ms, e_ms = real(verified, sql, **kwargs)
        return [{**r, "txn_count": r["txn_count"] + 1000} for r in rows], q_ms, e_ms

    monkeypatch.setattr(submit, "execute_sql", moved_on)
    second = _ask(ask_http, "alpha")
    assert second["verified_query_id"] == query.id
    assert second["rows"][0]["txn_count"] == first["rows"][0]["txn_count"] + 1000
    assert second["answer_id"] != first["answer_id"]


# -- must-fails over HTTP -----------------------------------------------------
def test_must_fail_contract_ask_unconfirmed_query_never_reused(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    vq.get_verified_queries().propose(
        space_id="alpha", question=VQ_Q, sql=VQ_SQL, actor="analyst", source="ask:a1"
    )
    _assert_not_verified(_ask(ask_http, "alpha"))
    _assert_not_verified(_ask(ask_http, "alpha", VQ_Q))
    assert _vq_runs(execute_spy) == []


def test_must_fail_contract_ask_scored_pack_never_reads_verified_queries(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    _confirm("alpha")
    _assert_not_verified(_ask(ask_http, "alpha", scored_pack_id="curated_ceo"))
    assert _vq_runs(execute_spy) == []
    assert sm.get_space_memory().stamps(space_id="alpha")[-1].served_reason == sm.SCORED_ROUND_READ


def test_must_fail_contract_ask_space_b_never_reuses_space_a_query(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    ask_http.bind_session("beta")
    query = _confirm("alpha")
    beta = _ask(ask_http, "beta")
    _assert_not_verified(beta)
    assert beta["memory_ids_read"] == []
    assert _vq_runs(execute_spy) == []
    assert _ask(ask_http, "alpha")["verified_query_id"] == query.id


def test_must_fail_contract_ask_revoked_query_never_reused(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    assert _ask(ask_http, "alpha")["verified_query_id"] == query.id
    vq.get_verified_queries().revoke(space_id="alpha", query_id=query.id, steward=STEWARD)
    _assert_not_verified(_ask(ask_http, "alpha"))
    assert len(_vq_runs(execute_spy)) == 1


def test_must_fail_bound_query_never_widens_beyond_the_grant(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.execution import submit

    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha", LOC3_GRANT)
    query = _confirm("alpha", LOC_Q, LOC_SQL)
    enforced: list[str] = []
    real = submit.enforce_manifest

    def spy(sql: str, verified):  # noqa: ANN001
        out = real(sql, verified)
        enforced.append(out)
        return out

    monkeypatch.setattr(submit, "enforce_manifest", spy)
    visible = _warehouse(
        "SELECT COUNT(*) FROM transactions WHERE location_id = 'LOC-004' "
        "AND timestamp >= DATE '2026-06-01' AND timestamp < DATE '2026-07-01'",
        [],
    )[0][0]
    assert visible > 0, "LOC-004 has rows; the grant must hide them"

    body = _ask(ask_http, "alpha", "total quantity_kg moved at LOC-004 in June 2026")
    assert body["verified_query_id"] == query.id
    assert body["rows"][0]["txn_count"] == 0
    assert any("location_id = 'LOC-003'" in sql and "$1" in sql for sql in enforced)

    own = _ask(ask_http, "alpha", "total quantity_kg moved at LOC-003 in June 2026")
    assert own["verified_query_id"] == query.id and own["rows"][0]["txn_count"] > 0


# -- model-assisted matching: FreeRoute only, mocked --------------------------
def _recorded_served() -> dict[str, Any]:
    body = json.loads(C_STAMP.read_text(encoding="utf-8"))
    assert body["served_provider"] and body["served_model"]
    return body


def test_model_match_goes_through_freeroute_only_and_stamps_served(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    from CortexOS.integrations import freeroute

    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    monkeypatch.setenv(vqa.MODEL_MATCH_ENV, "1")
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    recorded = _recorded_served()
    sent: list[dict[str, Any]] = []

    def mocked_complete(task: str, messages: list[dict[str, Any]], **kwargs: Any) -> freeroute.Completion:
        sent.append({"task": task, "messages": messages})
        assert kwargs["accept"](query.id)
        stamp = freeroute.RouteStamp(
            call_id="mock",
            task=task,
            requested=recorded["model"],
            served=recorded["model"],
            status=200,
            usable=True,
            impl="test-mock (not OpenVault)",
            served_provider=recorded["served_provider"],
            served_model=recorded["served_model"],
            served_local=bool(recorded.get("served_local")),
            served_reason=str(recorded.get("served_reason") or ""),
        )
        return freeroute.Completion(ok=True, text=query.id, stamp=stamp)

    monkeypatch.setattr(freeroute, "complete", mocked_complete)
    body = _ask(ask_http, "alpha", "how many kilos of SKU-00109 shifted during June 2026")
    assert len(sent) == 1 and sent[0]["task"] == vqa.MODEL_TASK
    prompt = json.dumps(sent[0]["messages"])
    assert query.id in prompt and "SELECT" not in prompt and "rows" not in prompt
    assert body["verified_query_id"] == query.id
    assert body["served_provider"] == recorded["served_provider"]
    assert body["served_model"] == recorded["served_model"]
    assert "model match" in body["provenance"]["assumptions"]
    assert _vq_runs(execute_spy)[-1][1][0] == "SKU-00109"

    sent.clear()
    assert _ask(ask_http, "alpha")["verified_query_id"] == query.id
    assert sent == [], "a deterministic hit never calls the model"


def test_model_match_off_never_calls_freeroute(ask_http, monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.integrations import freeroute

    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    ask_http.bind_session("alpha")
    _confirm("alpha")

    def no_model(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("FreeRoute called with model matching off")

    monkeypatch.setattr(freeroute, "complete", no_model)
    _assert_not_verified(_ask(ask_http, "alpha", "how many kilos of SKU-00109 shifted during June 2026"))
