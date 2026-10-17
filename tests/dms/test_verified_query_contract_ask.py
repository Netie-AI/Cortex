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

Near-miss must-fails use a model that copies the nearest example's SQL, or
rewrites it into an equivalent form. The served answer must abstain with
``VQ_EXAMPLE_ECHO``, never carry the example's rows. An example's own SQL,
or SQL that returns its rows, answers only the question the steward
confirmed with it (normalised), and is then executed fresh. Every other
question, a paraphrase included, gets new SQL from the model or abstains.
The example's SQL is executed only by the serve-time result comparison,
under the same session manifest; its rows are never served.
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
HIGHEST_Q = "Which 5 SKUs have the highest quantity_kg in inventory?"
HIGHEST_SQL = "SELECT sku, quantity_kg FROM inventory ORDER BY quantity_kg DESC LIMIT 5"
RANGE_Q = (
    "List the sku and quantity_kg of inventory where quantity_kg is above 300 "
    "and reorder_level_kg is below 10"
)
RANGE_SQL = "SELECT sku, quantity_kg FROM inventory WHERE quantity_kg > 300 AND reorder_level_kg < 10"
REORDERED_Q = (
    "List the sku and quantity_kg of inventory where quantity_kg is below 300 "
    "and reorder_level_kg is above 10"
)
LOWEST_Q = "Which 5 SKUs have the lowest quantity_kg in inventory?"
INBOUND_Q = "List the sku and quantity_kg of inbound transactions for SKU-00296"
INBOUND_SQL = (
    "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00296' AND txn_type = 'inbound'"
)
COPY_REASON = "only the question the steward confirmed for it may re-serve it"
ROWS_REASON = "only the question the steward confirmed for it may re-serve them"
# Review @ 4bb518b: rewrites of VQ_SQL that the SQL text comparison cannot
# see. Each returns the example's 13 rows; only 3 of them are inbound.
EQUIVALENT_COPIES = {
    "commuted_eq": "SELECT sku, quantity_kg FROM transactions WHERE 'SKU-00296' = sku",
    "single_in": "SELECT sku, quantity_kg FROM transactions WHERE sku IN ('SKU-00296')",
    "columns_swapped": "SELECT quantity_kg, sku FROM transactions WHERE sku = 'SKU-00296'",
    "order_added": (
        "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00296' ORDER BY quantity_kg"
    ),
}
LOWEST_REWRITE = "SELECT sku, quantity_kg FROM inventory ORDER BY 2 DESC LIMIT 5"
RANGE_REWRITE = (
    "SELECT sku, quantity_kg FROM inventory WHERE reorder_level_kg < 10 AND quantity_kg > 300"
)
# The item's must-fail: near misses of a confirmed question, each answered by
# a model that copies the stored SQL unchanged.
DELAYED_NEAR_MISSES = (
    "How many delayed shipments do we have last month?",
    "How many delayed shipments do we have per warehouse?",
    "How many shipments do we have that are delayed?",
    "How many delayed inbound shipments do we have?",
    "How many delayed shipments do we not have?",
)
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
    # 1.5.0 fields (#329) are present and null. verified_query_ids (1.8.0)
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


@pytest.mark.parametrize("value", [None, "", "0", "false", "off", "no", "enabled"])
def test_flag_is_off_by_default_and_only_an_explicit_on_turns_it_on(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """PR Bot 6240552800: the flag stays off by default."""
    if value is None:
        monkeypatch.delenv(vqa.ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(vqa.ENABLED_ENV, value)

    def untouched() -> None:
        raise AssertionError("verified-query library read with the flag off")

    monkeypatch.setattr(vqa, "get_verified_queries", untouched)
    assert vqa.verified_query_enabled() is False
    assert vqa.bind_verified_examples(space_id="alpha", scored_pack_id=None) is None
    assert vqa.generation_examples(space_id="alpha", question=VQ_Q) is None
    monkeypatch.setenv(vqa.ENABLED_ENV, "1")
    assert vqa.verified_query_enabled() is True


def test_no_tracked_file_outside_the_lane_and_its_tests_names_the_flag() -> None:
    """No deploy file, script or other module can switch the lane on.

    Only the lane, the query-skill short-circuit that reads it, tests and
    prose mention the variable.
    """
    import re
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")
    allowed = {"CortexOS/dms/verified_query_ask.py", "packs/dms/semantic/query_skills.py"}
    name = re.compile(re.escape(vqa.ENABLED_ENV) + r"(?![A-Z0-9_])")
    seen: list[str] = []
    for rel in filter(None, listed.split("\0")):
        path = ROOT / rel
        if (
            rel in allowed
            or rel.startswith(("tests/", "docs/"))
            or rel.endswith(".md")
            or not path.is_file()
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if name.search(text):
            seen.append(rel)
    assert seen == []
    for rel in allowed:
        assert name.search((ROOT / rel).read_text(encoding="utf-8")), rel


@pytest.mark.parametrize("value", [None, "0"])
def test_flag_off_ask_is_byte_identical_to_the_ask_without_this_lane(
    ask_http, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """PR Bot 6240552800: flag-off output is main's.

    Main has none of this lane's hooks. With the flag off, the same asks give
    the same bytes whether the hooks run or are replaced by no-ops, with a
    confirmed example in the library for one of the questions.
    """
    import itertools
    import uuid
    from types import SimpleNamespace

    from CortexOS.dms.answer_engine import clear_session

    if value is None:
        monkeypatch.delenv(vqa.ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(vqa.ENABLED_ENV, value)
    ask_http.bind_session("alpha")
    _confirm("alpha")
    questions = ("what is our total revenue", VQ_Q, ASK)

    def ask_bytes(question: str) -> bytes:
        clear_session(SESSION, space_id="alpha")
        counter = itertools.count()
        monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))
        monkeypatch.setattr(
            "CortexOS.execution.drillthrough.time",
            SimpleNamespace(time=lambda: 1_900_000_000.0),
        )
        return _post(ask_http, "alpha", question).content

    with_hooks = [ask_bytes(q) for q in questions]

    def tripwire(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("examples stamped with the flag off")

    monkeypatch.setattr(vqa, "bind_verified_examples", lambda **_kwargs: None)
    monkeypatch.setattr(vqa, "take_verified_examples", lambda _tokens: None)
    monkeypatch.setattr(vqa, "stamp_verified_examples", tripwire)
    without = [ask_bytes(q) for q in questions]
    assert with_hooks == without
    assert any(json.loads(raw)["rows"] for raw in with_hooks), "a served answer is compared"
    assert all(b"verified_query_ids" not in raw for raw in with_hooks)


def test_answer_serialization_omits_empty_verified_query_ids() -> None:
    base = {"answer": "a", "audit_id": "x", "route": "r", "provenance": {"layer": "l", "badge": "session"}}
    assert "verified_query_ids" not in Answer.model_validate(base).model_dump_json()
    assert "verified_query_ids" not in Answer.model_validate(base).model_dump()
    got = Answer.model_validate({**base, "verified_query_ids": ["vq_1", "vq_2"]})
    assert got.model_dump()["verified_query_ids"] == ["vq_1", "vq_2"]
    assert Answer.model_validate_json(got.model_dump_json()).verified_query_ids == ["vq_1", "vq_2"]
    spec = json.loads((ROOT / "contract" / "openapi-1.8.0.json").read_text(encoding="utf-8"))
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
    # The SQL that answered is the model's, never the example's.
    assert "SKU-00109" in body["sql_used"] and "SKU-00296" not in body["sql_used"]
    # The example's SQL runs only as the serve-time result comparison, under
    # the same manifest. Every other statement is the model's SQL.
    executed = [sql for sql, _params in execute_spy]
    example_key = vqa._query_key(vqa._parse(VQ_SQL))
    example_runs = [sql for sql in executed if "SKU-00296" in sql]
    assert example_runs, "the result comparison ran the example"
    assert all(vqa._query_key(vqa._parse(sql)) == example_key for sql in example_runs)
    assert all("SKU-00109" in sql for sql in executed if sql not in example_runs)
    assert "SKU-00296" not in json.dumps(body["rows"])
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


def test_confirmed_question_re_serves_fresh_rows_every_time(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    """Normalised equality on the confirmed question re-serves; each ask executes again."""
    from CortexOS.execution import submit

    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", HIGHEST_Q, HIGHEST_SQL)
    expected = _warehouse_rows(HIGHEST_SQL)
    assert len(expected) == 5
    first = _ask(ask_http, "alpha", "which 5 SKUs have the highest quantity_kg in inventory")
    _assert_served(first)
    assert first["rows"] == expected
    assert first["answer"].startswith("Found 5 row(s).")
    assert first["verified_query_ids"] == [query.id]
    assert first["reused"] is False
    assert len(execute_spy) >= 1

    real = submit.execute_sql

    def moved_on(verified, sql, **kwargs):  # noqa: ANN001
        rows, q_ms, e_ms = real(verified, sql, **kwargs)
        return [{**r, "quantity_kg": float(r.get("quantity_kg") or 0) - 1} for r in rows], q_ms, e_ms

    monkeypatch.setattr(submit, "execute_sql", moved_on)
    second = _ask(ask_http, "alpha", HIGHEST_Q)
    _assert_served(second)
    assert [r["quantity_kg"] for r in second["rows"]] == [
        float(r["quantity_kg"]) - 1 for r in expected
    ], "the re-serve is a new execution, not a stored answer"
    assert second["answer_id"] != first["answer_id"]
    assert second["verified_query_ids"] == [query.id]


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
    # Every statement runs under the grant, the result comparison's included.
    assert all("location_id = 'LOC-003'" in sql for sql in enforced)
    assert any("LOC-004" in sql for sql in enforced)
    # The example's SQL runs only as that comparison. The answer is the model's.
    assert "SKU-00296" not in (body["sql_used"] or "")
    assert all("LOC-004" in sql for sql in enforced if "SKU-00296" not in sql)


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
    query = _confirm("alpha", SKUS_Q, SKUS_SQL)
    for asked in (
        "How many locations do we have in inventory?",
        "How many SKUs do we have in inventory by location?",
    ):
        body = _ask(ask_http, "alpha", asked)
        _assert_echo_abstain(body, query.id, COPY_REASON)


def test_must_fail_copied_example_ignores_a_number_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy drops a qualifier the question adds."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    query = _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    body = _ask(ask_http, "alpha", "How many delayed shipments do we have at LOC-003?")
    _assert_echo_abstain(body, query.id, COPY_REASON)
    body = _ask(ask_http, "alpha", "How many delayed shipments do we have by carrier?")
    _assert_echo_abstain(body, query.id, COPY_REASON)


def test_must_fail_dropped_filter_example_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filter with no value (is_hazardous = true) is kept for a question without it."""
    hazard_q = "List hazardous SKUs in inventory"
    hazard_sql = "SELECT sku FROM inventory WHERE is_hazardous = true"
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", hazard_q, hazard_sql)
    body = _ask(ask_http, "alpha", "List SKUs in inventory")
    _assert_echo_abstain(body, "'hazardous'")
    # A paraphrase that keeps the word still gets the example's SQL unchanged: abstain.
    body = _ask(ask_http, "alpha", "Show hazardous SKUs in inventory")
    _assert_echo_abstain(body, query.id, COPY_REASON)
    same = _ask(ask_http, "alpha", "list hazardous SKUs in inventory.")
    _assert_served(same)
    assert same["rows"] == _warehouse_rows(hazard_sql)
    assert same["verified_query_ids"] == [query.id]


# -- must-fail: no word makes a copy fit another question (review @ 2161c14) --
def test_must_fail_copy_with_added_qualifier_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'inbound' narrows the question. The unchanged copy would serve all 13 rows."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    body = _ask(ask_http, "alpha", "List the sku and quantity_kg of inbound transactions for SKU-00296")
    _assert_echo_abstain(body, query.id, COPY_REASON)


def test_must_fail_reordered_words_are_not_the_confirmed_question(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same words, opposite meaning: word order is part of the confirmed question."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", RANGE_Q, RANGE_SQL)
    asked = (
        "List the sku and quantity_kg of inventory where quantity_kg is below 300 "
        "and reorder_level_kg is above 10"
    )
    assert vqa.lexical_similarity(asked, RANGE_Q) == 1.0
    body = _ask(ask_http, "alpha", asked)
    _assert_echo_abstain(body, query.id, COPY_REASON)


def test_must_fail_copy_for_the_opposite_order_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'lowest' against a confirmed 'highest': the copy would serve the maximum."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", HIGHEST_Q, HIGHEST_SQL)
    body = _ask(ask_http, "alpha", "Which 5 SKUs have the lowest quantity_kg in inventory?")
    _assert_echo_abstain(body, query.id, COPY_REASON)


def test_must_fail_copy_for_a_negated_question_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'not delayed' spells the example's value, so only the copy rule stands here."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    query = _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    delayed = _warehouse_rows(DELAYED_SQL)[0]["delayed_count"]
    body = _ask(ask_http, "alpha", "How many shipments are not delayed?")
    _assert_echo_abstain(body, query.id, COPY_REASON)
    assert f"delayed_count = {delayed}" not in body["answer"]


def test_must_fail_renamed_copy_is_still_a_copy(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy with new output names and a table alias is still the example's SQL."""
    renamed = "SELECT t.sku AS item, t.quantity_kg AS kg FROM transactions t WHERE t.sku = 'SKU-00296'"
    _arm_model(monkeypatch, renamed)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    body = _ask(ask_http, "alpha", "List the sku and quantity_kg of inbound transactions for SKU-00296")
    _assert_echo_abstain(body, query.id, COPY_REASON)


@pytest.mark.parametrize("asked", DELAYED_NEAR_MISSES)
def test_must_fail_near_miss_never_gets_the_stored_sql(
    ask_http, monkeypatch: pytest.MonkeyPatch, asked: str
) -> None:
    """A near miss of the confirmed question is never answered by its SQL unchanged."""
    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    query = _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    assert vqa.lexical_similarity(asked, DELAYED_Q) >= vqa.RETRIEVE_MIN, "the example is retrieved"
    delayed = _warehouse_rows(DELAYED_SQL)[0]["delayed_count"]
    body = _ask(ask_http, "alpha", asked)
    _assert_echo_abstain(body, query.id, COPY_REASON)
    assert f"delayed_count = {delayed}" not in body["answer"]


def test_confirmed_question_re_serves_the_stored_sql_on_fresh_rows(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmed question itself (normalised) re-serves, and each ask executes again."""
    from CortexOS.execution import submit

    _arm_model(monkeypatch, copying)
    ask_http.bind_session("alpha", SHIP_GRANT)
    query = _confirm("alpha", DELAYED_Q, DELAYED_SQL)
    delayed = _warehouse_rows(DELAYED_SQL)[0]["delayed_count"]
    first = _ask(ask_http, "alpha", "  how many DELAYED shipments do we have  ")
    _assert_served(first)
    assert first["rows"] == [{"delayed_count": delayed}]
    assert first["answer"] == f"Result: delayed_count = {delayed}"
    assert first["verified_query_ids"] == [query.id] and first["reused"] is False

    real = submit.execute_sql

    def moved_on(verified, sql, **kwargs):  # noqa: ANN001
        rows, q_ms, e_ms = real(verified, sql, **kwargs)
        return [
            {**r, "delayed_count": r["delayed_count"] + 1} if "delayed_count" in r else r
            for r in rows
        ], q_ms, e_ms

    monkeypatch.setattr(submit, "execute_sql", moved_on)
    second = _ask(ask_http, "alpha", DELAYED_Q)
    _assert_served(second)
    assert second["rows"] == [{"delayed_count": delayed + 1}], "a new execution, not a stored answer"
    assert second["answer"] == f"Result: delayed_count = {delayed + 1}"
    assert second["answer_id"] != first["answer_id"]
    assert second["verified_query_ids"] == [query.id] and second["reused"] is False


# -- must-fail: an equivalent rewrite is still the example (review @ 4bb518b) --
def _not_a_text_copy(question: str, sql: str, example_q: str, example_sql: str) -> None:
    """The SQL rules pass it: only the result comparison can refuse it."""
    seen = [{"id": "vq_probe", "question": example_q, "sql": example_sql}]
    assert vqa.example_echo(question, sql, seen, schema={}) == []


@pytest.mark.parametrize("rewrite", sorted(EQUIVALENT_COPIES))
def test_must_fail_equivalent_copy_for_an_added_qualifier_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch, rewrite: str
) -> None:
    """'inbound' narrows the question. A rewrite of the example returns all 13 rows."""
    sql = EQUIVALENT_COPIES[rewrite]
    _not_a_text_copy(INBOUND_Q, sql, VQ_Q, VQ_SQL)
    assert len(_warehouse_rows(VQ_SQL)) == 13 and len(_warehouse_rows(INBOUND_SQL)) == 3
    _arm_model(monkeypatch, sql)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    body = _ask(ask_http, "alpha", INBOUND_Q)
    _assert_echo_abstain(body, query.id, ROWS_REASON)


def test_must_fail_equivalent_copy_for_the_opposite_order_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'lowest' against a confirmed 'highest': ORDER BY 2 DESC would serve the maximum."""
    _not_a_text_copy(LOWEST_Q, LOWEST_REWRITE, HIGHEST_Q, HIGHEST_SQL)
    _arm_model(monkeypatch, LOWEST_REWRITE)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", HIGHEST_Q, HIGHEST_SQL)
    body = _ask(ask_http, "alpha", LOWEST_Q)
    _assert_echo_abstain(body, query.id, ROWS_REASON)


def test_must_fail_equivalent_copy_for_reordered_words_never_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swapped conjuncts: 641 rows above 300 for a question that asks below 300."""
    _not_a_text_copy(REORDERED_Q, RANGE_REWRITE, RANGE_Q, RANGE_SQL)
    _arm_model(monkeypatch, RANGE_REWRITE)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", RANGE_Q, RANGE_SQL)
    body = _ask(ask_http, "alpha", REORDERED_Q)
    _assert_echo_abstain(body, query.id, ROWS_REASON)


def test_new_sql_for_the_added_qualifier_is_served(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: SQL that returns other rows than the example is served on current data."""
    _arm_model(monkeypatch, INBOUND_SQL)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    body = _ask(ask_http, "alpha", INBOUND_Q)
    _assert_served(body)
    expected = _warehouse_rows(INBOUND_SQL)
    assert len(expected) == 3
    assert body["rows"] == expected
    assert body["answer"].startswith("Found 3 row(s).")
    assert body["verified_query_ids"] == [query.id]


def test_confirmed_question_re_serves_an_equivalent_rewrite(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmed question may get the example's rows, however the SQL is written."""
    _arm_model(monkeypatch, RANGE_REWRITE)
    ask_http.bind_session("alpha", INV_GRANT)
    query = _confirm("alpha", RANGE_Q, RANGE_SQL)
    body = _ask(ask_http, "alpha", RANGE_Q)
    _assert_served(body)
    expected = _warehouse_rows(RANGE_SQL)
    assert len(expected) == 641

    def rows(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return sorted((r["sku"], r["quantity_kg"]) for r in items)

    assert rows(body["rows"]) == rows(expected)
    assert body["answer"].startswith("Found 641 row(s).")
    assert body["verified_query_ids"] == [query.id]


def test_freeroute_is_the_only_model_call_and_it_sees_the_example_sql(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _arm_model(monkeypatch)
    ask_http.bind_session("alpha")
    query = _confirm("alpha")
    # A paraphrase of the confirmed question: the model sees the example, and
    # the SQL it writes is that example unchanged. Only the confirmed question
    # may re-serve it, so this abstains.
    paraphrase = "Show me the sku and quantity_kg for SKU-00296 transactions"
    assert paraphrase.lower() != VQ_Q.lower()
    body = _ask(ask_http, "alpha", paraphrase)
    assert sent and {item["task"] for item in sent} == {"gen-ask-sql"}
    prompt = json.dumps(sent)
    assert query.id in prompt and VQ_SQL in prompt
    _assert_echo_abstain(body, query.id, COPY_REASON)
    # A related question gets new SQL from the model, with the example as context.
    sent.clear()
    other = _ask(ask_http, "alpha", "Show me the sku and quantity_kg for SKU-00109 transactions")
    assert sent and {item["task"] for item in sent} == {"gen-ask-sql"}
    assert query.id in json.dumps(sent) and VQ_SQL in json.dumps(sent)
    _assert_served(other)
    assert other["verified_query_ids"] == [query.id]
    assert other["served_provider"] == _recorded_served()["served_provider"]
    expected = _warehouse_rows(MODEL_SQL)
    assert len(expected) == 6
    assert other["rows"] == expected
    assert other["answer"].startswith("Found 6 row(s).")


def test_must_fail_paraphrase_reaches_certified_example_not_the_phrase_table(
    ask_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[tuple[str, Any]]
) -> None:
    """Paraphrase retrieves the example. The exact phrase is not a table hit.

    Neither question is answered from a phrase table. The confirmed question
    re-serves the example's SQL on current data. The paraphrase reaches the
    example too, but the example's SQL unchanged is not its answer: abstain.
    """
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
        assert body["provenance"]["badge"] != "certified"
        assert body["reused"] is False
        assert execute_spy, "the model's SQL ran"
        assert sent, "the model wrote it"
        assert phrase_calls == [], question
        if question == paraphrase:
            _assert_echo_abstain(body, query.id, COPY_REASON)
            assert f"sku_count = {expected[0]['sku_count']}" not in body["answer"]
            continue
        _assert_served(body)
        assert body["verified_query_ids"] == [query.id]
        assert body["rows"] == expected
        assert body["answer"] == f"Result: sku_count = {expected[0]['sku_count']}"
