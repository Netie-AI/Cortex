"""SCHEMA-CONTEXT-01. Caller schema replaces the pack table list.

Model calls are the FreeRoute ``complete`` seam. No network. Table names in
this file are synthetic. Pack table names are read from the live ranking at
runtime and are not written here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.insights import schema_context as schema_mod
from packs.dms.security.rate_limit import reset_limiter

INTENT = "count qty on the supplied ledger"
SCHEMA = "\n".join(
    [
        "DIALECT: generic",
        "SCHEMA",
        "- alpha_ledger",
        "- qty integer samples=zzsc01alpha",
        "- beta_entry",
        "- ref_code integer",
        "JOINS",
        "- alpha_ledger.id = beta_entry.ref_code (ledger_entry)",
    ]
)
SCHEMA_NO_JOIN = "\n".join(
    [
        "SCHEMA",
        "- alpha_ledger",
        "- qty integer",
        "- beta_entry",
        "- ref_code integer",
    ]
)
SQL_OK = "SELECT qty FROM alpha_ledger WHERE qty > 1"
SQL_SCAN = "SELECT qty FROM alpha_ledger"
SQL_OTHER = "SELECT qty FROM other_ledger WHERE qty > 1"
SQL_JOIN = (
    "SELECT alpha_ledger.qty FROM alpha_ledger "
    "JOIN beta_entry ON alpha_ledger.id = beta_entry.ref_code "
    "WHERE alpha_ledger.qty > 1"
)
HEADERS = {"Authorization": "Bearer ov_schema_ctx_test"}
_FROZEN_UUID = uuid.UUID("0123456789abcdef0123456789abcdef")
# Measured on 279cbd85 with the frozen clock below, usage key absent.
_ABSENT_SHA256 = "6c38d0a89050612baebae9c32e4f77c511d365211d8d7cd4636656b9627c2e95"


def _pack_tables(intent: str) -> set[str]:
    from CortexOS.crew import insights
    from CortexOS.crew.cot_climb import _allowed_tables

    return _allowed_tables(insights.retrieve_ontology(intent))


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


def _complete(prompts: list[tuple[str, str]], sql_for):
    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, kwargs
        prompts.append((purpose, prompt))
        if purpose == "think":
            return {
                "ok": True,
                "text": "use the supplied schema",
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            }
        return {
            "ok": True,
            "text": sql_for(prompt),
            "prompt_tokens": 5,
            "completion_tokens": 4,
            "total_tokens": 9,
        }

    return complete


def _bind(monkeypatch: pytest.MonkeyPatch, complete) -> None:
    from CortexOS.crew import freeroute as fr

    _arm(monkeypatch, armed=True)
    monkeypatch.setattr(fr, "complete", complete)


def _generate(api: TestClient, monkeypatch: pytest.MonkeyPatch, schema: str, sql_for):
    prompts: list[tuple[str, str]] = []
    _bind(monkeypatch, _complete(prompts, sql_for))
    body = _post(
        api,
        {
            "intent": INTENT,
            "ask": False,
            "generate": True,
            "schema_context": schema,
        },
    )
    return body, prompts


def _sql_for_offered(pack: set[str]):
    def sql_for(prompt: str) -> str:
        idents = schema_mod.schema_idents(prompt)
        offered = sorted(idents & pack)
        if "alpha_ledger" in idents and not offered:
            return SQL_OK
        if offered:
            return f"SELECT 1 AS n FROM {offered[0]} WHERE 1 = 1"
        return "SELECT 1"

    return sql_for


def _assert_caller_sql(body: dict[str, Any], prompts: list[tuple[str, str]], pack: set[str]) -> None:
    sql = str(body.get("sql_used") or "")
    idents = schema_mod.schema_idents(sql)
    assert "alpha_ledger" in idents
    assert not (idents & pack)
    assert body["status"] == "ABSTAIN"
    assert body["row_cap"] == schema_mod.SCHEMA_CONTEXT_ROW_CAP
    assert body["truncated"] is False
    assert prompts
    for _purpose, prompt in prompts:
        assert not (schema_mod.schema_idents(prompt) & pack)
        assert "alpha_ledger" in schema_mod.schema_idents(prompt)


def _assert_pack_not_offered(prompts: list[tuple[str, str]], pack: set[str]) -> None:
    assert pack
    assert prompts
    for _purpose, prompt in prompts:
        assert not (schema_mod.schema_idents(prompt) & pack)


def _assert_named_refusal(body: dict[str, Any], code: str, sql: str) -> None:
    assert body["status"] == "REFUSE"
    assert body["answer"] == code
    assert (body.get("generative") or {}).get("refuse_reason") == code
    assert not body.get("sql_used")
    assert not (body.get("generative") or {}).get("sql")
    assert sql not in json.dumps(body)


def _generate_count(prompts: list[tuple[str, str]]) -> int:
    return sum(1 for purpose, _prompt in prompts if purpose == "generative_ask")


def test_must_pass_non_pack_space_sql_names_only_caller_tables(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = _pack_tables(INTENT)
    body, prompts = _generate(api, monkeypatch, SCHEMA, _sql_for_offered(pack))
    _assert_caller_sql(body, prompts, pack)
    assert body["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "total_tokens": 12,
    }


def test_must_fail_pack_table_never_offered(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = _pack_tables(INTENT)
    _body, prompts = _generate(api, monkeypatch, SCHEMA, _sql_for_offered(pack))
    _assert_pack_not_offered(prompts, pack)


def test_must_fail_ungranted_table_refuses_without_retry(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def sql_for(_prompt: str) -> str:
        return SQL_OTHER

    body, prompts = _generate(api, monkeypatch, SCHEMA, sql_for)
    _assert_named_refusal(body, "schema_context:ungranted_table:other_ledger", SQL_OTHER)
    assert _generate_count(prompts) == 1
    assert all(SQL_OTHER not in prompt for _purpose, prompt in prompts)


def test_must_fail_ungranted_join_refuses_without_retry(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def sql_for(_prompt: str) -> str:
        return SQL_JOIN

    body, prompts = _generate(api, monkeypatch, SCHEMA_NO_JOIN, sql_for)
    _assert_named_refusal(
        body, "schema_context:ungranted_join:alpha_ledger+beta_entry", SQL_JOIN
    )
    assert _generate_count(prompts) == 1
    assert all(SQL_JOIN not in prompt for _purpose, prompt in prompts)


def test_must_fail_brute_force_scan_refuses_before_run(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def sql_for(_prompt: str) -> str:
        return SQL_SCAN

    body, prompts = _generate(api, monkeypatch, SCHEMA, sql_for)
    _assert_named_refusal(body, "brute_force_scan", SQL_SCAN)
    assert _generate_count(prompts) == 1


def test_must_fail_over_cap_is_named_422(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    marker = "zzsc01over"
    huge = marker + ("z" * schema_mod.SCHEMA_CONTEXT_MAX_CHARS)
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": False, "schema_context": huge},
    )
    assert body["refuse_reason"] == schema_mod.SCHEMA_CONTEXT_TOO_LARGE
    assert body["usage"] == schema_mod.null_usage()
    assert marker not in json.dumps({k: v for k, v in body.items() if k != "detail"})
    # detail states the length, not the text
    assert marker not in str(body.get("detail") or "")
    for rec in caplog.records:
        if rec.levelno < logging.INFO:
            continue
        assert marker not in rec.getMessage()


def test_must_fail_schema_context_absent_from_info_logs(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    _post(
        api,
        {"intent": INTENT, "ask": False, "generate": False, "schema_context": SCHEMA},
    )
    logged = False
    for rec in caplog.records:
        if rec.levelno < logging.INFO:
            continue
        assert "zzsc01alpha" not in rec.getMessage()
        for arg in rec.args or ():
            assert "zzsc01alpha" not in str(arg)
        if rec.getMessage().startswith("schema_context len="):
            logged = True
            assert "zzsc01alpha" not in rec.getMessage()
    assert logged


def test_must_fail_usage_null_when_provider_omits_counts(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        text = "use the supplied schema" if purpose == "think" else SQL_OK
        return {"ok": True, "text": text}

    _bind(monkeypatch, complete)
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    assert body["status"] == "ABSTAIN"
    assert body["usage"] == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def test_must_fail_usage_keeps_reported_counts_including_zero(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        if purpose == "think":
            return {
                "ok": True,
                "text": "use the supplied schema",
                "prompt_tokens": 0,
                "completion_tokens": 5,
                "total_tokens": 5,
            }
        return {
            "ok": True,
            "text": SQL_OK,
            "prompt_tokens": 8,
            "completion_tokens": 0,
            "total_tokens": 8,
        }

    _bind(monkeypatch, complete)
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    assert body["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 5,
        "total_tokens": 13,
    }


def test_must_fail_usage_sums_retries(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {"gen": 0}

    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        if purpose == "think":
            return {
                "ok": True,
                "text": "use the supplied schema",
                "prompt_tokens": 10 if seen["gen"] == 0 else 30,
                "completion_tokens": 1 if seen["gen"] == 0 else 3,
                "total_tokens": 11 if seen["gen"] == 0 else 33,
            }
        seen["gen"] += 1
        if seen["gen"] == 1:
            return {
                "ok": True,
                "text": "SELECT 1",
                "prompt_tokens": 20,
                "completion_tokens": 2,
                "total_tokens": 22,
            }
        return {
            "ok": True,
            "text": SQL_OK,
            "prompt_tokens": 40,
            "completion_tokens": 4,
            "total_tokens": 44,
        }

    _bind(monkeypatch, complete)
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    assert body["status"] == "ABSTAIN", body.get("answer")
    assert seen["gen"] >= 2
    assert body["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "total_tokens": 110,
    }


def test_must_fail_row_cap_truncates_and_stamps(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import insights as insights_mod

    async def fake_inner(*args, **kwargs):  # noqa: ANN002
        del args, kwargs
        return {
            "ok": True,
            "status": "ABSTAIN",
            "values": [{"n": i} for i in range(5)],
            "answer": "rows",
            "badge": "abstain",
        }

    _arm(monkeypatch, armed=False)
    monkeypatch.setattr(insights_mod, "_run_insights", fake_inner)
    monkeypatch.setenv(schema_mod.ROW_CAP_ENV, "2")
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    assert body["row_cap"] == 2
    assert body["truncated"] is True
    assert body["values"] == [{"n": 0}, {"n": 1}]


def test_non_string_schema_context_is_named_422(api: TestClient) -> None:
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": False, "schema_context": {"raw": 1}},
    )
    assert body["refuse_reason"] == schema_mod.SCHEMA_CONTEXT_INVALID
    assert "raw" not in str(body.get("detail") or "")


def test_parent_fail_absent_schema_still_offers_pack_tables() -> None:
    """279cbd85 builds the SQL prompt from the pack list. That still happens
    when schema_context is absent, so the fallback is the parent path.
    """
    from CortexOS.crew import insights
    from CortexOS.crew.cot_climb import _allowed_tables, _sql_prompt

    ranking = insights.retrieve_ontology(INTENT)
    pack = _allowed_tables(ranking)
    prompt = _sql_prompt(INTENT, ranking)
    assert pack
    assert schema_mod.schema_idents(prompt) & pack


def test_parent_fail_must_pass_fails_when_replacement_removed(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import cot_climb

    pack = _pack_tables(INTENT)
    monkeypatch.setattr(
        cot_climb,
        "_prompt_ontology_lines",
        lambda ranking, schema_context: cot_climb._ontology_lines(ranking),
    )
    body, prompts = _generate(api, monkeypatch, SCHEMA, _sql_for_offered(pack))
    with pytest.raises(AssertionError):
        _assert_caller_sql(body, prompts, pack)


def test_guard_removed_pack_lines_fail_the_offer_check(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import cot_climb

    pack = _pack_tables(INTENT)
    monkeypatch.setattr(
        cot_climb,
        "_prompt_ontology_lines",
        lambda ranking, schema_context: cot_climb._ontology_lines(ranking),
    )
    _body, prompts = _generate(api, monkeypatch, SCHEMA, _sql_for_offered(pack))
    with pytest.raises(AssertionError):
        _assert_pack_not_offered(prompts, pack)


def test_guard_removed_ungranted_check_retries_and_echoes_sql(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_mod, "ungranted_reason", lambda sql, schema: None)

    def sql_for(_prompt: str) -> str:
        return SQL_OTHER

    body, prompts = _generate(api, monkeypatch, SCHEMA, sql_for)
    with pytest.raises(AssertionError):
        _assert_named_refusal(body, "schema_context:ungranted_table:other_ledger", SQL_OTHER)
        assert _generate_count(prompts) == 1


def test_guard_removed_join_check_accepts_undeclared_join(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_mod, "ungranted_reason", lambda sql, schema: None)

    def sql_for(_prompt: str) -> str:
        return SQL_JOIN

    body, prompts = _generate(api, monkeypatch, SCHEMA_NO_JOIN, sql_for)
    with pytest.raises(AssertionError):
        _assert_named_refusal(
            body, "schema_context:ungranted_join:alpha_ledger+beta_entry", SQL_JOIN
        )


def test_guard_removed_brute_force_accepts_full_read(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_mod, "brute_force_reason", lambda sql: None)

    def sql_for(_prompt: str) -> str:
        return SQL_SCAN

    body, prompts = _generate(api, monkeypatch, SCHEMA, sql_for)
    with pytest.raises(AssertionError):
        _assert_named_refusal(body, "brute_force_scan", SQL_SCAN)


def test_guard_removed_row_cap_returns_every_row(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import insights as insights_mod

    async def fake_inner(*args, **kwargs):  # noqa: ANN002
        del args, kwargs
        return {
            "ok": True,
            "status": "ABSTAIN",
            "values": [{"n": i} for i in range(5)],
            "answer": "rows",
            "badge": "abstain",
        }

    _arm(monkeypatch, armed=False)
    monkeypatch.setattr(insights_mod, "_run_insights", fake_inner)
    monkeypatch.setattr(schema_mod, "stamp_row_cap", lambda envelope: envelope)
    monkeypatch.setenv(schema_mod.ROW_CAP_ENV, "2")
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    with pytest.raises(AssertionError):
        assert body.get("truncated") is True
        assert len(body.get("values") or []) == 2


def test_guard_removed_over_cap_is_not_rejected(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_mod, "problem", lambda value: None)
    huge = "z" * (schema_mod.SCHEMA_CONTEXT_MAX_CHARS + 1)
    response = api.post(
        "/v1/insights",
        json={"intent": INTENT, "ask": False, "generate": False, "schema_context": huge},
    )
    with pytest.raises(AssertionError):
        assert response.status_code == 422
        assert response.json()["refuse_reason"] == schema_mod.SCHEMA_CONTEXT_TOO_LARGE


def test_guard_removed_log_writes_the_raw_schema(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    def leak(value: str) -> None:
        logging.getLogger("CortexOS.insights.schema_context").info("schema_context %s", value)

    monkeypatch.setattr(schema_mod, "log_schema_context", leak)
    _post(
        api,
        {"intent": INTENT, "ask": False, "generate": False, "schema_context": SCHEMA},
    )
    with pytest.raises(AssertionError):
        for rec in caplog.records:
            if rec.levelno < logging.INFO:
                continue
            assert "zzsc01alpha" not in rec.getMessage()


def test_guard_removed_usage_fills_missing_counts_with_zero(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = schema_mod.usage_from_calls

    def zero_fill(calls):  # noqa: ANN001
        out = real(calls)
        return {key: 0 if val is None else val for key, val in out.items()}

    monkeypatch.setattr(schema_mod, "usage_from_calls", zero_fill)

    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        text = "use the supplied schema" if purpose == "think" else SQL_OK
        return {"ok": True, "text": text}

    _bind(monkeypatch, complete)
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    with pytest.raises(AssertionError):
        assert body["usage"]["prompt_tokens"] is None
        assert body["usage"]["completion_tokens"] is None
        assert body["usage"]["total_tokens"] is None


def test_guard_removed_usage_keeps_only_the_last_attempt(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = schema_mod.usage_from_calls

    def last_only(calls):  # noqa: ANN001
        seq = [call for call in (calls or []) if isinstance(call, dict)]
        return real(seq[-1:])

    monkeypatch.setattr(schema_mod, "usage_from_calls", last_only)
    seen = {"gen": 0}

    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        if purpose == "think":
            return {
                "ok": True,
                "text": "use the supplied schema",
                "prompt_tokens": 10 if seen["gen"] == 0 else 30,
                "completion_tokens": 1 if seen["gen"] == 0 else 3,
                "total_tokens": 11 if seen["gen"] == 0 else 33,
            }
        seen["gen"] += 1
        if seen["gen"] == 1:
            return {
                "ok": True,
                "text": "SELECT 1",
                "prompt_tokens": 20,
                "completion_tokens": 2,
                "total_tokens": 22,
            }
        return {
            "ok": True,
            "text": SQL_OK,
            "prompt_tokens": 40,
            "completion_tokens": 4,
            "total_tokens": 44,
        }

    _bind(monkeypatch, complete)
    body = _post(
        api,
        {"intent": INTENT, "ask": False, "generate": True, "schema_context": SCHEMA},
    )
    with pytest.raises(AssertionError):
        assert body["usage"]["prompt_tokens"] == 100


def test_absent_schema_context_matches_parent_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Frozen uuid and time. Drop usage and the rest matches 279cbd85."""
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
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
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

    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/insights",
            json={"intent": "count qty on the supplied ledger", "ask": True, "generate": False},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage"] == schema_mod.null_usage()
    assert "row_cap" not in body
    body.pop("usage")
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == _ABSENT_SHA256, digest
