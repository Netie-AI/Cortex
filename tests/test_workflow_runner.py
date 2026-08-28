"""Cortex workflow runner + recognizer + OOM gate tests (stubbed LLM)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from CortexOS.execution import workflow_oom, workflow_runner, workflow_store, workflow_templates
from CortexOS.execution.dag_runner import NodeResult
from CortexOS.execution.workflow_recognizer import recognize
from CortexOS.fabrication.dsl_parser import NodeType


async def _stub_execute_node(node, context, router, ledger, workflow_cost_ceiling_myr=None):
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    if node.type == NodeType.AGENT_TASK:
        data = {
            "findings": [
                {
                    "title": "jank",
                    "file": "a.py",
                    "line": 1,
                    "severity": "high",
                    "est_ms": 5,
                    "claim": "x",
                    "evidence": "y",
                    "url": "u",
                }
            ],
            "angles": [
                {
                    "id": "angle-a",
                    "question": "what is it",
                    "why": "basics",
                    "search_terms": ["x"],
                }
            ],
        }
        tel = {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "tool_count": 1,
            "elapsed_ms": 40,
            "model": "stub",
            "tier": "T1",
            "cost_myr": 0.01,
            "steps": 1,
            "tools": [{"tool": "web_search", "ok": True, "ms": 5, "summary": "1 hit"}],
            "label": ann.get("label"),
            "purpose": ann.get("purpose"),
        }
        return NodeResult(
            node_id=node.id,
            output={"content": '{"ok":true}', "data": data, "telemetry": tel},
            tier="agent",
            cost_myr=0.01,
        )
    if node.type == NodeType.EMIT:
        merged = {i: context.get(i) for i in node.inputs}
        return NodeResult(node_id=node.id, output=merged, tier="emit", cost_myr=0.0)
    if node.type == NodeType.DOCUMENT_REF:
        key = node.context_key or "prompt"
        return NodeResult(
            node_id=node.id,
            output=context.get(key),
            tier="deterministic",
            cost_myr=0.0,
        )
    return NodeResult(node_id=node.id, output={}, tier="deterministic", cost_myr=0.0)


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    db = tmp_path / "wf.db"
    monkeypatch.setattr(workflow_store, "DB_PATH", db)
    workflow_store.init()
    yield


def test_catalog_has_smoothness_and_research():
    ids = {w["id"] for w in workflow_runner.list_workflows()}
    assert "smoothness_audit" in ids
    assert "deep_research" in ids
    assert "recon_decompose" in ids
    assert "adversarial_verify" in ids
    assert "ticket_triage" in ids
    assert "build_and_verify" in ids


def test_recognize_recon_decompose():
    rec = recognize("recon decompose into buildable tickets for Cortex")
    assert rec.template_id == "recon_decompose"
    assert rec.as_dict()["should_run"] is True


def test_recognize_adversarial_verify():
    rec = recognize("adversarially verify this paper against the tree")
    assert rec.template_id == "adversarial_verify"
    assert rec.as_dict()["should_run"] is True


def test_recognize_explicit_fanout():
    rec = recognize("invoke subagents to research blake2b embeddings")
    assert rec.template_id == "deep_research"
    assert rec.explicit
    assert rec.as_dict()["should_run"] is True


def test_recognize_suppress():
    rec = recognize("quick question: what is 2+2 briefly")
    assert rec.template_id is None
    assert rec.as_dict()["should_run"] is False


def test_recognize_smoothness():
    rec = recognize("this UI feels janky — performance audit the animations")
    assert rec.template_id == "smoothness_audit"
    assert rec.as_dict()["should_run"] is True


def test_oom_gate_shrinks_on_low_vram():
    g = workflow_oom.resolve_max_parallel(
        {"vram_gb": 4, "nvidia": {"present": True}}, requested=4
    )
    assert g["max_parallel"] <= 2
    assert g["spill_high_effort_to_cloud"] is True


def test_oom_gate_cpu_caps_at_two():
    g = workflow_oom.resolve_max_parallel({"vram_gb": 0, "ram_gb": 16}, requested=4)
    assert g["max_parallel"] <= 2


def _wait_done(run_id: str, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = workflow_runner.get_task(run_id)
        if task and task["status"] not in ("running", "queued"):
            return task
        time.sleep(0.1)
    return workflow_runner.get_task(run_id)


def test_smoothness_audit_runs_all_phases():
    with patch("CortexOS.execution.dag_runner.execute_node", new=_stub_execute_node):
        with patch(
            "CortexOS.execution.workflow_openvault.ensure_provider_keys",
            return_value={"ok": True},
        ):
            with patch(
                "CortexOS.execution.workflow_openvault.check_openfree_budget",
                return_value={"ok": True},
            ):
                res = workflow_runner.start(
                    "smoothness_audit",
                    target="D:/AirGPT",
                    cost_ceiling_myr=2.0,
                    hardware={"vram_gb": 24, "nvidia": {"present": True}},
                )
                assert res["ok"] is True
                task = _wait_done(res["run_id"])
    assert task is not None
    assert task["status"] == "done"
    assert task["totals"]["phases_done"] == task["totals"]["phases_total"]
    assert task["totals"]["tokens"] > 0
    assert len(task["phases"][0]["agents"]) >= 4


def test_cancel_mid_run():
    async def slow_node(node, context, router, ledger, workflow_cost_ceiling_myr=None):
        if node.type == NodeType.AGENT_TASK:
            time.sleep(0.35)
        return await _stub_execute_node(
            node, context, router, ledger, workflow_cost_ceiling_myr
        )

    with patch("CortexOS.execution.dag_runner.execute_node", new=slow_node):
        with patch(
            "CortexOS.execution.workflow_openvault.ensure_provider_keys",
            return_value={"ok": True},
        ):
            with patch(
                "CortexOS.execution.workflow_openvault.check_openfree_budget",
                return_value={"ok": True},
            ):
                res = workflow_runner.start("smoothness_audit", target="x", cost_ceiling_myr=2.0)
                time.sleep(0.2)
                cancelled = workflow_runner.cancel(res["run_id"])
                assert cancelled["ok"] is True
                task = _wait_done(res["run_id"], timeout=6.0)
    assert task["status"] in ("cancelled", "done", "error")


def test_panel_snapshot_shape():
    with patch("CortexOS.execution.dag_runner.execute_node", new=_stub_execute_node):
        with patch(
            "CortexOS.execution.workflow_openvault.ensure_provider_keys",
            return_value={"ok": True},
        ):
            with patch(
                "CortexOS.execution.workflow_openvault.check_openfree_budget",
                return_value={"ok": True},
            ):
                res = workflow_runner.start("code_review", target="foo.py", cost_ceiling_myr=2.0)
                _wait_done(res["run_id"])
                snap = workflow_runner.snapshot()
    assert snap["ok"] is True
    assert "running" in snap and "finished" in snap
    assert any(t["id"] == res["run_id"] for t in snap["finished"])


def test_store_retries_transient_disk_io(monkeypatch):
    import sqlite3 as _sqlite3

    calls = {"n": 0}
    real_connect = _sqlite3.connect

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _sqlite3.OperationalError("disk I/O error")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(_sqlite3, "connect", flaky)
    rows = workflow_store.list_runs()
    assert rows == []
    assert calls["n"] >= 2


def test_alias_netie_smoothness():
    t = workflow_templates.get("smoothness_audit")
    assert t is not None
    with patch("CortexOS.execution.dag_runner.execute_node", new=_stub_execute_node):
        with patch(
            "CortexOS.execution.workflow_openvault.ensure_provider_keys",
            return_value={"ok": True},
        ):
            with patch(
                "CortexOS.execution.workflow_openvault.check_openfree_budget",
                return_value={"ok": True},
            ):
                res = workflow_runner.start(
                    "netie-smoothness-audit", target="x", cost_ceiling_myr=1.0
                )
                assert res["ok"] is True
                assert res["template_id"] == "smoothness_audit"
                _wait_done(res["run_id"])
