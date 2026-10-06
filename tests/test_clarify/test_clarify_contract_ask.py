"""CLARIFY (#307) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives (R-0001), rendered answer
text and rows included. Off by default: with ``CORTEX_CLARIFY`` unset the ask
is answered exactly as before. On: a known-ambiguous ask never returns a
number, an unambiguous ask is answered, and re-sending the chosen option
answers without asking again. No model is called.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms import clarify_ask
from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes

ROOT = Path(__file__).resolve().parents[2]
SESSION = "clarify-307"
SPACE = "alpha"
GRANT = {t: "TRUE" for t in ("transactions", "inventory", "locations", "shipments", "suppliers")}
AMBIGUOUS = ("recent revenue", "What was revenue recently?", "capacity", "delayed", "high risk")
UNAMBIGUOUS = (
    "what is our total revenue",
    "Show warehouse capacity utilisation",
    "how many delayed shipments",
    "revenue in the last 7 days",
)


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
    from CortexOS.memory.space_memory import ENABLED_ENV as MEMORY_ENV
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.delenv(clarify_ask.ENABLED_ENV, raising=False)
    monkeypatch.delenv(MEMORY_ENV, raising=False)
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

    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=SESSION,
        org_id="acme",
        space_id=SPACE,
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
    clear_session(SESSION, space_id=SPACE)

    yield TestClient(create_app())

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


@pytest.fixture
def clarify_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clarify_ask.ENABLED_ENV, "1")


@pytest.fixture
def engine_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from CortexOS.dms import answer_engine

    real = answer_engine.answer
    calls: list[str] = []

    def spy(question: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(question)
        return real(question, **kwargs)

    monkeypatch.setattr(answer_engine, "answer", spy)
    return calls


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from CortexOS.execution import submit

    real = submit.execute_sql
    calls: list[str] = []

    def spy(verified, sql, **kwargs):  # noqa: ANN001
        calls.append(sql)
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", spy)
    return calls


def _post(client, question: str, **extra: Any):
    return client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SESSION, "space_id": SPACE, **extra},
    )


def _ask(client, question: str, **extra: Any) -> dict[str, Any]:
    resp = _post(client, question, **extra)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _keys_1_4() -> set[str]:
    spec = json.loads((ROOT / "contract" / "openapi-1.4.0.json").read_text(encoding="utf-8"))
    return set(spec["components"]["schemas"]["ContractAnswer"]["properties"])


def _assert_numeric(body: dict[str, Any]) -> None:
    assert "clarify" not in body, body
    assert body["sql_used"] and body["rows"], body
    assert body["provenance"]["badge"] != "abstain", body
    assert body["answer"]


@pytest.mark.parametrize("question", AMBIGUOUS)
def test_flag_off_ambiguous_ask_is_answered_as_before(
    ask_http, engine_calls: list[str], monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    resolve_calls: list[str] = []
    monkeypatch.setattr(clarify_ask, "resolve", lambda q, *a, **k: resolve_calls.append(q))

    plain = _post(ask_http, question)
    with_ids = _post(ask_http, question, clarify_option_ids=["opt_anything"])
    assert plain.status_code == with_ids.status_code == 200

    for resp in (plain, with_ids):
        body = resp.json()
        assert set(body) == _keys_1_4(), sorted(set(body) ^ _keys_1_4())
        assert b'"clarify' not in resp.content
        assert body["route"] != "clarify" and body["provenance"]["layer"] != "clarify"
    assert engine_calls == [question, question]
    assert resolve_calls == []

    def _stable(raw: bytes) -> dict[str, Any]:
        body = json.loads(raw)
        for key in ("audit_id", "answer_id", "drillthrough_token"):
            body.pop(key, None)
        return body

    assert _stable(plain.content) == _stable(with_ids.content)


def test_flag_off_does_not_read_the_catalog(ask_http, monkeypatch: pytest.MonkeyPatch) -> None:
    clarify_ask.dms_catalog.cache_clear()
    monkeypatch.setattr(clarify_ask, "dms_route", lambda q: pytest.fail("routed with flag off"))
    _ask(ask_http, "recent revenue")
    assert clarify_ask.dms_catalog.cache_info().currsize == 0


@pytest.mark.parametrize("question", AMBIGUOUS)
def test_must_fail_ambiguous_ask_never_returns_a_number_when_on(
    ask_http, clarify_on, engine_calls: list[str], executed: list[str], question: str
) -> None:
    body = _ask(ask_http, question)
    clarify = body.get("clarify")
    assert clarify, f"{question!r} returned {body['answer']!r} with rows {body.get('rows')}"
    assert body["rows"] is None and body["row_count"] is None
    assert body["sql_used"] is None and body["drillthrough_token"] is None
    assert body["contributing_sources"] == []
    assert body["route"] == "clarify"
    assert body["provenance"] == {
        "layer": "clarify",
        "badge": "abstain",
        "metric_id": None,
        "query_source": None,
        "assumptions": clarify["served_reason"],
    }
    assert engine_calls == [] and executed == []

    assert clarify["ambiguity_type"] in {"metric", "time_window"}
    assert 2 <= len(clarify["options"]) <= 5
    assert clarify["served_by"] == "cortex:clarify"
    assert clarify["served_at"] and clarify["served_catalog"].startswith("sha256:")
    labels = "; ".join(f"({i}) {o['label']}" for i, o in enumerate(clarify["options"], 1))
    assert body["answer"] == f"{clarify['question']} Options: {labels}."


@pytest.mark.parametrize("question", UNAMBIGUOUS)
def test_must_fail_unambiguous_ask_is_answered_not_clarified_when_on(
    ask_http, clarify_on, engine_calls: list[str], question: str
) -> None:
    body = _ask(ask_http, question)
    _assert_numeric(body)
    assert "clarify_resolved" not in body
    assert engine_calls == [question]


def test_follow_up_with_chosen_window_answers_without_asking_again(
    ask_http, clarify_on, engine_calls: list[str], executed: list[str]
) -> None:
    first = _ask(ask_http, "recent revenue")
    (week,) = [o for o in first["clarify"]["options"] if o["interpretation"]["days"] == "7"]
    assert week["interpretation"] == {"metric_id": "revenue_windowed", "days": "7"}

    second = _ask(ask_http, "recent revenue", clarify_option_ids=[week["id"]])
    _assert_numeric(second)
    assert second["clarify_resolved"] == [week["id"]]
    assert engine_calls == [week["resolved_question"]]
    assert "INTERVAL '7' DAY" in second["sql_used"]
    assert "revenue_myr" in second["rows"][0]
    assert executed and "INTERVAL '7' DAY" in executed[-1]


def test_follow_up_with_chosen_metric_answers_without_asking_again(
    ask_http, clarify_on, engine_calls: list[str]
) -> None:
    first = _ask(ask_http, "capacity")
    assert first["clarify"]["ambiguity_type"] == "metric"
    (util,) = [
        o for o in first["clarify"]["options"]
        if o["interpretation"]["metric_id"] == "capacity_utilisation"
    ]
    second = _ask(ask_http, "capacity", clarify_option_ids=[util["id"]])
    _assert_numeric(second)
    assert second["clarify_resolved"] == [util["id"]]
    assert engine_calls == [util["resolved_question"]]
    assert second["provenance"]["metric_id"] == "capacity_utilisation"


def test_option_not_offered_asks_again(ask_http, clarify_on, engine_calls: list[str]) -> None:
    body = _ask(ask_http, "recent revenue", clarify_option_ids=["opt_forged"])
    assert body["clarify"] and body["rows"] is None and body["sql_used"] is None
    assert "'opt_forged' was not offered" in body["clarify"]["served_reason"]
    assert "clarify_resolved" not in body
    assert engine_calls == []
