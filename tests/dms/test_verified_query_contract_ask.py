"""VERIFIED-QUERY (#309) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives: answer text, rows and the
badge. With ``CORTEX_VERIFIED_QUERY`` off the answer carries no
``verified_query_ids`` key and has exactly the 1.6.0 field set. The library is
not read.

With it on, confirmed examples are placed in the FreeRoute SQL prompt. The
model writes the SQL. That SQL runs through main's real serve path: the L2
validation gate, the listing and stored-plan gates, the session manifest,
plausibility and the plan-shape check. Only ``freeroute.complete`` is mocked
(no live model), and its ``served_*`` copy the recorded C-STAMP response.
Nothing on the answer path is stubbed.

Near-miss must-fails use a model that copies the nearest example's SQL. The
served answer must abstain with ``VQ_EXAMPLE_ECHO``, never carry the
example's rows.
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
SHIP_GRANT = {"transactions": "TRUE", "shipments": "TRUE"}
INV_GRANT = {"inventory": "TRUE"}
# A listing: main's L2 serve path serves a generated listing over granted,
# retrieved tables. A numeric answer must be a stored metric's SQL instead.
VQ_Q = "List the sku and quantity_kg of transactions for SKU-00296"
VQ_SQL = "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00296'"
ASK = "List the sku and quantity_kg of transactions for SKU-00109"
MODEL_SQL = "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00109'"
GRANT_SQL = "SELECT quantity_kg FROM transactions WHERE location_id = 'LOC-004' LIMIT 10"
DELAYED_Q = "How many delayed shipments do we have?"
DELAYED_SQL = "SELECT COUNT(*) AS delayed_count FROM shipments WHERE status = 'DELAYED'"
SKUS_Q = "How many SKUs do we have in inventory?"
SKUS_SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
C_STAMP = ROOT / "tests" / "dms" / "fixtures" / "c_stamp_289" / "resp_body.json"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
        "kid": kid,
        "x": _b64u(
            private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ),
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
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("CORTEX_DEV_MODE", raising=False)
    for env in (vqa.ENABLED_ENV, sm.ENABLED_ENV, sm.SCORED_ROUND_ENV, "DMS_L2_ENABLED"):
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

    client = TestClient(create_app(), headers={"X-API-Key": "dms-demo-admin-key"})
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
    proposed = lib.propose(
        space_id=space_id, question=question, sql=sql, actor="analyst", source="ask:a1"
    )
    return lib.confirm(space_id=space_id, query_id=proposed.id, steward=STEWARD)


def _warehouse(sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _recorded_served() -> dict[str, Any]:
    body = json.loads(C_STAMP.read_text(encoding="utf-8"))
    assert body["served_provider"] and body["served_model"]
    return body


def _assert_not_verified(body: dict[str, Any]) -> None:
    assert "verified_query_ids" not in body
    assert body["reused"] is False


def _assert_served(body: dict[str, Any]) -> None:
    """Main's positive L2 badge on the DMS envelope (L2_VALIDATED engine-side)."""
    assert body["provenance"]["badge"] == "query_skill", body["answer"]
    assert body["provenance"]["layer"] == "generated", body["answer"]
    assert body["route"] != "abstain"
    assert body["drillthrough_token"]


def _assert_echo_abstain(body: dict[str, Any], *needles: str) -> None:
    """Abstain on the envelope, named by the serve-time check. No example rows."""
    assert body["provenance"]["badge"] == "abstain", body["answer"]
    assert body["rows"] == [] and not body["row_count"]
    assert body["sql_used"] is None and body["drillthrough_token"] is None
    assert body["answer"].startswith("I can't answer that"), body["answer"]
    assert vqa.ECHO_CODE in body["answer"], body["answer"]
    for needle in needles:
        assert needle in body["answer"], (needle, body["answer"])
    assert "verified_query_ids" not in body


def _warehouse_rows(sql: str) -> list[dict[str, Any]]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
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


def _asked(prompt: str) -> str:
    """The question the generator was asked, not an example's question."""
    block = prompt.split("QUESTION: ", 1)[1].split("\n\nEmit one DuckDB SELECT", 1)[0]
    return [line for line in block.splitlines() if line.strip()][-1].strip()


def faithful(prompt: str) -> str:
    """A model that writes the listing for the SKU the QUESTION names."""
    import re

    sku = re.search(r"SKU-\d+", _asked(prompt))
    assert sku is not None, prompt
    return f"SELECT sku, quantity_kg FROM transactions WHERE sku = '{sku.group(0)}'"


def copying(prompt: str) -> str:
    """A model that copies the nearest example's SQL, the usual few-shot failure."""
    import re

    found = re.search(r"^SQL: (.+)$", prompt, re.M)
    assert found is not None, "no example in the prompt"
    return found.group(1)


def _arm_model(monkeypatch: pytest.MonkeyPatch, model: Any = faithful) -> list[dict[str, Any]]:
    """Flag on, L2 on, FreeRoute mocked. Every gate on the answer path is real.

    ``model`` maps the prompt text to the SQL the mocked FreeRoute returns:
    a str is returned as is.
    """
    from CortexOS.integrations import freeroute
    from packs.dms.generative import sql_generator

    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(sql_generator, "is_configured", lambda: True)
    recorded = _recorded_served()
    sent: list[dict[str, Any]] = []

    def mocked_complete(task: str, messages: list[dict[str, Any]], **kwargs: Any) -> freeroute.Completion:
        sent.append({"task": task, "messages": messages})
        assert task == "gen-ask-sql", task
        text = "\n".join(str(m.get("content") or "") for m in messages)
        sql = model if isinstance(model, str) else model(text)
        stamp = freeroute.RouteStamp(
            call_id="mock",
            task=task,
            requested=str(recorded.get("model") or ""),
            served=str(recorded.get("model") or ""),
            status=200,
            usable=True,
            impl="test-mock (not OpenVault)",
            served_provider=recorded["served_provider"],
            served_model=recorded["served_model"],
            served_local=bool(recorded.get("served_local")),
            served_reason=str(recorded.get("served_reason") or ""),
        )
        freeroute._journal_add(stamp)
        return freeroute.Completion(ok=True, text=sql, stamp=stamp)

    monkeypatch.setattr(freeroute, "complete", mocked_complete)
    return sent


# -- off: unchanged on the wire ---------------------------------------------
def test_flag_off_answer_is_the_1_4_0_wire_shape(ask_http, monkeypatch: pytest.MonkeyPatch) -> None:
    ask_http.bind_session("alpha")
    _confirm("alpha")

    def untouched() -> None:
        raise AssertionError("verified-query library touched with the flag off")

    monkeypatch.setattr(vqa, "get_verified_queries", untouched)
    resp = _post(ask_http, "alpha")
    assert b"verified_query_ids" not in resp.content
    body = resp.json()
    spec = json.loads((ROOT / "contract" / "openapi-1.4.0.json").read_text(encoding="utf-8"))
    base = set(spec["components"]["schemas"]["Answer"]["properties"])
    prior = json.loads((ROOT / "contract" / "openapi-1.6.0.json").read_text(encoding="utf-8"))
    # 1.5.0 fields (#329) are present and null. verified_query_ids (1.7.0)
    # stays omitted, so the flag-off body is exactly the 1.6.0 Answer.
    assert set(body) - base == {"reconfirm", "plan_sql_rung", "model_calls"}
    assert set(body) == set(prior["components"]["schemas"]["Answer"]["properties"])
    assert body["reconfirm"] is None and body["plan_sql_rung"] is None
    assert body["model_calls"] is None
    assert list(body) == [f for f in Answer.model_fields if f != "verified_query_ids"]
    _assert_not_verified(body)
    assert body["memory_ids_read"] == []
    assert [s.served_op for s in sm.get_space_memory().stamps(space_id="alpha")] == [
        "vq_propose", "write", "vq_confirm",
    ], "no read or reuse with the flag off"


def test_flag_off_binds_no_example_port(ask_http, monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag off: the ask does not swap the L2 port or the phrase lanes."""
    calls: list[str] = []
    real = vqa._suspend_exact_phrase_match

    def spy() -> bool:
        calls.append("suspend")
        return real()

    monkeypatch.setattr(vqa, "_suspend_exact_phrase_match", spy)
    ask_http.bind_session("alpha")
    _post(ask_http, "alpha")
    assert calls == []
    assert vqa.bind_verified_examples(space_id="alpha", scored_pack_id=None) is None


def test_answer_serialization_omits_empty_verified_query_ids() -> None:
    base = {"answer": "a", "audit_id": "x", "route": "r", "provenance": {"layer": "l", "badge": "session"}}
    assert "verified_query_ids" not in Answer.model_validate(base).model_dump_json()
    assert "verified_query_ids" not in Answer.model_validate(base).model_dump()
    got = Answer.model_validate({**base, "verified_query_ids": ["vq_1", "vq_2"]})
    assert got.model_dump()["verified_query_ids"] == ["vq_1", "vq_2"]
    assert Answer.model_validate_json(got.model_dump_json()).verified_query_ids == ["vq_1", "vq_2"]
    spec = json.loads((ROOT / "contract" / "openapi-1.7.0.json").read_text(encoding="utf-8"))
    field = spec["components"]["schemas"]["Answer"]["properties"]["verified_query_ids"]
    assert field["type"] == "array" and field["items"] == {"type": "string"}
    assert "verified_query_id" not in spec["components"]["schemas"]["Answer"]["properties"]


# -- on: generate, never the stored example -----------------------------------
def test_verified_examples_feed_the_generator_and_current_data_is_executed(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")

    body = _ask(ask_http, "alpha")
    prompt = json.dumps(sent)
    assert query.id in prompt and VQ_SQL in prompt and VQ_Q in prompt
    assert sent and all(item["task"] == "gen-ask-sql" for item in sent)
    _assert_served(body)
    assert body["verified_query_ids"] == [query.id]
    assert body["reused"] is False
    assert body["memory_ids_read"] == [query.entry_id]
    (read,) = body["memory_reads"]
    assert read["id"] == query.entry_id and read["kind"] == "solution" and read["space_id"] == "alpha"
    assert read["source"] == f"verified_query:{query.id};ask:a1"
    assert "Not a stored answer" in body["provenance"]["assumptions"]
    assert body["served_provider"] == _recorded_served()["served_provider"]
    assert body["served_model"] == _recorded_served()["served_model"]
    assert execute_spy, "generated SQL is executed"
    executed = "\n".join(sql for sql, _params in execute_spy)
    assert "SKU-00296" not in executed
    assert "SKU-00109" in executed
    # The rows are SKU-00109's current rows, not the example's SKU-00296 rows.
    expected = _warehouse_rows(MODEL_SQL)
    assert len(expected) == 6
    assert body["rows"] == expected
    assert body["row_count"] == len(expected)
    assert body["answer"].startswith(f"Found {len(expected)} row(s).")
    assert "SKU-00109" in body["answer"] and "SKU-00296" not in body["answer"]


def test_verified_query_never_serves_a_cached_answer(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.execution import submit

    _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    first = _ask(ask_http, "alpha")
    _assert_served(first)
    assert first["verified_query_ids"] == [query.id]
    assert first["rows"]

    real = submit.execute_sql

    def moved_on(verified, sql, **kwargs):  # noqa: ANN001
        rows, q_ms, e_ms = real(verified, sql, **kwargs)
        return [{**r, "quantity_kg": float(r.get("quantity_kg") or 0) + 1000} for r in rows], q_ms, e_ms

    monkeypatch.setattr(submit, "execute_sql", moved_on)
    second = _ask(ask_http, "alpha")
    _assert_served(second)
    assert second["verified_query_ids"] == [query.id]
    assert second["rows"][0]["quantity_kg"] == first["rows"][0]["quantity_kg"] + 1000
    assert second["answer_id"] != first["answer_id"]
    assert second["answer"] != first["answer"]


def test_confirmed_question_is_re_served_on_current_data(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmed question itself: the example SQL runs fresh and is served."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    query = _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    expected = _warehouse_rows(DELAYED_SQL)
    body = _ask(ask_http, "alpha", DELAYED_Q)
    _assert_served(body)
    assert body["rows"] == expected
    assert body["answer"] == f"Result: delayed_count = {expected[0]['delayed_count']}"
    assert body["verified_query_ids"] == [query.id]
    assert body["reused"] is False
    assert "DELAYED" in body["sql_used"]


def test_verified_query_ids_is_a_list_of_single_ids(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    first = _confirm("alpha")
    second = _confirm(
        "alpha",
        "List the sku and quantity_kg of transactions for SKU-00297",
        "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00297'",
    )
    resp = _post(ask_http, "alpha")
    body = resp.json()
    _assert_served(body)
    assert first.id in json.dumps(sent) and second.id in json.dumps(sent)
    assert sorted(body["verified_query_ids"]) == sorted([first.id, second.id])
    assert all("," not in qid for qid in body["verified_query_ids"])
    assert b'"verified_query_ids":["' in resp.content
    assert body["rows"] == _warehouse_rows(MODEL_SQL)


# -- must-fails over HTTP -----------------------------------------------------
def test_must_fail_contract_ask_unconfirmed_query_never_reused(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    vq.get_verified_queries().propose(
        space_id="alpha", question=VQ_Q, sql=VQ_SQL, actor="analyst", source="ask:a1"
    )
    body = _ask(ask_http, "alpha")
    assert sent, "the generator ran"
    _assert_not_verified(body)
    assert VQ_SQL not in json.dumps(sent)
    same = _ask(ask_http, "alpha", VQ_Q)
    _assert_not_verified(same)
    assert VQ_SQL not in json.dumps(sent)


def test_must_fail_contract_ask_scored_pack_never_reads_verified_queries(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    _confirm("alpha")
    body = _ask(ask_http, "alpha", scored_pack_id="curated_ceo")
    assert sent, "the generator ran"
    _assert_not_verified(body)
    assert VQ_SQL not in json.dumps(sent)
    assert sm.get_space_memory().stamps(space_id="alpha")[-1].served_reason == sm.SCORED_ROUND_READ


def test_must_fail_contract_ask_space_b_never_reuses_space_a_query(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    ask_http.bind_session("beta")
    query = _confirm("alpha")
    beta = _ask(ask_http, "beta")
    assert sent, "the generator ran"
    _assert_not_verified(beta)
    assert beta["memory_ids_read"] == []
    assert VQ_SQL not in json.dumps(sent)
    sent.clear()
    alpha = _ask(ask_http, "alpha")
    _assert_served(alpha)
    assert alpha["verified_query_ids"] == [query.id]
    assert VQ_SQL in json.dumps(sent)


def test_must_fail_contract_ask_revoked_query_never_reused(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    first = _ask(ask_http, "alpha")
    _assert_served(first)
    assert first["verified_query_ids"] == [query.id]
    vq.get_verified_queries().revoke(space_id="alpha", query_id=query.id, steward=STEWARD)
    sent.clear()
    after = _ask(ask_http, "alpha")
    assert sent, "the generator ran"
    _assert_not_verified(after)
    assert VQ_SQL not in json.dumps(sent)


def test_must_fail_generated_sql_never_widens_beyond_the_grant(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.execution import submit

    _arm_model(monkeypatch, GRANT_SQL)
    ask_http.bind_session("alpha", LOC3_GRANT)
    query = _confirm("alpha")
    enforced: list[str] = []
    real = submit.enforce_manifest

    def spy(sql: str, verified):  # noqa: ANN001
        out = real(sql, verified)
        enforced.append(out)
        return out

    monkeypatch.setattr(submit, "enforce_manifest", spy)
    visible = _warehouse(
        "SELECT COUNT(*) FROM transactions WHERE location_id = 'LOC-004'",
        [],
    )[0][0]
    assert visible > 0, "LOC-004 has rows; the grant must hide them"

    body = _ask(ask_http, "alpha", ASK)
    assert body.get("verified_query_ids") == [query.id]
    assert body["row_count"] == 0
    assert body["rows"] == []
    assert enforced
    assert any("location_id = 'LOC-003'" in sql for sql in enforced)
    assert any("LOC-004" in sql for sql in enforced)
    assert all("SKU-00296" not in sql for sql in enforced)


def test_must_fail_example_outside_grant_or_schema_never_in_prompt(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item: only an example whose grant and schema still match reaches the model."""
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha", OPEN_GRANT)
    inside = _confirm("alpha")
    gone_column = _confirm(
        "alpha",
        "List the sku and legacy_bin of transactions for SKU-00296",
        "SELECT sku, legacy_bin FROM transactions WHERE sku = 'SKU-00296'",
    )
    other_table = _confirm(
        "alpha",
        "List the sku and quantity_kg of shipments for SKU-00296",
        "SELECT sku, quantity_kg FROM shipments WHERE sku = 'SKU-00296'",
    )
    for q in (gone_column, other_table):
        assert vqa.lexical_similarity(ASK, q.question) >= vqa.RETRIEVE_MIN
    body = _ask(ask_http, "alpha")
    prompt = json.dumps(sent)
    assert inside.id in prompt
    assert gone_column.id not in prompt and "legacy_bin" not in prompt, "schema no longer has it"
    assert other_table.id not in prompt and "FROM shipments" not in prompt, "outside the grant"
    _assert_served(body)
    assert body["verified_query_ids"] == [inside.id]
    assert body["rows"] == _warehouse_rows(MODEL_SQL)


# -- must-fail: a near-miss example is never the answer (serve-time check) ----
def test_must_fail_near_miss_example_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review #321 repro: the DELAYED example copied for a PENDING question.

    The copied SQL is the stored ``delayed_count`` metric, so main's plan gate
    accepts it. Only the serve-time check stands between it and the customer.
    """
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    asked = "How many pending shipments do we have?"
    assert vqa.lexical_similarity(asked, DELAYED_Q) == 0.75
    delayed = _warehouse_rows(DELAYED_SQL)[0]["delayed_count"]
    body = _ask(ask_http, "alpha", asked)
    _assert_echo_abstain(body, "'delayed'")
    assert str(delayed) not in body["answer"]


def test_must_fail_near_miss_listing_example_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review #321 listing variant: SKU-00296's rows must not answer SKU-00109."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha")
    _confirm("alpha")
    assert vqa.lexical_similarity(ASK, VQ_Q) > 0.8
    body = _ask(ask_http, "alpha", ASK)
    _assert_echo_abstain(body, "sku-00296")
    assert "SKU-00296" not in json.dumps(body["rows"])


def test_must_fail_copied_example_for_another_field_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No literal to catch: the copy counts SKUs when the question asks for locations."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    _confirm("alpha", SKUS_Q, SKUS_SQL)
    for asked, word in (
        ("How many locations do we have in inventory?", "'locations'"),
        ("How many SKUs do we have in inventory by location?", "'location'"),
    ):
        body = _ask(ask_http, "alpha", asked)
        _assert_echo_abstain(body, word)


def test_must_fail_copied_example_ignores_a_number_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy drops a qualifier the question adds."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    body = _ask(ask_http, "alpha", "How many delayed shipments do we have at LOC-003?")
    _assert_echo_abstain(body, "'003'")
    body = _ask(ask_http, "alpha", "How many delayed shipments do we have by carrier?")
    _assert_echo_abstain(body, "'carrier'")


def test_must_fail_dropped_filter_example_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filter with no value (is_hazardous = true) is kept for a question without it."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    _confirm("alpha", "List hazardous SKUs in inventory", "SELECT sku FROM inventory WHERE is_hazardous = true")
    body = _ask(ask_http, "alpha", "List SKUs in inventory")
    _assert_echo_abstain(body, "'hazardous'")
    same = _ask(ask_http, "alpha", "Show hazardous SKUs in inventory")
    _assert_served(same)
    assert same["rows"] == _warehouse_rows("SELECT sku FROM inventory WHERE is_hazardous = true")


def test_freeroute_is_the_only_model_call_and_it_sees_the_example_sql(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    paraphrase = "Show me the sku and quantity_kg for SKU-00296 transactions"
    assert paraphrase.lower() != VQ_Q.lower()
    body = _ask(ask_http, "alpha", paraphrase)
    assert sent and {item["task"] for item in sent} == {"gen-ask-sql"}
    prompt = json.dumps(sent)
    assert query.id in prompt and VQ_SQL in prompt
    _assert_served(body)
    assert body["verified_query_ids"] == [query.id]
    assert body["served_provider"] == _recorded_served()["served_provider"]
    expected = _warehouse_rows(VQ_SQL)
    assert len(expected) == 13
    assert body["rows"] == expected
    assert body["answer"].startswith("Found 13 row(s).")


def test_must_fail_paraphrase_reaches_certified_example_not_the_phrase_table(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    """Paraphrase retrieves the example. The exact phrase is not a table hit."""
    from CortexOS.dms import answer_engine as ae

    paraphrase = "How many distinct SKUs are currently in our inventory?"
    assert " ".join(paraphrase.lower().split()) != " ".join(SKUS_Q.lower().split())
    phrase_calls: list[str] = []
    real_certified = ae.match_certified
    real_route = ae.route_to_metric

    def spy_certified(question: str):
        phrase_calls.append("certified")
        return real_certified(question)

    def spy_route(question: str):
        phrase_calls.append("route")
        return real_route(question)

    monkeypatch.setattr(ae, "match_certified", spy_certified)
    monkeypatch.setattr(ae, "route_to_metric", spy_route)
    # The model writes the SQL it judges right for the question. Here that is
    # the steward's query, and main's stored-plan gate checks it.
    sent = _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", SKUS_Q, SKUS_SQL)
    expected = _warehouse_rows(SKUS_SQL)

    for question in (paraphrase, SKUS_Q):
        sent.clear()
        phrase_calls.clear()
        execute_spy.clear()
        body = _ask(ask_http, "alpha", question)
        prompt = json.dumps(sent)
        assert query.id in prompt and SKUS_SQL in prompt and SKUS_Q in prompt
        _assert_served(body)
        assert body["verified_query_ids"] == [query.id]
        assert body["provenance"]["badge"] != "certified"
        assert body["reused"] is False
        assert body["rows"] == expected
        assert body["answer"] == f"Result: sku_count = {expected[0]['sku_count']}"
        assert execute_spy, "the model's SQL ran"
        assert sent, "the model wrote it"
        assert phrase_calls == [], question
