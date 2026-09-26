"""TRUST-04 (#260): runs orphaned by an engine restart are reaped and resumable.

Every test asserts what an operator sees: the Background tasks panel
(``workflow_runner.snapshot`` / ``get_task``), the runs listed as active, the
``resume()`` return value, and the live event stream a panel subscribes to.

A restart is simulated in-process: the orphan row is backdated to before the
runner's recorded process start, ``_ACTIVE`` does not hold it, and the runner
forgets its loop so the next ``_ensure_loop`` builds a fresh one.
"""

from __future__ import annotations

import queue
import sqlite3
import time
from typing import Any
from unittest.mock import patch

import pytest

from CortexOS.execution import step_journal, workflow_runner, workflow_store
from CortexOS.execution.dag_runner import NodeResult
from CortexOS.fabrication.dsl_parser import NodeType

TEMPLATE = "smoothness_audit"


async def _stub_execute_node(node, context, router, ledger, workflow_cost_ceiling_myr=None):
    """Deterministic agent: no network, fixed telemetry."""
    if node.type == NodeType.AGENT_TASK:
        tel = {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "tool_count": 0,
            "elapsed_ms": 5,
            "model": "stub",
            "tier": "T1",
            "cost_myr": 0.01,
            "steps": 1,
        }
        return NodeResult(
            node_id=node.id,
            output={"content": '{"ok":true}', "data": {"findings": []}, "telemetry": tel},
            tier="agent",
            cost_myr=0.01,
        )
    if node.type == NodeType.EMIT:
        return NodeResult(
            node_id=node.id, output={i: context.get(i) for i in node.inputs}, tier="emit", cost_myr=0.0
        )
    if node.type == NodeType.DOCUMENT_REF:
        return NodeResult(
            node_id=node.id, output=context.get(node.context_key or "prompt"), tier="deterministic", cost_myr=0.0
        )
    return NodeResult(node_id=node.id, output={}, tier="deterministic", cost_myr=0.0)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "wf.db")
    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")
    workflow_store.init()
    yield


@pytest.fixture
def no_network():
    with patch(
        "CortexOS.execution.workflow_openvault.ensure_provider_keys", return_value={"ok": True}
    ), patch(
        "CortexOS.execution.workflow_openvault.check_openfree_budget", return_value={"ok": True}
    ):
        yield


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(workflow_store.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _backdate(run_id: str, at: float) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE wf_runs SET created_at=?, started_at=CASE WHEN started_at IS NULL THEN NULL ELSE ? END"
            " WHERE id=?",
            (at, at, run_id),
        )


def _seed_orphan(run_id: str, *, status: str = "running", age_s: float = 60.0) -> None:
    """A row a dead engine left behind: created, started, never finished."""
    workflow_store.create_run(
        run_id, template_id=TEMPLATE, title="Smoothness audit", prompt="x", variables={"target": "x"}
    )
    if status == "running":
        workflow_store.start_run(run_id)
    _backdate(run_id, time.time() - age_s)


def _simulate_restart(monkeypatch) -> None:
    """This process started now; it has no loop yet and has reaped nothing.

    The previous loop thread is left running (as a sibling would be) so a run
    already on it keeps executing.
    """
    monkeypatch.setattr(workflow_runner, "_LOOP", None)
    monkeypatch.setattr(workflow_runner, "_PROCESS_STARTED_AT", time.time(), raising=False)
    monkeypatch.setattr(workflow_runner, "_REAPED_ONCE", False, raising=False)


def _wait_terminal(run_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = workflow_runner.get_task(run_id)
        if task and task["status"] not in ("running", "queued"):
            return task
        time.sleep(0.05)
    return workflow_runner.get_task(run_id)


def _runs_table() -> list[dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM wf_runs ORDER BY id").fetchall()]


def _active_ids() -> set[str]:
    return {r["id"] for r in workflow_store.list_runs(status="active")}


# ---------------------------------------------------------------------------


def test_runs_panel_shows_orphan_as_interrupted_not_active(monkeypatch):
    _seed_orphan("wf_orphan_run")
    _seed_orphan("wf_orphan_q", status="queued")
    _simulate_restart(monkeypatch)

    panel = workflow_runner.snapshot()

    running_ids = {t["id"] for t in panel["running"]}
    finished = {t["id"]: t for t in panel["finished"]}
    for rid in ("wf_orphan_run", "wf_orphan_q"):
        assert rid not in running_ids
        assert finished[rid]["status"] == "error"
        assert "interrupted" in finished[rid]["error"]
        assert "resumable" in finished[rid]["error"]
    assert not ({"wf_orphan_run", "wf_orphan_q"} & _active_ids())


def test_loop_creation_reaps_orphan(monkeypatch):
    _seed_orphan("wf_orphan_loop")
    _simulate_restart(monkeypatch)

    workflow_runner._ensure_loop()

    task = workflow_runner.get_task("wf_orphan_loop")
    assert task["status"] == "error"
    assert "interrupted" in task["error"] and "resumable" in task["error"]
    assert "wf_orphan_loop" not in _active_ids()


def test_resume_after_restart_replays_journaled_nodes(monkeypatch, no_network):
    calls: list[str] = []

    async def counting(node, context, router, ledger, workflow_cost_ceiling_myr=None):
        if node.type == NodeType.AGENT_TASK:
            calls.append(node.id)
        return await _stub_execute_node(node, context, router, ledger, workflow_cost_ceiling_myr)

    with patch("CortexOS.execution.dag_runner.execute_node", new=counting):
        first = workflow_runner.start(TEMPLATE, target="x", cost_ceiling_myr=2.0)
        run_id = first["run_id"]
        done = _wait_terminal(run_id)
        assert done["status"] == "done"
        agents_first_pass = len(calls)
        assert agents_first_pass > 0
        tokens_first_pass = done["totals"]["tokens"]

        # The engine dies with this run on the books as 'running'.
        with _db() as conn:
            conn.execute(
                "UPDATE wf_runs SET status='running', finished_at=NULL, error='' WHERE id=?", (run_id,)
            )
        _backdate(run_id, time.time() - 60)
        _simulate_restart(monkeypatch)

        events: queue.Queue = workflow_store.subscribe(run_id)
        try:
            res = workflow_runner.resume(run_id, cost_ceiling_myr=2.0)
            assert res["ok"] is True, res
            assert res["resumed"] is True
            task = _wait_terminal(run_id)
        finally:
            workflow_store.unsubscribe(run_id, events)

    seen: list[dict[str, Any]] = []
    while True:
        try:
            seen.append(events.get_nowait())
        except queue.Empty:
            break
    replayed = [e for e in seen if e.get("replayed") is True]
    assert replayed, [e.get("type") for e in seen]
    # Every agent came back from the journal; none was re-run or re-billed.
    assert len(calls) == agents_first_pass
    assert task["status"] == "done"
    assert task["error"] == ""
    assert all(a["status"] == "done" for ph in task["phases"] for a in ph["agents"])
    assert task["totals"]["tokens"] <= tokens_first_pass


def test_live_runs_are_not_reaped(monkeypatch, no_network):
    """No false positive: a run this engine is executing, or one created after
    it started, keeps its status while a real orphan beside it is reaped."""

    async def slow(node, context, router, ledger, workflow_cost_ceiling_myr=None):
        if node.type == NodeType.AGENT_TASK:
            time.sleep(0.3)
        return await _stub_execute_node(node, context, router, ledger, workflow_cost_ceiling_myr)

    _seed_orphan("wf_orphan_side")
    with patch("CortexOS.execution.dag_runner.execute_node", new=slow):
        live = workflow_runner.start(TEMPLATE, target="x", cost_ceiling_myr=2.0)
        live_id = live["run_id"]
        time.sleep(0.1)
        assert live_id in workflow_runner._ACTIVE
        # Make the live run look older than the process: only _ACTIVE protects it.
        _backdate(live_id, time.time() - 60)

        # A row created after this process started but not (yet) in _ACTIVE.
        _simulate_restart(monkeypatch)
        time.sleep(0.01)
        workflow_store.create_run(
            "wf_fresh", template_id=TEMPLATE, title="Smoothness audit", prompt="x"
        )
        workflow_store.start_run("wf_fresh")

        workflow_runner._ensure_loop()

        assert workflow_runner.get_task(live_id)["status"] == "running"
        assert workflow_runner.get_task("wf_fresh")["status"] == "running"
        assert {live_id, "wf_fresh"} <= _active_ids()
        orphan = workflow_runner.get_task("wf_orphan_side")
        assert orphan["status"] == "error" and "interrupted" in orphan["error"]

        # The live run finishes normally: the reaper never marked it.
        finished = _wait_terminal(live_id, timeout=20.0)
    assert finished["status"] == "done"
    assert finished["error"] == ""


def test_no_orphans_leaves_wf_runs_unchanged(monkeypatch):
    for rid, status in (("wf_ok", "completed"), ("wf_bad", "error"), ("wf_stop", "stopped")):
        workflow_store.create_run(rid, template_id=TEMPLATE, title="t", prompt="x")
        workflow_store.finish_run(rid, status=status, error="boom" if status == "error" else "")
        _backdate(rid, time.time() - 60)
    _simulate_restart(monkeypatch)
    workflow_store.create_run("wf_new", template_id=TEMPLATE, title="t", prompt="x")
    workflow_store.start_run("wf_new")
    before = _runs_table()

    reaped = workflow_store.reap_orphans(set(), workflow_runner._PROCESS_STARTED_AT)
    workflow_runner.snapshot()

    assert reaped == []
    assert _runs_table() == before
