"""C7-05 Crew serve-on-miss gates and parent-value proof."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cortex_contract.execution import Manifest

from CortexOS.crew import insights
from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import (
    get_session_registry,
    reset_session_registry_for_tests,
)

SESSION = "c7-05-l2-serve"
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"


class _MissBridge:
    session_id = SESSION
    space_id = None

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, question: str) -> dict[str, Any]:
        self.asked.append(question)
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
        }


def _bind() -> None:
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    now = datetime.now(timezone.utc)
    get_session_registry().bind(
        VerifiedManifest(
            manifest=Manifest(
                session_id=SESSION,
                org_id="acme",
                pool_id="default",
                issuer_key_id="int-1",
                allowed_paths=["/data/pool/acme/**"],
                row_predicates={"inventory": "1=1"},
                issued_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                signature="not-checked-here",
            ),
            issuer_kid="int-1",
            verified_at=now,
        )
    )


def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.crew import freeroute

    monkeypatch.setattr(
        freeroute,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "vault_live": True,
            "local_keys": True,
            "cloud_keys": False,
            "detail": "test",
            "live_5000_ci": False,
        },
    )


def _complete(sql: str = SQL):
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        return {
            "ok": True,
            "text": sql,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "requested-only", "model": "requested-only"},
            "stamp": {
                "call_id": f"call-{purpose}",
                "task": "crew-insights-sql",
                "requested": "requested-only",
                "served_provider": "vault-provider",
                "served_model": "vault-model",
                "served_local": True,
                "served_reason": "",
            },
        }

    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_session_registry_for_tests()
    yield
    reset_session_registry_for_tests()


@pytest.mark.asyncio
async def test_flag_off_is_byte_identical_for_same_main_path(monkeypatch) -> None:
    _armed(monkeypatch)
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    first = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    monkeypatch.setenv("DMS_L2_ENABLED", "0")
    second = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["status"] == "ABSTAIN"
    assert first["values"] == []


@pytest.mark.asyncio
async def test_parent_wrong_value_now_serves_gated_value_and_real_stamp(
    monkeypatch,
) -> None:
    """On 279cbd85 this reaches the old miss and returns values=[] (wrong value)."""
    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    calls: list[str] = []

    def execute_sql(verified, sql, *, explain_gate=None):  # noqa: ANN001
        del verified
        assert sql == SQL
        assert explain_gate is True
        calls.extend(["manifest_check", "explain"])
        return [{"sku_count": 4}], 0.0, 0.1

    def plausible(question, sql, rows, **kwargs):  # noqa: ANN001
        del question, kwargs
        assert sql == SQL
        assert rows == [{"sku_count": 4}]
        calls.append("plausibility")
        from CortexOS.dms.l2_plausibility import PlausibilityResult

        return PlausibilityResult(ok=True)

    monkeypatch.setattr("CortexOS.execution.submit.execute_sql", execute_sql)
    monkeypatch.setattr("CortexOS.dms.l2_plausibility.assess_plausibility", plausible)
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )

    assert calls == ["manifest_check", "explain", "plausibility"], (
        body["answer"],
        (body.get("generative") or {}).get("sql"),
    )
    assert body["values"] == [{"sku_count": 4}]
    assert body["status"] == "CERTIFIED"
    assert body["layer"] == "generated"
    assert body["badge"] == "L2_VALIDATED"
    assert body["answer_step"] == "l2_plausibility"
    assert "sku_count=4" in body["answer"]
    assert body["model_called"] is True
    assert body["served_provider"] == "vault-provider"
    assert body["served_model"] == "vault-model"
    assert body["served_local"] is True
    assert body["served_provider"] != "requested-only"


@pytest.mark.asyncio
async def test_wrong_columns_abstain_at_named_plan_shape(monkeypatch) -> None:
    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda verified, sql, *, explain_gate=None: ([{"extra": 4}], 0.0, 0.1),
    )
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["answer_step"] == "plan_shape", body["answer"]
    assert "expected columns=['sku_count']" in body["answer"]


@pytest.mark.asyncio
async def test_certified_measure_mismatch_abstains_before_execute(monkeypatch) -> None:
    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    executed: list[str] = []
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda *args, **kwargs: executed.append("called"),
    )
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete("SELECT COUNT(*) AS sku_count FROM inventory"),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert executed == []
    assert body["status"] == "ABSTAIN"
    assert body["answer_step"] == "l2_plan"
    assert "certified_measure_not_used:cq_sku_count" in body["answer"]
