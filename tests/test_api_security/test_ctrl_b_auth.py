"""T2-CTRL-B (#263): workflow, DAG-run and activity routes are role-gated.

Every assertion is on what an HTTP caller gets back (status and body) or on
the stored state a route would have changed: the workflow runs table, the
runner's hardware profile, the cost ledger, and spies on the runner entry
points. The no-false-positive tests compare a sufficient-role response with
the response the same route gives with auth disabled (the pre-gate behaviour).

Ticket-specific lines:
* the run's actor is the authenticated caller, even when the body names someone else;
* runs orphaned by an engine restart are reaped on every read path (task, event
  stream, activity panel), not only on the tasks panel;
* the activity panel never echoes a store's exception text (host paths).
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution import (
    dag_runner,
    scoreboard,
    step_journal,
    workflow_runner,
    workflow_store,
)
from CortexOS.execution.dag_runner import NodeResult
from CortexOS.fabrication.dsl_parser import NodeType

KEYS = {"viewer": "t2b-viewer-key", "steward": "t2b-steward-key", "admin": "t2b-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())
_ROLE_ORDER = ("viewer", "steward", "admin")
TEMPLATE = "smoothness_audit"
_REAL_EXECUTE_NODE = dag_runner.execute_node


async def _stub_execute_node(node, context, router, ledger, workflow_cost_ceiling_myr=None):
    """Deterministic workflow agent: no network, fixed telemetry.

    ``POST /run`` nodes (llm_judged) go to the real executor, which uses the
    stub model router installed on the app.
    """
    if node.type == NodeType.LLM_JUDGED:
        return await _REAL_EXECUTE_NODE(
            node, context, router, ledger, workflow_cost_ceiling_myr=workflow_cost_ceiling_myr
        )
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


def _runs_table() -> list[dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM wf_runs ORDER BY id").fetchall()]


def _seed_orphan(run_id: str) -> None:
    """A row a dead engine left behind: created, started, never finished."""
    workflow_store.create_run(
        run_id, template_id=TEMPLATE, title="Smoothness audit", prompt="x", variables={"target": "x"}
    )
    workflow_store.start_run(run_id)
    _backdate(run_id, time.time() - 60)


def _seed_done(run_id: str) -> None:
    workflow_store.create_run(
        run_id, template_id=TEMPLATE, title="Smoothness audit", prompt="x", variables={"target": "x"}
    )
    workflow_store.finish_run(run_id, status="completed")
    _backdate(run_id, time.time() - 120)


def _simulate_restart(monkeypatch) -> None:
    """This process started now and has reaped nothing yet."""
    monkeypatch.setattr(workflow_runner, "_PROCESS_STARTED_AT", time.time(), raising=False)
    monkeypatch.setattr(workflow_runner, "_REAPED_ONCE", False, raising=False)


def _wait_terminal(run_id: str, timeout: float = 15.0) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = workflow_runner.get_task(run_id)
        if task and task["status"] not in ("running", "queued"):
            return task
        time.sleep(0.05)
    return workflow_runner.get_task(run_id)


def _dag(prompt: str) -> dict[str, Any]:
    from CortexOS.execution.model_router import BIG_API_PLACEHOLDER

    return {
        "version": "2.0",
        "intent_hash": "h",
        "entry_node_id": "j1",
        "output_node_id": "e1",
        "nodes": [
            {
                "id": "j1",
                "kind": "llm_judged",
                "default_tier": "T3",
                "max_tier": "T3",
                "provider": BIG_API_PLACEHOLDER,
                "prompt": prompt,
                "max_tokens": 40,
                "cost_ceiling_myr": 10.0,
                "inputs": [],
            },
            {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
        ],
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")
    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "wf.db")
    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    from CortexOS.execution import app_store, routine_scheduler

    monkeypatch.setattr(routine_scheduler, "DB_PATH", tmp_path / "routines.db")
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    monkeypatch.setattr(workflow_runner, "_HARDWARE", {}, raising=False)
    workflow_store.init()

    from CortexOS.api.app import create_app
    from CortexOS.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter
    from tests.test_execution.test_cost_ledger_and_executor import StubAdapter

    app = create_app()
    with patch(
        "CortexOS.execution.workflow_openvault.ensure_provider_keys", return_value={"ok": True}
    ), patch(
        "CortexOS.execution.workflow_openvault.check_openfree_budget", return_value={"ok": True}
    ), patch("CortexOS.execution.dag_runner.execute_node", new=_stub_execute_node), TestClient(
        app
    ) as client:
        stub = StubAdapter(projected_rate=0.001)
        app.state.model_router = ModelRouter(
            adapter_registry={"anthropic": stub, "openai": stub, "self_hosted": stub},
            provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
        )
        yield {"client": client, "app": app, "tmp": tmp_path}


def _hdr(role: str | None) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]} if role else {}


def _below(min_role: str) -> list[str]:
    return list(_ROLE_ORDER[: _ROLE_ORDER.index(min_role)])


def _assert_no_path(body: str, tmp_path) -> None:
    assert str(tmp_path) not in body
    assert "/home/" not in body and "/tmp/" not in body and ":\\\\" not in body


# (method, path, json body factory, minimum role)
def _routes() -> list[tuple[str, str, Any, str]]:
    return [
        ("GET", "/api/workflows", None, "viewer"),
        ("GET", "/api/workflows/tasks", None, "viewer"),
        ("GET", "/api/workflows/task/wf_orphan", None, "viewer"),
        ("GET", "/api/workflows/task/wf_orphan/events", None, "viewer"),
        (
            "POST",
            "/api/workflows/run",
            lambda: {"template_id": TEMPLATE, "target": "x", "cost_ceiling_myr": 1000.0, "actor": "mallory"},
            "steward",
        ),
        ("POST", "/api/workflows/cancel", lambda: {"task_id": "wf_orphan"}, "steward"),
        ("POST", "/api/workflows/resume", lambda: {"task_id": "wf_done"}, "steward"),
        ("POST", "/api/workflows/clear", None, "admin"),
        ("POST", "/api/workflows/recognize", lambda: {"prompt": "audit the smoothness of x"}, "viewer"),
        ("POST", "/api/workflows/hardware", lambda: {"hardware": {"vram_gb": 1}}, "steward"),
        ("POST", "/run", lambda: {"dag": _dag("refused"), "run_id": "t2b_refused"}, "admin"),
        ("GET", "/api/engine/runs/t2b_refused/cost", None, "viewer"),
        ("GET", "/api/engine/activity", None, "viewer"),
    ]


def _call(client: TestClient, method: str, path: str, body: Any, role: str | None):
    kwargs: dict[str, Any] = {"headers": _hdr(role)}
    if body is not None:
        kwargs["json"] = body()
    return client.request(method, path, **kwargs)


# --- refusals: 401 without a key, 403 below the role, no side effect ----------


def test_every_route_refuses_without_key_or_with_low_role_and_changes_nothing(env, monkeypatch):
    client, tmp, app = env["client"], env["tmp"], env["app"]
    _seed_orphan("wf_orphan")
    _seed_done("wf_done")
    _simulate_restart(monkeypatch)

    calls: list[str] = []

    def _spy(name: str):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} ran on a refused request")

        return _fn

    for name in ("start", "cancel", "resume", "clear_finished", "set_hardware", "_reap_orphans_once"):
        monkeypatch.setattr(workflow_runner, name, _spy(name))
    import CortexOS.api.workflow_routes as wr

    monkeypatch.setattr(wr, "recognize", _spy("recognize"))
    for mod in ("CortexOS.execution.dag_runner", "netie.execution.dag_runner"):
        monkeypatch.setattr(importlib.import_module(mod), "run_dag", _spy("run_dag"))

    before_rows = _runs_table()
    before_hw = workflow_runner.get_hardware()
    before_ledger = len(app.state.ledger.records())

    for method, path, body, min_role in _routes():
        res = _call(client, method, path, body, None)
        assert res.status_code == 401, (method, path, res.status_code, res.text)
        _assert_no_path(res.text, tmp)
        for role in _below(min_role):
            res = _call(client, method, path, body, role)
            assert res.status_code == 403, (method, path, role, res.status_code, res.text)
            _assert_no_path(res.text, tmp)

    assert calls == []
    # The orphan is still 'running': a refused read did not even reap.
    assert _runs_table() == before_rows
    assert workflow_runner.get_hardware() == before_hw
    assert len(app.state.ledger.records()) == before_ledger


def test_every_registered_route_in_scope_is_gated(env):
    """A route added later without the gate fails here, not in production."""
    client = env["client"]
    seen = 0
    for path, ops in client.app.openapi()["paths"].items():
        in_scope = (
            path.startswith("/api/workflows")
            or path == "/run"
            or path.startswith("/api/engine/runs/")
            or path == "/api/engine/activity"
        )
        if not in_scope:
            continue
        concrete = path.replace("{task_id}", "wf_x").replace("{run_id}", "r_x")
        for method in sorted(m.upper() for m in ops):
            seen += 1
            res = client.request(method, concrete, json={})
            assert res.status_code == 401, (method, path, res.status_code)
    assert seen >= len(_routes())


# --- no false positive: a sufficient role sees today's behaviour --------------


def _baseline(monkeypatch, fn):
    """Run ``fn`` with auth disabled: the response the route gave before the gate."""
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    try:
        return fn()
    finally:
        monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)


def _drop_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_volatile(v) for k, v in value.items() if k not in ("ts", "in_seconds")}
    if isinstance(value, list):
        return [_drop_volatile(v) for v in value]
    return value


def _stable(res: Any) -> Any:
    if "application/json" in res.headers.get("content-type", ""):
        return _drop_volatile(res.json())
    return res.text


def test_viewer_reads_match_ungated_response(env, monkeypatch):
    client = env["client"]
    _seed_done("wf_done")
    for method, path, body in (
        ("GET", "/api/workflows", None),
        ("GET", "/api/workflows/tasks", None),
        ("GET", "/api/workflows/task/wf_done", None),
        ("GET", "/api/workflows/task/wf_missing", None),
        ("GET", "/api/workflows/task/wf_done/events", None),
        ("GET", "/api/workflows/task/wf_missing/events", None),
        ("POST", "/api/workflows/recognize", lambda: {"prompt": "audit the smoothness of x"}),
        ("GET", "/api/engine/runs/nothing_here/cost", None),
        ("GET", "/api/engine/activity", None),
    ):
        want = _baseline(monkeypatch, lambda m=method, p=path, b=body: _call(client, m, p, b, None))
        assert want.status_code in (200, 404), (path, want.status_code, want.text)
        for role in _ROLE_ORDER:
            got = _call(client, method, path, body, role)
            assert got.status_code == want.status_code, (path, role)
            assert _stable(got) == _stable(want), (path, role)


def test_steward_and_admin_mutations_work(env):
    client = env["client"]
    _seed_done("wf_done")

    hw = client.post("/api/workflows/hardware", json={"hardware": {"vram_gb": 24}}, headers=_hdr("steward"))
    assert hw.status_code == 200 and hw.json() == {"ok": True, "hardware": {"vram_gb": 24}}
    assert workflow_runner.get_hardware() == {"vram_gb": 24}

    started = client.post(
        "/api/workflows/run", json={"template_id": TEMPLATE, "target": "x", "cost_ceiling_myr": 1000.0}, headers=_hdr("steward")
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _t = _wait_terminal(run_id)
    assert _t["status"] == "done", _t["error"]
    tasks = client.get("/api/workflows/tasks", headers=_hdr("viewer")).json()
    assert run_id in [t["id"] for t in tasks["finished"]]

    resumed = client.post("/api/workflows/resume", json={"task_id": "wf_done"}, headers=_hdr("steward"))
    assert resumed.status_code == 200 and resumed.json()["ok"] is True, resumed.text
    assert _wait_terminal("wf_done")["status"] == "done"

    workflow_store.create_run("wf_live", template_id=TEMPLATE, title="t", prompt="x")
    workflow_store.start_run("wf_live")
    cancelled = client.post("/api/workflows/cancel", json={"task_id": "wf_live"}, headers=_hdr("steward"))
    assert cancelled.status_code == 200 and cancelled.json() == {"ok": True, "task_id": "wf_live"}
    assert workflow_runner.get_task("wf_live")["status"] != "running"

    cleared = client.post("/api/workflows/clear", headers=_hdr("admin"))
    assert cleared.status_code == 200 and cleared.json()["cleared"] >= 2
    assert client.get("/api/workflows/tasks", headers=_hdr("viewer")).json()["finished"] == []

    ran = client.post("/run", json={"dag": _dag("probe"), "run_id": "t2b_run"}, headers=_hdr("admin"))
    assert ran.status_code == 200, ran.text
    assert ran.json()["nodes"]["j1"]["output"]["content"] == "ok"
    assert ran.json()["total_myr"] > 0
    cost = client.get("/api/engine/runs/t2b_run/cost", headers=_hdr("viewer"))
    assert cost.status_code == 200 and cost.json()["total_myr"] > 0
    assert [r["node_id"] for r in cost.json()["records"]].count("j1") == 1


# --- actor from the authenticated caller -------------------------------------


@pytest.mark.parametrize("role", ["steward", "admin"])
def test_run_actor_is_the_caller_not_the_body(env, role):
    client = env["client"]
    res = client.post(
        "/api/workflows/run",
        json={"template_id": TEMPLATE, "target": "x", "cost_ceiling_myr": 1000.0, "actor": "someone_else"},
        headers=_hdr(role),
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]
    assert _wait_terminal(run_id)["status"] == "done"
    assert workflow_store.get_run(run_id)["actor"] == f"api_{role}"


def test_run_actor_with_auth_disabled_is_the_local_caller(env, monkeypatch):
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    res = env["client"].post(
        "/api/workflows/run", json={"template_id": TEMPLATE, "target": "x", "cost_ceiling_myr": 1000.0, "actor": "someone_else"}
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]
    assert _wait_terminal(run_id)["status"] == "done"
    assert workflow_store.get_run(run_id)["actor"] == "auth_disabled"


# --- orphaned runs are reaped on every read path -----------------------------


def test_task_read_reaps_an_orphan(env, monkeypatch):
    _seed_orphan("wf_orphan")
    _simulate_restart(monkeypatch)
    res = env["client"].get("/api/workflows/task/wf_orphan", headers=_hdr("viewer"))
    assert res.status_code == 200
    task = res.json()["task"]
    assert task["status"] == "error"
    assert "interrupted" in task["error"]


def test_event_stream_reaps_an_orphan(env, monkeypatch):
    _seed_orphan("wf_orphan")
    _simulate_restart(monkeypatch)
    real_stream = workflow_store.stream

    def _bounded(run_id: str, **_kw: Any):
        # Keep the pre-fix code from streaming keepalives forever.
        for i, event in enumerate(real_stream(run_id, keepalive=0.05)):
            yield event
            if i >= 2:
                return

    monkeypatch.setattr(workflow_store, "stream", _bounded)
    res = env["client"].get("/api/workflows/task/wf_orphan/events", headers=_hdr("viewer"))
    assert res.status_code == 200
    events = [json.loads(line[len("data: "):]) for line in res.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "snapshot"
    assert events[0]["run"]["status"] == "error"
    assert "interrupted" in events[0]["run"]["error"]
    assert len(events) == 1  # a terminal run ends the stream after its snapshot


def test_activity_panel_reaps_an_orphan(env, monkeypatch):
    _seed_orphan("wf_orphan")
    _simulate_restart(monkeypatch)
    res = env["client"].get("/api/engine/activity", headers=_hdr("viewer"))
    assert res.status_code == 200
    workflows = res.json()["workflows"]
    assert "wf_orphan" not in [r["id"] for r in workflows["active"]]
    recent = {r["id"]: r for r in workflows["recent"]}
    assert recent["wf_orphan"]["status"] == "error"


def test_live_run_is_not_reaped_by_a_read(env, monkeypatch):
    """No false positive: a run created after this process started stays live."""
    _simulate_restart(monkeypatch)
    time.sleep(0.01)
    workflow_store.create_run("wf_fresh", template_id=TEMPLATE, title="t", prompt="x")
    workflow_store.start_run("wf_fresh")
    client = env["client"]
    assert client.get("/api/workflows/task/wf_fresh", headers=_hdr("viewer")).json()["task"]["status"] == "running"
    active = client.get("/api/engine/activity", headers=_hdr("viewer")).json()["workflows"]["active"]
    assert "wf_fresh" in [r["id"] for r in active]
    workflow_store.finish_run("wf_fresh", status="completed")


# --- the activity panel does not echo exception text -------------------------


def test_activity_section_error_has_no_host_path(env, monkeypatch):
    tmp = env["tmp"]

    def _broken() -> None:
        raise RuntimeError(f"unable to open database file {tmp / 'scoreboard.db'}")

    monkeypatch.setattr(scoreboard, "init", _broken)
    res = env["client"].get("/api/engine/activity", headers=_hdr("viewer"))
    assert res.status_code == 200
    body = res.json()
    assert body["races"] == {"error": "RuntimeError"}
    assert "active" in body["workflows"]  # the other sections still render
    _assert_no_path(res.text, tmp)
