"""Cortex #276 SERVED-PASSTHROUGH. Real POST /v1/insights, stubbed vault.

served_* on the answer copy the RouteStamp of the call whose SQL was served.
They are never filled from the requested model, the pin, or route.model.
A ranking answer with no freeroute.complete() call leaves them empty.
These tests fail on 27f79ea8 (stamp_api overwrites the vault stamp).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packs.dms.security.rate_limit import reset_limiter
from tests.freeroute_local_fake import LocalFakeOpenVault

ROOT = Path(__file__).resolve().parents[2]
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
BEARER = "ov_callerkeyxx"
PIN = "deepseek-v4-pro"
VAULT_PROVIDER = "ov-served-provider"
VAULT_MODEL = "ov-served-model"
THINK_PROVIDER = "think-not-served"
THINK_MODEL = "think-not-served-model"
NO_MODEL_CALLED = "no model called"

# #272 comment 5829081052. Names must stay in tests/freeroute_local/.
_CORTEX_272 = (
    "test_local_only_drops_cloud_served_answer_text",
    "test_local_spendable_hop_arms_without_pooled_cloud_keys",
    "test_served_fields_come_from_response_not_requested_model",
    "test_local_only_unavailable_local_unreachable",
    "test_local_only_unavailable_local_model_not_loaded",
    "test_local_only_unavailable_local_base_url_not_loopback",
    "test_local_only_sealed_is_403_openvault_vault_sealed",
    "test_served_local_and_local_only_require_json_boolean_true",
    "test_router_fingerprint_equals_route_stamp",
    "test_router_fingerprint_empty_stamp_stays_null_false_plus_reason",
    "test_insights_stamp_plan_source_equals_route_stamp",
)


def _certified_engine(**extra: Any) -> dict[str, Any]:
    body = {
        "ok": True,
        "answer": "There are 12 skus.",
        "badge": "governed_metric",
        "layer": "governed_metric",
        "metric_id": "sku_count",
        "audit_id": "audit-sku",
        "row_count": 1,
        "rows": [{"sku_count": 12}],
        "sources": ["inventory"],
        "sql_used": SQL,
        "truncated": False,
    }
    body.update(extra)
    return body


def _abstain_engine() -> dict[str, Any]:
    return {
        "ok": True,
        "answer": "Engine abstained. No number invented.",
        "badge": "abstain",
        "layer": "l1",
        "metric_id": "sku_count",
        "audit_id": None,
        "row_count": 0,
        "rows": [],
        "sources": ["inventory"],
        "sql_used": None,
        "truncated": False,
    }


@pytest.fixture()
def local_vault(monkeypatch):
    """Scripted OpenVault with served_* on the chat body. Does not edit conftest."""
    from CortexOS.integrations import freeroute, openvault_client

    fake = LocalFakeOpenVault()
    fake.identities[BEARER] = "caller-276"
    for name in ("OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_OPENVAULT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(openvault_client, "request_json", fake)
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        lambda **kwargs: {"ok": True, "allowed": True, "reasons": []},
    )
    monkeypatch.delenv("CORTEX_FREEROUTE", raising=False)
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("CORTEX_FREEROUTE_MODELS", PIN)
    real_connect = socket.socket.connect

    def _guarded(self, address):  # noqa: ANN001
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port in (5000, 11434):
            raise AssertionError(f"test reached a live service on port {port}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded)
    freeroute.reset()
    return fake


def _queue(
    fake: LocalFakeOpenVault,
    content: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    local: Any = None,
    include_local: bool = False,
) -> None:
    kwargs: dict[str, Any] = {}
    if provider is not None:
        kwargs["served_provider"] = provider
    if model is not None:
        kwargs["served_model"] = model
    fake.reply(content, **kwargs)
    if include_local:
        code, body = fake.replies[-1]
        patched = dict(body or {})
        patched["served_local"] = local
        fake.replies[-1] = (code, patched)


def _queue_think_then_sql(
    fake: LocalFakeOpenVault,
    *,
    provider: str | None = VAULT_PROVIDER,
    model: str | None = VAULT_MODEL,
    local: Any = True,
    include_local: bool = True,
) -> None:
    _queue(
        fake,
        "inventory is the ranked table",
        provider=THINK_PROVIDER,
        model=THINK_MODEL,
        local=False,
        include_local=True,
    )
    for _ in range(4):
        _queue(
            fake,
            SQL,
            provider=provider,
            model=model,
            local=local,
            include_local=include_local,
        )


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def _post(api: TestClient, payload: dict[str, Any], *, bearer: str | None = None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    res = api.post("/v1/insights", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _patch_bridge(monkeypatch, engine: dict[str, Any]) -> None:
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return dict(engine)

    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)


def _served(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "served_provider": body.get("served_provider"),
        "served_model": body.get("served_model"),
        "served_local": body.get("served_local"),
        "served_reason": body.get("served_reason"),
    }


def test_ranking_no_model_call_leaves_served_empty(api, monkeypatch) -> None:
    """Case a. Ranking certified the number. No freeroute.complete() call."""
    _patch_bridge(monkeypatch, _certified_engine())
    body = _post(api, {"intent": "how many skus", "ask": True, "generate": False})
    assert body["status"] == "CERTIFIED"
    assert body["values"] == [{"sku_count": 12}]
    assert "12" in (body.get("answer") or "")
    assert body["model_called"] is False
    assert body["plan_source"] == "other"
    served = _served(body)
    assert served["served_provider"] is None
    assert served["served_model"] is None
    assert served["served_local"] is False
    assert served["served_reason"] == NO_MODEL_CALLED
    dumped = json.dumps(served)
    assert PIN not in dumped
    assert "gpt-oss" not in dumped


def test_model_answer_keeps_vault_served_fields(api, local_vault) -> None:
    """Case b. The SQL call's vault stamp reaches the response unchanged."""
    _queue_think_then_sql(local_vault, local=True, include_local=True)
    body = _post(
        api,
        {"intent": "how many skus", "ask": False, "generate": True},
        bearer=BEARER,
    )
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["model_called"] is True
    assert body["plan_source"] == "ontology_plan"
    assert "inventory" in (body.get("query_sql") or "").lower()
    stamp = (body.get("generative") or {}).get("stamp") or {}
    assert stamp.get("task") == "crew-insights-sql"
    assert body["served_provider"] == VAULT_PROVIDER
    assert body["served_model"] == VAULT_MODEL
    assert body["served_local"] is True
    assert body["served_provider"] == stamp.get("served_provider")
    assert body["served_model"] == stamp.get("served_model")
    assert body["served_local"] is stamp.get("served_local")
    # A model answer with empty served_* fails here.
    assert body["served_provider"]
    assert body["served_model"]
    served = _served(body)
    dumped = json.dumps(served)
    assert THINK_PROVIDER not in dumped
    assert THINK_MODEL not in dumped
    assert PIN not in dumped
    assert "gpt-oss" not in dumped
    assert "deepseek" not in dumped
    assert len(local_vault.chat_calls) >= 2


def test_missing_vault_served_value_stays_null(api, local_vault) -> None:
    """A field the vault omitted stays null. Nothing is filled in."""
    _queue_think_then_sql(
        local_vault,
        provider=VAULT_PROVIDER,
        model=None,
        include_local=False,
    )
    body = _post(
        api,
        {"intent": "how many skus", "ask": False, "generate": True},
        bearer=BEARER,
    )
    assert body["model_called"] is True
    assert body["status"] == "ABSTAIN"
    assert body["plan_source"] == "ontology_plan"
    assert body["served_provider"] == VAULT_PROVIDER
    assert body["served_model"] is None
    assert body["served_local"] is False
    reason = str(body.get("served_reason") or "")
    assert "omitted served_model" in reason
    assert "omitted served_local" in reason
    dumped = json.dumps(_served(body))
    assert PIN not in dumped
    assert "gpt-oss" not in dumped
    assert VAULT_MODEL not in dumped


@pytest.mark.parametrize(
    ("raw_local", "expect_local", "reason_bit"),
    [
        (True, True, ""),
        ("true", False, "not true"),
        (1, False, "not true"),
        (None, False, "omitted served_local"),
    ],
)
def test_served_local_true_only_for_json_boolean(
    api, local_vault, raw_local: Any, expect_local: bool, reason_bit: str
) -> None:
    """#272: served_local is true only for JSON boolean true."""
    _queue_think_then_sql(
        local_vault,
        provider=VAULT_PROVIDER,
        model=VAULT_MODEL,
        local=raw_local,
        include_local=raw_local is not None,
    )
    body = _post(
        api,
        {"intent": "how many skus", "ask": False, "generate": True},
        bearer=BEARER,
    )
    assert body["model_called"] is True
    assert body["served_provider"] == VAULT_PROVIDER
    assert body["served_model"] == VAULT_MODEL
    assert body["served_local"] is expect_local
    if reason_bit:
        assert reason_bit in str(body.get("served_reason") or "")
    else:
        assert body.get("served_reason") in (None, "")


@pytest.mark.parametrize(
    ("engine", "status"),
    [
        ("certified", "CERTIFIED"),
        ("abstain", "ABSTAIN"),
    ],
)
def test_model_called_but_ranking_or_abstain_answered(
    api, local_vault, monkeypatch, engine: str, status: str
) -> None:
    """Case c. complete() ran, but ranking or abstain served the answer."""
    _patch_bridge(
        monkeypatch,
        _certified_engine() if engine == "certified" else _abstain_engine(),
    )
    _queue_think_then_sql(local_vault, local=True, include_local=True)
    body = _post(
        api,
        {"intent": "how many skus", "ask": True, "generate": True},
        bearer=BEARER,
    )
    assert body["status"] == status
    assert body["model_called"] is True
    assert body["plan_source"] == "other"
    assert "query_sql" not in body
    assert body["served_provider"] is None
    assert body["served_model"] is None
    assert body["served_local"] is False
    reason = str(body.get("served_reason") or "")
    assert "model call rejected" in reason
    assert "ranking or abstain answered" in reason
    assert "crew-insights-sql" in reason
    dumped = json.dumps(_served(body))
    assert VAULT_PROVIDER not in dumped
    assert VAULT_MODEL not in dumped
    assert THINK_PROVIDER not in dumped
    assert PIN not in dumped
    if status == "CERTIFIED":
        assert body["values"] == [{"sku_count": 12}]
        assert "12" in (body.get("answer") or "")
    else:
        assert body["values"] == []


def test_refuse_without_a_model_keeps_empty_fingerprint(api) -> None:
    """No-model REFUSE still uses the empty-stamp fingerprint. Not a guess."""
    from CortexOS.integrations import freeroute as fr

    none_fp = fr.router_fingerprint()
    assert none_fp["served_provider"] is None
    assert none_fp["served_model"] is None
    assert none_fp["served_local"] is False
    assert none_fp["served_reason"] == fr.SERVED_PENDING_272
    body = _post(api, {"intent": "what is our ARR", "ask": True})
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert body["plan_source"] == "other"
    assert body.get("model_called") is False
    assert body["served_provider"] is None
    assert body["served_model"] is None
    assert body["served_local"] is False
    assert body["served_reason"] == fr.SERVED_PENDING_272


def test_generate_path_still_calls_validate_sql(api, local_vault, monkeypatch) -> None:
    """Engine-pack path still calls crew freeroute.validate_sql. Unchanged."""
    from CortexOS.crew import freeroute as fr

    seen: list[str] = []
    real = fr.validate_sql

    def wrapped(sql: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(sql or "")
        return real(sql, *args, **kwargs)

    monkeypatch.setattr(fr, "validate_sql", wrapped)
    _queue_think_then_sql(local_vault, local=True, include_local=True)
    body = _post(
        api,
        {"intent": "how many skus", "ask": False, "generate": True},
        bearer=BEARER,
    )
    assert body["status"] == "ABSTAIN"
    assert body["plan_source"] == "ontology_plan"
    assert seen, "generate path skipped fr.validate_sql"
    assert any("inventory" in sql.lower() for sql in seen)
    assert body["served_provider"] == VAULT_PROVIDER


def test_272_names_and_273_refusal_file_and_guard_bans_stay() -> None:
    """Must-stay files. This test reads them; it does not edit them."""
    local = (ROOT / "tests/freeroute_local/test_freeroute_local.py").read_text(encoding="utf-8")
    for name in _CORTEX_272:
        assert f"def {name}" in local
    refusal = (ROOT / "tests/test_freeroute_router1.py").read_text(encoding="utf-8")
    assert "def test_router_fingerprint_never_infers_served_from_requested" in refusal
    assert "def test_refusal_tests_have_no_skip_or_xfail_markers" in refusal
    cot = (ROOT / "tests/test_crew/test_cot_climb.py").read_text(encoding="utf-8")
    harness = (ROOT / "tests/test_crew/test_prompt_harness_climb.py").read_text(encoding="utf-8")
    for banned in (
        "CortexOS/crew/freeroute.py",
        "tests/conftest.py",
        "tests/freeroute_fake.py",
        "tests/test_crew/test_freeroute.py",
    ):
        assert banned in cot
    for banned in (
        "CortexOS/crew/freeroute.py",
        "packages/cortex_contract/execution.py",
    ):
        assert banned in harness
