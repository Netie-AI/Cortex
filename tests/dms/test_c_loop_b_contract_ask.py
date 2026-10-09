"""C-LOOP-B (#304) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives (R-0001). The generator is a
mock registered through the seam; every candidate runs through the real
``run_gate`` and the real manifest-enforced ``execute_sql`` under a signed
grant. No model is called.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import SQL_LOOP_ABSTAIN_REASONS, SqlAttempt
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.dms import sql_self_correct_ask as seam
from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes
from CortexOS.memory import space_memory as sm
from CortexOS.memory.space_memory import reset_space_memory_for_tests

ROOT = Path(__file__).resolve().parents[2]
SESSION = "c-loop-b-304"
SPACE = "alpha"
Q = "how many transactions are there"
GRANT = {"transactions": "TRUE"}
GOOD = "SELECT COUNT(*) AS txn_count FROM transactions"
SQL_ERROR = "SELECT SUM(revenue_myr) AS revenue_myr FROM transactions"
EMPTY = "SELECT txn_id FROM transactions WHERE 1 = 0"
NEGATIVE = "SELECT -1 AS txn_count FROM transactions LIMIT 1"
UNKNOWN_TABLE = "SELECT * FROM customers"
UNGRANTED = "SELECT COUNT(*) AS n FROM suppliers"
DDL = "DROP TABLE transactions"
REASON_VALUES = {r.value for r in SQL_LOOP_ABSTAIN_REASONS}


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
    monkeypatch.delenv(seam.ENABLED_ENV, raising=False)
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
    seam.clear_sql_generator()

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

    seam.clear_sql_generator()
    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    reset_space_memory_for_tests()
    netie.config._cached_config = None


class MockGenerator:
    """Replays a fixed script of SQL candidates and records the feedback it got."""

    def __init__(self, *script: str | None) -> None:
        self.script = list(script)
        self.prompts: list[tuple[SqlAttempt, ...]] = []

    def __call__(self, question: str, prior: Sequence[SqlAttempt]) -> str | None:
        self.prompts.append(tuple(prior))
        return self.script[min(len(self.prompts), len(self.script)) - 1]


@pytest.fixture
def loop_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(seam.ENABLED_ENV, "1")

    def install(*script: str | None) -> MockGenerator:
        gen = MockGenerator(*script)
        seam.register_sql_generator(gen)
        return gen

    return install


@pytest.fixture
def sql_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Record every SQL the real gate saw and every SQL the real executor ran."""
    from CortexOS.dms import sql_validate_gate
    from CortexOS.execution import submit

    seen: dict[str, list[str]] = {"gated": [], "executed": []}
    real_gate, real_exec = sql_validate_gate.run_gate, submit.execute_sql

    def gate_spy(sql, *args, **kwargs):  # noqa: ANN001
        seen["gated"].append(sql)
        return real_gate(sql, *args, **kwargs)

    def exec_spy(verified, sql, **kwargs):  # noqa: ANN001
        seen["executed"].append(sql)
        return real_exec(verified, sql, **kwargs)

    monkeypatch.setattr(sql_validate_gate, "run_gate", gate_spy)
    monkeypatch.setattr(submit, "execute_sql", exec_spy)
    return seen


def _ask(client, **extra: Any) -> dict[str, Any]:
    resp = client.post(
        "/v1/contract/ask",
        json={"question": Q, "session_id": SESSION, "space_id": SPACE, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_named_abstain(body: dict[str, Any], reason: str, attempts: int) -> None:
    assert body["provenance"]["badge"] == "abstain"
    assert body["rows"] in (None, [])
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["contributing_sources"] == []
    assert reason in body["answer"]
    loop = body["sql_loop"]
    assert loop["served_outcome"] == "abstained"
    assert loop["served_abstain_reason"] == reason
    assert loop["served_abstain_reason"] in REASON_VALUES
    assert len(loop["served_attempts"]) == attempts
    assert loop["served_retries"] == max(attempts - 1, 0)


# --- flag off: /ask is unchanged ---------------------------------------------


def test_flag_off_contract_ask_never_enters_the_loop(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = seam.self_correct_answer
    monkeypatch.setattr(seam, "self_correct_answer", lambda *a, **k: calls.append("x") or real(*a, **k))
    seam.register_sql_generator(MockGenerator(NEGATIVE))
    body = _ask(ask_http)
    assert calls == []
    assert "sql_loop" not in body
    frozen = json.loads((ROOT / "contract" / "openapi-1.4.0.json").read_text(encoding="utf-8"))
    assert set(body) <= set(frozen["components"]["schemas"]["Answer"]["properties"])
    assert body["provenance"]["layer"] != seam.LAYER
    assert {"txn_count": -1} not in (body["rows"] or [])


# --- answered after self-correction ------------------------------------------


def test_contract_ask_corrects_sql_error_and_empty_result(ask_http, loop_on) -> None:
    gen = loop_on(SQL_ERROR, EMPTY, GOOD)
    body = _ask(ask_http)
    assert body["rows"] == [{"txn_count": 5001}]
    assert "5001" in body["answer"].replace(",", "")
    assert body["sql_used"].startswith(GOOD)
    assert body["provenance"]["badge"] == "session"
    assert body["provenance"]["layer"] == "sql_self_correct"
    assert body["drillthrough_token"]
    loop = body["sql_loop"]
    assert loop["served_outcome"] == "answered"
    assert loop["served_abstain_reason"] is None
    assert loop["served_retries"] == 2
    assert [a["served_check"] for a in loop["served_attempts"]] == ["execute", "empty", "passed"]
    assert [a["served_sql"] for a in loop["served_attempts"]] == [SQL_ERROR, EMPTY, GOOD]
    assert all(a["served_at"] for a in loop["served_attempts"])
    assert "EXPLAIN" in loop["served_attempts"][0]["served_error"]
    assert [len(p) for p in gen.prompts] == [0, 1, 2]


# --- must-fail (1) over HTTP --------------------------------------------------


def test_must_fail_contract_ask_never_serves_implausible_rows(ask_http, loop_on) -> None:
    loop_on(NEGATIVE)
    body = _ask(ask_http)
    _assert_named_abstain(body, "implausible_result_after_retries", attempts=3)
    assert {a["served_check"] for a in body["sql_loop"]["served_attempts"]} == {"negative_count"}
    assert "-1" not in body["answer"]


def test_must_fail_contract_ask_implausible_then_corrected(ask_http, loop_on) -> None:
    loop_on(NEGATIVE, GOOD)
    body = _ask(ask_http)
    assert body["rows"] == [{"txn_count": 5001}]
    checks = [a["served_check"] for a in body["sql_loop"]["served_attempts"]]
    assert checks == ["negative_count", "passed"]


# --- must-fail (2) over HTTP: every loop abstain is named --------------------


@pytest.mark.parametrize(
    ("script", "reason", "attempts"),
    [
        ((SQL_ERROR,), "sql_error_after_retries", 3),
        ((DDL,), "sql_gate_refused_after_retries", 3),
        ((EMPTY,), "empty_result_after_retries", 3),
        ((None,), "no_sql_candidate", 1),
    ],
)
def test_must_fail_contract_ask_abstain_is_named(
    ask_http, loop_on, script: tuple[str | None, ...], reason: str, attempts: int
) -> None:
    loop_on(*script)
    _assert_named_abstain(_ask(ask_http), reason, attempts)


def test_contract_ask_without_registered_generator_abstains_named(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seam.ENABLED_ENV, "1")
    _assert_named_abstain(_ask(ask_http), "sql_generator_unavailable", attempts=0)


# --- must-fail (3) over HTTP --------------------------------------------------


def test_must_fail_contract_ask_never_more_than_two_retries(
    ask_http, loop_on, sql_spy: dict[str, list[str]]
) -> None:
    gen = loop_on(SQL_ERROR, SQL_ERROR, SQL_ERROR, SQL_ERROR, GOOD)
    body = _ask(ask_http)
    assert len(gen.prompts) == 3
    assert sql_spy["gated"].count(SQL_ERROR) == 3
    assert GOOD not in sql_spy["gated"]
    _assert_named_abstain(body, "sql_error_after_retries", attempts=3)


# --- must-fail (4) over HTTP: a retry never bypasses the SQL gate ------------


def test_must_fail_contract_ask_retry_never_bypasses_sql_gate(
    ask_http, loop_on, sql_spy: dict[str, list[str]]
) -> None:
    loop_on(UNKNOWN_TABLE, UNGRANTED, GOOD)
    body = _ask(ask_http)
    assert sql_spy["gated"] == [UNKNOWN_TABLE, UNGRANTED, GOOD]
    (ran,) = sql_spy["executed"]
    assert ran.startswith(GOOD)
    assert body["rows"] == [{"txn_count": 5001}]
    attempts = body["sql_loop"]["served_attempts"]
    assert [a["served_check"] for a in attempts] == ["gate", "gate", "passed"]
    assert "UNKNOWN_TABLE" in attempts[0]["served_error"]
    assert "MANIFEST:" in attempts[1]["served_error"]


def test_must_fail_contract_ask_gate_refusals_are_never_executed(
    ask_http, loop_on, sql_spy: dict[str, list[str]]
) -> None:
    loop_on(DDL, UNGRANTED, UNKNOWN_TABLE)
    body = _ask(ask_http)
    assert sql_spy["gated"] == [DDL, UNGRANTED, UNKNOWN_TABLE]
    assert sql_spy["executed"] == []
    _assert_named_abstain(body, "sql_gate_refused_after_retries", attempts=3)


# --- no scored-pack memory writes ---------------------------------------------


@pytest.mark.parametrize("scored_pack_id", ["curated_ceo", None])
def test_loop_writes_no_memory(
    ask_http, loop_on, monkeypatch: pytest.MonkeyPatch, scored_pack_id: str | None
) -> None:
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    loop_on(NEGATIVE, GOOD)
    extra = {"scored_pack_id": scored_pack_id} if scored_pack_id else {}
    body = _ask(ask_http, **extra)
    assert body["sql_loop"]["served_outcome"] == "answered"
    ops = {s.served_op for s in sm.get_space_memory().stamps(space_id=SPACE)}
    assert ops <= {"read"}, ops
