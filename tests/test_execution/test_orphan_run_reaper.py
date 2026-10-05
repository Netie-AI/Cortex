from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import time

import pytest

from CortexOS.execution import (
    dag_runner,
    step_journal,
    workflow_runner,
    workflow_store,
    workflow_templates,
)
from CortexOS.execution.dag_runner import NodeResult
from CortexOS.execution.model_router import ModelRouter
from CortexOS.fabrication.dsl_parser import NodeType
from CortexOS.routing.cost_ledger import CostLedger


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "workflows.db")
    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")
    monkeypatch.setattr(workflow_runner, "_ACTIVE", {})
    monkeypatch.setattr(workflow_runner, "_LOOP", None)
    monkeypatch.setattr(workflow_runner, "_LOOP_THREAD", None)
    workflow_store.init()


def _seed_run(run_id: str, *, status: str, at: float) -> None:
    workflow_store.create_run(
        run_id,
        template_id="orphan_test",
        title=run_id,
        variables={"topic": "trust"},
    )
    fields = {"status": status, "created_at": at}
    if status == "running":
        fields["started_at"] = at
    workflow_store._update_run(run_id, **fields)


def test_reap_orphans_marks_only_old_inactive_runs() -> None:
    cutoff = time.time()
    _seed_run("old-running", status="running", at=cutoff - 10)
    _seed_run("old-queued", status="queued", at=cutoff - 10)
    _seed_run("live-running", status="running", at=cutoff - 10)
    _seed_run("recent-running", status="running", at=cutoff + 10)
    _seed_run("already-done", status="completed", at=cutoff - 10)

    assert workflow_store.reap_orphans({"live-running"}, cutoff) == 2

    for run_id in ("old-running", "old-queued"):
        run = workflow_store.get_run(run_id)
        assert run["status"] == "error"
        assert "interrupted" in run["error"].lower()
        assert "resumable" in run["error"].lower()
        assert run["finished_at"] is not None

    assert workflow_store.get_run("live-running")["status"] == "running"
    assert workflow_store.get_run("recent-running")["status"] == "running"
    assert workflow_store.get_run("already-done")["status"] == "completed"

    before = workflow_store.get_run("live-running")
    assert workflow_store.reap_orphans({"live-running"}, cutoff) == 0
    after = workflow_store.get_run("live-running")
    assert after["status"] == before["status"]
    assert after["error"] == before["error"]
    assert after["finished_at"] == before["finished_at"]


def test_ensure_loop_reaps_once_with_active_ids(monkeypatch) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    class FakeLoop:
        running = False

        def is_running(self) -> bool:
            return self.running

        def run_forever(self) -> None:
            self.running = True

    class FakeThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self) -> None:
            self.target()

    loop = FakeLoop()
    monkeypatch.setattr(workflow_runner, "_ACTIVE", {"wf-live": object()})
    monkeypatch.setattr(workflow_runner, "_PROCESS_STARTED_AT", 123.0)
    monkeypatch.setattr(
        workflow_store,
        "reap_orphans",
        lambda active_ids, started_before: calls.append((tuple(active_ids), started_before)),
    )
    monkeypatch.setattr(workflow_runner.asyncio, "new_event_loop", lambda: loop)
    monkeypatch.setattr(workflow_runner.threading, "Thread", FakeThread)

    assert workflow_runner._ensure_loop() is loop
    assert workflow_runner._ensure_loop() is loop
    assert calls == [(("wf-live",), 123.0)]


def test_reaped_run_leaves_active_panel_and_resumes_with_replay(monkeypatch) -> None:
    template = workflow_templates.WorkflowTemplate(
        id="orphan_test",
        name="Orphan test",
        description="One journaled node",
        triggers=(),
        phases=(
            workflow_templates.PhaseSpec(
                id="work",
                title="Work",
                detail="Journal one node",
                agents=(
                    workflow_templates.AgentSpec(
                        id="worker",
                        purpose="Produce a cached result",
                        prompt_id="generic.worker",
                    ),
                ),
            ),
        ),
    )

    async def execute_node(node, context, _router, _ledger, **_kwargs):
        if node.type == NodeType.DOCUMENT_REF:
            return NodeResult(node.id, context.get(node.context_key or "prompt"), "deterministic", 0)
        if node.type == NodeType.AGENT_TASK:
            return NodeResult(node.id, {"data": {"ok": True}}, "agent", 0)
        return NodeResult(node.id, {"done": True}, "emit", 0)

    monkeypatch.setattr(dag_runner, "execute_node", execute_node)
    monkeypatch.setattr(
        workflow_runner.workflow_openvault, "ensure_provider_keys", lambda: {"ok": True}
    )
    monkeypatch.setattr(
        workflow_runner.workflow_openvault,
        "check_openfree_budget",
        lambda *_args, **_kwargs: {"ok": True},
    )

    run_id = "wf-orphan"
    workflow_store.create_run(
        run_id,
        template_id=template.id,
        title=template.name,
        purpose=template.description,
        variables={"topic": "trust"},
        phases=workflow_runner._phase_rows(template),
    )
    asyncio.run(
        workflow_runner._execute_run(
            run_id,
            template,
            {"topic": "trust"},
            cost_ceiling_myr=1.0,
            router=ModelRouter(),
            ledger=CostLedger(),
            spill_high_effort=False,
            max_parallel=1,
        )
    )

    cutoff = time.time()
    workflow_store._update_run(run_id, status="running", started_at=cutoff - 10, finished_at=None)
    assert workflow_store.reap_orphans(set(), cutoff) == 1

    task = workflow_runner.get_task(run_id)
    assert task is not None
    assert task["status"] == "error"
    assert "interrupted" in task["error"].lower()
    assert "resumable" in task["error"].lower()
    assert all(item["id"] != run_id for item in workflow_runner.snapshot()["running"])

    events = workflow_store.subscribe(run_id)
    monkeypatch.setattr(workflow_templates, "get", lambda template_id: template)
    monkeypatch.setattr(workflow_runner, "_ensure_loop", lambda: object())

    def run_now(coro, _loop):
        asyncio.run(coro)
        future: concurrent.futures.Future[None] = concurrent.futures.Future()
        future.set_result(None)
        return future

    monkeypatch.setattr(workflow_runner.asyncio, "run_coroutine_threadsafe", run_now)
    resumed = workflow_runner.resume(run_id, router=ModelRouter(), ledger=CostLedger())

    assert resumed["ok"] is True
    replay_events = []
    while True:
        try:
            replay_events.append(events.get_nowait())
        except queue.Empty:
            break
    workflow_store.unsubscribe(run_id, events)
    assert any(event.get("replayed") is True for event in replay_events)
