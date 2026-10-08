"""STEP-TRACE-01. Trace of what this insights request actually ran.

No new model call. Table names here are synthetic. Nothing in the trace
builder is selected by a question, table, column, or pack.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.insights import schema_context as schema_mod
from CortexOS.insights import step_trace
from packs.dms.security.rate_limit import reset_limiter

INTENT = "count qty on the supplied ledger"
ASK_INTENT = "how many skus"
SCHEMA = "\n".join(
    [
        "SCHEMA",
        "- alpha_ledger reason=score:9",
        "- qty integer reason=measure",
        "- beta_entry",
        "JOINS",
        "- alpha_ledger.id = beta_entry.ref_code reason=ledger",
    ]
)
SCHEMA_OTHER = "\n".join(
    [
        "SCHEMA",
        "- other_ledger reason=score:9",
        "- alpha_ledger",
    ]
)
SQL_OK = "SELECT qty FROM alpha_ledger WHERE qty > 1"
SQL_EXACT = "SELECT  qty FROM alpha_ledger WHERE  qty > 1\n"
PII = "ada@example.com"
HEADERS = {"Authorization": "Bearer ov_step_trace_test"}
_FROZEN_UUID = uuid.UUID("0123456789abcdef0123456789abcdef")
# Parent (#351 / 279cbd85 probe) digest once usage is removed.
_ABSENT_SHA256 = "6c38d0a89050612baebae9c32e4f77c511d365211d8d7cd4636656b9627c2e95"
_MODEL_KINDS = frozenset({"think", "generate", "retry"})


def _arm(monkeypatch: pytest.MonkeyPatch, *, armed: bool) -> None:
    from CortexOS.crew import freeroute as fr

    body = {
        "ok": armed,
        "armed": armed,
        "vault_live": armed,
        "local_keys": False,
        "cloud_keys": False,
        "detail": "test-armed" if armed else "unarmed",
        "live_5000_ci": False,
    }
    monkeypatch.setattr(fr, "arming", lambda: body)


def _post(api: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = api.post("/v1/insights", json=payload, headers=HEADERS)
    assert response.status_code in {200, 401, 422}, response.text
    return response.json()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "routes.db"))
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.delenv(schema_mod.ROW_CAP_ENV, raising=False)
    reset_limiter(per_minute=600)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def _complete(prompts: list[tuple[str, str]], sql_for, calls: dict[str, int] | None = None):
    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, kwargs
        if calls is not None:
            calls["n"] = calls.get("n", 0) + 1
        prompts.append((purpose, prompt))
        if purpose == "think":
            return {"ok": True, "text": "use the supplied schema", "stamp": {"task": "think"}}
        return {"ok": True, "text": sql_for(prompt), "stamp": {"task": "generative_ask"}}

    return complete


def _bind(monkeypatch: pytest.MonkeyPatch, complete) -> None:
    from CortexOS.crew import freeroute as fr

    _arm(monkeypatch, armed=True)
    monkeypatch.setattr(fr, "complete", complete)


def _generate(
    api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    schema: str,
    sql_for,
    *,
    step_trace_flag: bool | None = True,
    shortlist: Any = None,
    calls: dict[str, int] | None = None,
):
    prompts: list[tuple[str, str]] = []
    _bind(monkeypatch, _complete(prompts, sql_for, calls))
    payload: dict[str, Any] = {
        "intent": INTENT,
        "ask": False,
        "generate": True,
        "schema_context": schema,
    }
    if step_trace_flag is not None:
        payload["step_trace"] = step_trace_flag
    if shortlist is not None:
        payload["shortlist"] = shortlist
    body = _post(api, payload)
    return body, prompts


def _expected_model_kinds(prompts: list[tuple[str, str]]) -> list[str]:
    thinks = 0
    generates = 0
    kinds: list[str] = []
    for purpose, _prompt in prompts:
        if purpose == "think":
            thinks += 1
            kinds.append("think" if thinks == 1 else "retry")
        elif purpose == "generative_ask":
            generates += 1
            kinds.append("generate" if generates == 1 else "retry")
    return kinds


def _assert_execution_steps(body: dict[str, Any], prompts: list[tuple[str, str]]) -> None:
    steps = body["steps"]
    assert steps[0]["kind"] == "shortlist"
    assert steps[0]["n"] == 1
    model = [step["kind"] for step in steps if step["kind"] in _MODEL_KINDS]
    assert model == _expected_model_kinds(prompts)
    generates = sum(1 for purpose, _prompt in prompts if purpose == "generative_ask")
    checks = sum(1 for step in steps if step["kind"] == "check")
    assert checks == generates
    assert all(step["kind"] != "execute" for step in steps)


def _assert_sql_exact(body: dict[str, Any], sql: str) -> None:
    executed = [step for step in body["steps"] if step["kind"] == "execute"]
    assert executed
    for step in executed:
        assert step["sql"] == sql


def _assert_masked(body: dict[str, Any], secret: str) -> None:
    blob = json.dumps(body["steps"])
    assert secret not in blob
    assert "[REDACTED:" in blob


def _assert_capped(body: dict[str, Any], cap: int) -> None:
    executed = [step for step in body["steps"] if step["kind"] == "execute"]
    assert executed
    for step in executed:
        assert step["row_cap"] == cap
        assert step["truncated"] is True
        assert len(step["rows"]) == cap


def _ask_rows(
    api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sql: str,
    rows: list[dict[str, Any]],
    schema: str = SCHEMA,
    shortlist: Any = None,
) -> dict[str, Any]:
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return {
            "ok": True,
            "answer": "listed",
            "badge": "governed_metric",
            "layer": "governed_metric",
            "metric_id": "sku_count",
            "audit_id": "audit-rows",
            "row_count": len(rows),
            "rows": rows,
            "sources": ["inventory"],
            "sql_used": sql,
            "truncated": False,
        }

    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)
    payload: dict[str, Any] = {
        "intent": ASK_INTENT,
        "ask": True,
        "generate": False,
        "schema_context": schema,
        "step_trace": True,
    }
    if shortlist is not None:
        payload["shortlist"] = shortlist
    return _post(api, payload)


def _parent_digest(body: dict[str, Any]) -> str:
    assert "steps" not in body
    assert body["usage"] == schema_mod.null_usage()
    clone = dict(body)
    clone.pop("usage")
    raw = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()


def test_must_fail_steps_match_what_ran(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body, prompts = _generate(api, monkeypatch, SCHEMA, lambda _prompt: SQL_OK)
    _assert_execution_steps(body, prompts)

    seen = {"n": 0}

    def sql_for(_prompt: str) -> str:
        seen["n"] += 1
        return "SELECT 1" if seen["n"] == 1 else SQL_OK

    retried, prompts = _generate(api, monkeypatch, SCHEMA, sql_for)
    _assert_execution_steps(retried, prompts)
    assert "retry" in [step["kind"] for step in retried["steps"]]


def test_guard_removed_dropped_check_fails_the_run(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(step_trace, "note_check", lambda **kwargs: None)
    body, prompts = _generate(api, monkeypatch, SCHEMA, lambda _prompt: SQL_OK)
    with pytest.raises(AssertionError):
        _assert_execution_steps(body, prompts)


def test_must_fail_shown_sql_is_the_executed_string(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _ask_rows(api, monkeypatch, sql=SQL_EXACT, rows=[{"sku_count": 1}])
    _assert_sql_exact(body, SQL_EXACT)


def test_guard_removed_reformatted_sql_fails(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = step_trace.note_execute

    def rewrite(sql: str, rows: Any, **kwargs: Any) -> None:
        real(" ".join(sql.split()), rows, **kwargs)

    monkeypatch.setattr(step_trace, "note_execute", rewrite)
    body = _ask_rows(api, monkeypatch, sql=SQL_EXACT, rows=[{"sku_count": 1}])
    with pytest.raises(AssertionError):
        _assert_sql_exact(body, SQL_EXACT)


def test_must_fail_row_sample_masks_pii(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _ask_rows(
        api,
        monkeypatch,
        sql=SQL_EXACT,
        rows=[{"sku_count": 1, "note": PII}],
    )
    _assert_masked(body, PII)


def test_guard_removed_unmasked_pii_fails(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(step_trace, "mask_rows", lambda rows: rows)
    body = _ask_rows(
        api,
        monkeypatch,
        sql=SQL_EXACT,
        rows=[{"sku_count": 1, "note": PII}],
    )
    with pytest.raises(AssertionError):
        _assert_masked(body, PII)


def test_must_fail_row_sample_is_capped(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(schema_mod.ROW_CAP_ENV, "2")
    rows = [{"sku_count": i} for i in range(5)]
    body = _ask_rows(api, monkeypatch, sql=SQL_OK, rows=rows)
    _assert_capped(body, 2)


def test_guard_removed_uncapped_rows_fail(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(schema_mod.ROW_CAP_ENV, "2")
    monkeypatch.setattr(
        step_trace,
        "sample_rows",
        lambda rows: (list(rows), schema_mod.row_cap(), False),
    )
    rows = [{"sku_count": i} for i in range(5)]
    body = _ask_rows(api, monkeypatch, sql=SQL_OK, rows=rows)
    with pytest.raises(AssertionError):
        _assert_capped(body, 2)


def test_must_fail_trace_adds_no_model_call(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, int] = {"n": 0}

    def sql_for(_prompt: str) -> str:
        return SQL_OK

    _bind(monkeypatch, _complete([], sql_for, calls))
    calls["n"] = 0
    off = _post(
        api,
        {
            "intent": INTENT,
            "ask": False,
            "generate": True,
            "schema_context": SCHEMA,
        },
    )
    off_n = calls["n"]
    calls["n"] = 0
    on = _post(
        api,
        {
            "intent": INTENT,
            "ask": False,
            "generate": True,
            "schema_context": SCHEMA,
            "step_trace": True,
        },
    )
    assert "steps" not in off
    assert on["steps"]
    assert calls["n"] == off_n
    assert off_n > 0


def test_guard_removed_trace_counts_an_extra_model_call(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, int] = {"n": 0}
    real = step_trace.note_model

    def note_and_call(purpose: str, out: dict[str, Any]) -> None:
        # The trace seat is invoked on every complete(). Counting only while
        # a trace is open is the extra call the off path does not make.
        if step_trace.active():
            calls["n"] += 1
        real(purpose, out)

    monkeypatch.setattr(step_trace, "note_model", note_and_call)
    _bind(monkeypatch, _complete([], lambda _prompt: SQL_OK, calls))
    calls["n"] = 0
    _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    off_n = calls["n"]
    calls["n"] = 0
    _post(
        api,
        {
            "intent": INTENT,
            "ask": False,
            "generate": True,
            "schema_context": SCHEMA,
            "step_trace": True,
        },
    )
    with pytest.raises(AssertionError):
        assert calls["n"] == off_n


def test_must_fail_step1_matches_shortlist_not_a_foreign_table(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    shortlist = [
        {
            "table": "alpha_ledger",
            "reason": "score:2",
            "columns": [{"name": "qty", "reason": "kept"}],
            "joins": [
                {
                    "left": "alpha_ledger",
                    "right": "beta_entry",
                    "on": "alpha_ledger.id = beta_entry.ref_code",
                    "reason": "kept-join",
                }
            ],
        },
        {"table": "beta_entry", "reason": "score:1", "columns": [], "joins": []},
    ]
    body, _prompts = _generate(
        api,
        monkeypatch,
        SCHEMA_OTHER,
        lambda _prompt: SQL_OK,
        shortlist=shortlist,
    )
    step = body["steps"][0]
    assert step["shortlist"] == [
        {
            "table": "alpha_ledger",
            "reason": "score:2",
            "columns": [{"name": "qty", "reason": "kept"}],
            "joins": [
                {
                    "left": "alpha_ledger",
                    "right": "beta_entry",
                    "on": "alpha_ledger.id = beta_entry.ref_code",
                    "reason": "kept-join",
                }
            ],
        },
        {
            "table": "beta_entry",
            "reason": "score:1",
            "columns": [],
            "joins": [],
        },
    ]
    assert "other_ledger" not in json.dumps(step)
    assert "score:9" not in json.dumps(step)

    only_schema, _prompts = _generate(
        api, monkeypatch, SCHEMA, lambda _prompt: SQL_OK
    )
    listed = only_schema["steps"][0]
    assert listed["shortlist"] == [
        {"table": "alpha_ledger", "reason": "", "columns": [], "joins": []},
        {"table": "beta_entry", "reason": "", "columns": [], "joins": []},
    ]
    blob = json.dumps(listed)
    assert "score:9" not in blob
    assert "qty" not in blob
    assert "reason=" not in blob


def test_guard_removed_foreign_table_appears(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = step_trace.shortlist_picks

    def extra(shortlist: Any, schema_text: str | None) -> list[dict[str, Any]]:
        picks = real(shortlist, schema_text)
        picks.append(
            {"table": "not_sent", "reason": "score:9", "columns": [], "joins": []}
        )
        return picks

    monkeypatch.setattr(step_trace, "shortlist_picks", extra)
    body, _prompts = _generate(
        api,
        monkeypatch,
        SCHEMA,
        lambda _prompt: SQL_OK,
        shortlist=[{"table": "alpha_ledger", "reason": "score:2", "columns": [], "joins": []}],
    )
    with pytest.raises(AssertionError):
        assert body["steps"][0]["shortlist"] == [
            {
                "table": "alpha_ledger",
                "reason": "score:2",
                "columns": [],
                "joins": [],
            }
        ]
        assert "not_sent" not in json.dumps(body["steps"][0])


def test_must_fail_null_reason_stays_empty(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    shortlist = [
        {
            "table": "alpha_ledger",
            "reason": None,
            "columns": [{"name": "qty", "reason": None}],
            "joins": [
                {
                    "left": "alpha_ledger",
                    "right": "beta_entry",
                    "on": "alpha_ledger.id = beta_entry.ref_code",
                    "reason": None,
                }
            ],
        }
    ]
    body, _prompts = _generate(
        api,
        monkeypatch,
        SCHEMA,
        lambda _prompt: SQL_OK,
        shortlist=shortlist,
    )
    pick = body["steps"][0]["shortlist"][0]
    assert pick["reason"] == ""
    assert pick["columns"][0]["reason"] == ""
    assert pick["joins"][0]["reason"] == ""
    assert "score:9" not in json.dumps(body["steps"][0])


def test_guard_removed_null_reason_is_invented(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invented(value: Any) -> str:
        if value is None:
            return "invented"
        return value if isinstance(value, str) else ""

    monkeypatch.setattr(step_trace, "empty_reason", invented)
    body, _prompts = _generate(
        api,
        monkeypatch,
        SCHEMA,
        lambda _prompt: SQL_OK,
        shortlist=[
            {
                "table": "alpha_ledger",
                "reason": None,
                "columns": [{"name": "qty", "reason": None}],
                "joins": [
                    {
                        "left": "alpha_ledger",
                        "right": "beta_entry",
                        "on": "id",
                        "reason": None,
                    }
                ],
            }
        ],
    )
    with pytest.raises(AssertionError):
        pick = body["steps"][0]["shortlist"][0]
        assert pick["reason"] == ""
        assert pick["columns"][0]["reason"] == ""
        assert pick["joins"][0]["reason"] == ""


def _freeze_parent(monkeypatch: pytest.MonkeyPatch, tmp_path, ops_name: str) -> None:
    """Same clock and scoreboard path the parent digest was measured with."""
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return {
            "ok": True,
            "answer": "There are 12 rows.",
            "badge": "governed_metric",
            "layer": "governed_metric",
            "metric_id": "row_count",
            "audit_id": "audit-rows",
            "row_count": 1,
            "rows": [{"row_count": 12}],
            "sources": ["alpha_ledger"],
            "sql_used": "SELECT 1 AS row_count",
            "truncated": False,
        }

    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / ops_name))
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", "/tmp/schema-context-01-byte-probe.db")
    monkeypatch.setattr(uuid, "uuid4", lambda: _FROZEN_UUID)
    monkeypatch.setattr(time, "time", lambda: 1700000000.0)

    def _now(tz=None):  # noqa: ANN001
        return datetime(2023, 11, 14, 22, 13, 20, tzinfo=tz)

    try:
        monkeypatch.setattr(datetime, "now", _now)
    except (TypeError, AttributeError):
        pass
    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)
    reset_limiter(per_minute=240)
    probe_db = "/tmp/schema-context-01-byte-probe.db"
    if os.path.exists(probe_db):
        os.remove(probe_db)


def test_must_fail_absent_trace_matches_parent_bytes(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Frozen clock. No trace ask, and a trace ask with no schema, match the parent."""
    from CortexOS.api.app import create_app

    _freeze_parent(monkeypatch, tmp_path, "ops.db")
    with TestClient(create_app()) as client:
        plain = client.post(
            "/v1/insights",
            json={"intent": "count qty on the supplied ledger", "ask": True, "generate": False},
        )
    assert plain.status_code == 200, plain.text
    assert _parent_digest(plain.json()) == _ABSENT_SHA256

    _freeze_parent(monkeypatch, tmp_path, "ops-ask.db")
    with TestClient(create_app()) as client:
        asked = client.post(
            "/v1/insights",
            json={
                "intent": "count qty on the supplied ledger",
                "ask": True,
                "generate": False,
                "step_trace": True,
            },
        )
    assert asked.status_code == 200, asked.text
    assert _parent_digest(asked.json()) == _ABSENT_SHA256

    held = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": False, "schema_context": SCHEMA},
    )
    assert "steps" not in held


def test_guard_removed_attach_writes_steps_on_the_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def always(out: dict[str, Any]) -> None:
        out["steps"] = []

    monkeypatch.setattr(step_trace, "attach", always)
    _freeze_parent(monkeypatch, tmp_path, "ops-guard.db")
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/insights",
            json={"intent": "count qty on the supplied ledger", "ask": True, "generate": False},
        )
    assert response.status_code == 200, response.text
    with pytest.raises(AssertionError):
        assert _parent_digest(response.json()) == _ABSENT_SHA256


def test_contract_15_adds_optional_steps_without_a_bump() -> None:
    from pathlib import Path

    spec = json.loads(
        (Path(__file__).resolve().parents[2] / "contract" / "openapi-1.5.0.json").read_text(
            encoding="utf-8"
        )
    )
    assert spec["info"]["version"] == "1.5.0"
    schemas = spec["components"]["schemas"]
    assert "dms_payload" in schemas["ContractAskRequest"]["properties"]
    ctx = schemas["ContractInsightsSchemaContext"]["properties"]["schema_context"]
    assert "reason=" in ctx["description"]
    trace = schemas["ContractInsightsTrace"]
    assert "steps" in trace["properties"]
    assert "steps" not in (trace.get("required") or [])
    request = schemas["ContractInsightsTraceRequest"]["properties"]["step_trace"]
    assert "step_trace" not in (
        schemas["ContractInsightsTraceRequest"].get("required") or []
    )
    assert "boolean" in json.dumps(request)
    op = spec["paths"]["/v1/insights"]["post"]
    blob = json.dumps(op)
    assert "ContractInsightsTrace" in blob
    assert "ContractInsightsTraceRequest" in blob
    assert "ContractInsightsUsage" in blob
