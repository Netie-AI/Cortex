"""H2-COST-NODE-ALL (#252): every DAG node kind writes a ledger row, including on failure.

Before this ticket only ``llm_judged`` nodes wrote ``node_executions`` rows on
error (through ``invoke_routed_completion``). Every other kind got a zero-cost
``ok`` row only after success, so the audit trail of a failed run was missing
the node that failed, and a resumed worker with an empty in-process ledger
recorded nothing for replayed nodes.

Assertions are on the user-visible artifacts: the run result (raises the
original exception or completes with outputs) and the cost ledger rows for the
run (``records_for_run``), never on internals.
"""

from __future__ import annotations

import json

import pytest
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger

TOOLS = ("t1", "t2", "t3")


def _parse(nodes: list[dict], entry: str, output: str = "emit"):
    r = parse_dsl(
        json.dumps(
            {
                "version": "2.0",
                "intent_hash": "h",
                "entry_node_id": entry,
                "output_node_id": output,
                "nodes": nodes,
            }
        ),
        "test",
    )
    assert isinstance(r, Ok)
    return r.value


def _rule_raises_dag():
    """document_ref -> deterministic_rule without a ruleset (raises ValueError) -> emit."""
    return _parse(
        [
            {"id": "dref", "kind": "document_ref", "context_key": "seed_doc", "inputs": []},
            {"id": "chk", "kind": "deterministic_rule", "inputs": ["dref"]},
            {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["chk"]},
        ],
        entry="dref",
    )


def _tool_layer_dag():
    """Three tool_call siblings in one layer, then an emit."""
    nodes = [
        {"id": nid, "kind": "TOOL_CALL", "tool_name": nid, "inputs": []} for nid in TOOLS
    ]
    nodes.append({"id": "emit", "kind": "EMIT", "tier": 0, "inputs": list(TOOLS)})
    return _parse(nodes, entry="t1")


def _deterministic_dag():
    """document_ref -> emit: two non-LLM nodes, both journaled."""
    return _parse(
        [
            {"id": "dref", "kind": "document_ref", "context_key": "seed_doc", "inputs": []},
            {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["dref"]},
        ],
        entry="dref",
    )


class _BoomTool(Exception):
    pass


def _patch_tools(monkeypatch, failing: dict[str, BaseException]):
    """Replace the sandboxed tool runner: named tools raise, the rest return ok."""
    from netie.execution import tool_runner

    calls: list[str] = []

    def fake_run_tool_call(tool, params=None, *, actor, run_id=None, db_path=None):
        del params, actor, run_id, db_path
        calls.append(tool)
        if tool in failing:
            raise failing[tool]
        return {"ok": True, "tool": tool}

    monkeypatch.setattr(tool_runner, "run_tool_call", fake_run_tool_call)
    return calls


def _router() -> ModelRouter:
    return ModelRouter(adapter_registry={}, provider_aliases={})


# ---------------------------------------------------------------------------
# Acceptance 1: a non-LLM node that raises leaves exactly one error row.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_rule_that_raises_writes_one_error_row_and_reraises():
    ledger = CostLedger()
    ctx = ExecutionContext("run_rule_err", seed={"seed_doc": {"doc_type": "spa"}})

    with pytest.raises(ValueError) as excinfo:
        await run_dag(_rule_raises_dag(), ctx, _router(), ledger, workflow_cost_ceiling_myr=5.0)

    # The original exception, unchanged.
    assert "requires ruleset" in str(excinfo.value)

    rows = ledger.records_for_run("run_rule_err")
    chk = [r for r in rows if r.node_id == "chk"]
    assert len(chk) == 1, [(r.node_id, r.status) for r in rows]
    row = chk[0]
    assert row.status == "error"
    assert row.error is not None
    assert row.error.startswith("ValueError: ")
    assert "requires ruleset" in row.error
    assert row.cost_myr == 0.0
    assert row.tier == "deterministic"
    assert row.ceiling_myr == 5.0
    # The node before it completed and has its ok row; nothing after it ran.
    assert [(r.node_id, r.status) for r in rows] == [("dref", "ok"), ("chk", "error")]
    assert ledger.total_cost("run_rule_err") == 0.0


@pytest.mark.asyncio
async def test_error_text_is_class_plus_message_truncated(monkeypatch):
    # Imported here so the rest of the file still collects on pre-#252 code.
    from netie.routing.cost_ledger import ERROR_MAX_CHARS, format_node_error

    long_msg = "x" * (ERROR_MAX_CHARS * 4)
    _patch_tools(monkeypatch, {"t1": _BoomTool(long_msg)})
    dag = _parse(
        [
            {"id": "t1", "kind": "TOOL_CALL", "tool_name": "t1", "inputs": []},
            {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["t1"]},
        ],
        entry="t1",
    )
    ledger = CostLedger()

    with pytest.raises(_BoomTool) as excinfo:
        await run_dag(dag, ExecutionContext("run_trunc"), _router(), ledger)

    # Re-raised unchanged: the full message survives on the exception itself.
    assert str(excinfo.value) == long_msg
    rows = ledger.records_for_run("run_trunc")
    assert [(r.node_id, r.status) for r in rows] == [("t1", "error")]
    err = rows[0].error
    assert err is not None
    assert err.startswith("_BoomTool: xxx")
    assert len(err) <= ERROR_MAX_CHARS
    assert err.endswith("...")
    assert err == format_node_error(excinfo.value)


# ---------------------------------------------------------------------------
# Acceptance 3: a parallel layer with one failing tool_call gets one row per
# node and no duplicates.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("max_parallel", [None, 2])
async def test_parallel_layer_with_one_failing_tool_call_has_one_row_per_node(
    monkeypatch, max_parallel
):
    calls = _patch_tools(monkeypatch, {"t2": _BoomTool("tool t2 exploded")})
    ledger = CostLedger()
    run_id = f"run_par_tool_{max_parallel}"

    with pytest.raises(_BoomTool) as excinfo:
        await run_dag(
            _tool_layer_dag(),
            ExecutionContext(run_id),
            _router(),
            ledger,
            workflow_cost_ceiling_myr=1.0,
            parallel=True,
            max_parallel=max_parallel,
        )
    assert str(excinfo.value) == "tool t2 exploded"

    rows = ledger.records_for_run(run_id)
    by_node = {}
    for r in rows:
        by_node.setdefault(r.node_id, []).append(r)
    # Every sibling that ran has exactly one row, whatever the batching.
    for nid, group in by_node.items():
        assert len(group) == 1, f"{nid} has {len(group)} rows"
    assert by_node["t2"][0].status == "error"
    assert by_node["t2"][0].error == "_BoomTool: tool t2 exploded"
    assert by_node["t2"][0].tier == "tool"
    for nid in set(calls) - {"t2"}:
        assert by_node[nid][0].status == "ok"
        assert by_node[nid][0].cost_myr == 0.0
    # Every tool the runner called has a row, and no other node does.
    assert set(by_node) == set(calls)
    # The emit layer never ran: the failure stopped the run.
    assert "emit" not in by_node
    assert ledger.total_cost(run_id) == 0.0


@pytest.mark.asyncio
async def test_parallel_batch_settles_every_sibling_before_raising(monkeypatch):
    """All three siblings in the batch have rows even though t2 raised.

    With ``max_parallel=None`` the whole layer is one batch; a failure must not
    leave t1/t3 running detached with their rows arriving after the run has
    already raised.
    """
    calls = _patch_tools(monkeypatch, {"t2": _BoomTool("boom")})
    ledger = CostLedger()

    with pytest.raises(_BoomTool):
        await run_dag(
            _tool_layer_dag(),
            ExecutionContext("run_settle"),
            _router(),
            ledger,
            parallel=True,
        )

    assert sorted(calls) == sorted(TOOLS)
    rows = ledger.records_for_run("run_settle")
    assert sorted(r.node_id for r in rows) == sorted(TOOLS)
    statuses = {r.node_id: r.status for r in rows}
    assert statuses == {"t1": "ok", "t2": "error", "t3": "ok"}


# ---------------------------------------------------------------------------
# Acceptance 2: replayed nodes are marked and add 0 cost.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resumed_run_on_fresh_worker_records_replays_at_zero_cost(tmp_path, monkeypatch):
    from netie.execution import step_journal

    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")
    run_id = "run_replay_fresh"
    seed = {"seed_doc": {"doc_type": "spa"}}

    first = CostLedger()
    res1 = await run_dag(_deterministic_dag(), ExecutionContext(run_id, seed), _router(), first)
    assert res1.outputs["dref"].output == seed["seed_doc"]
    assert [(r.node_id, r.status) for r in first.records_for_run(run_id)] == [
        ("dref", "ok"),
        ("emit", "ok"),
    ]

    # A new worker (empty in-process ledger) resumes the same run_id.
    second = CostLedger()
    res2 = await run_dag(
        _deterministic_dag(), ExecutionContext(run_id, seed), _router(), second, resume=True
    )
    # Served from the journal: same outputs, marked as replay, no spend.
    assert res2.outputs["dref"].output == seed["seed_doc"]
    assert res2.outputs["dref"].cost_myr == 0.0
    assert res2.outputs["emit"].cost_myr == 0.0

    rows = second.records_for_run(run_id)
    assert [(r.node_id, r.status) for r in rows] == [("dref", "replayed"), ("emit", "replayed")]
    assert all(r.cost_myr == 0.0 and r.cache_hit and r.error is None for r in rows)
    assert not any(r.status == "ok" for r in rows), "a replay must never look like a fresh ok spend"
    assert second.total_cost(run_id) == 0.0


@pytest.mark.asyncio
async def test_resumed_run_on_same_worker_keeps_one_row_per_node(tmp_path, monkeypatch):
    from netie.execution import step_journal

    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")
    run_id = "run_replay_same"
    seed = {"seed_doc": {"k": 1}}
    ledger = CostLedger()

    await run_dag(_deterministic_dag(), ExecutionContext(run_id, seed), _router(), ledger)
    before = [(r.node_id, r.status, r.cost_myr) for r in ledger.records_for_run(run_id)]
    assert before == [("dref", "ok", 0.0), ("emit", "ok", 0.0)]

    res = await run_dag(
        _deterministic_dag(), ExecutionContext(run_id, seed), _router(), ledger, resume=True
    )
    assert res.outputs["dref"].output == seed["seed_doc"]
    # The worker that holds the original rows does not double them on replay.
    after = [(r.node_id, r.status, r.cost_myr) for r in ledger.records_for_run(run_id)]
    assert after == before
    assert ledger.total_cost(run_id) == 0.0


# ---------------------------------------------------------------------------
# Acceptance 3 (LLM path): never two rows for one attempt.
# ---------------------------------------------------------------------------


class _Adapter(LLMAdapter):
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.calls = 0

    async def complete(self, req) -> AdapterResponse:  # type: ignore[no-untyped-def]
        del req
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return AdapterResponse(content="ok", prompt_tokens=10, completion_tokens=20, latency_ms=1, raw={})

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        del prompt_tokens, completion_tokens
        return 0.5


def _llm_dag():
    return _parse(
        [
            {
                "id": "j1",
                "kind": "llm_judged",
                "default_tier": "T3",
                "max_tier": "T3",
                "provider": BIG_API_PLACEHOLDER,
                "prompt": "judge",
                "max_tokens": 10,
                "cost_ceiling_myr": 10.0,
                "inputs": [],
            },
            {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
        ],
        entry="j1",
    )


def _llm_router(adapter: _Adapter) -> ModelRouter:
    return ModelRouter(
        adapter_registry={"anthropic": adapter, "openai": adapter, "self_hosted": adapter},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )


@pytest.mark.asyncio
async def test_llm_node_failure_keeps_the_single_executor_error_row():
    adapter = _Adapter(fail=True)
    ledger = CostLedger()

    with pytest.raises(RuntimeError, match="provider down"):
        await run_dag(_llm_dag(), ExecutionContext("run_llm_err"), _llm_router(adapter), ledger)

    assert adapter.calls == 1
    rows = ledger.records_for_run("run_llm_err")
    assert [(r.node_id, r.status) for r in rows] == [("j1", "error")]
    assert rows[0].error == "provider down"


@pytest.mark.asyncio
async def test_llm_node_success_keeps_the_single_executor_ok_row():
    adapter = _Adapter(fail=False)
    ledger = CostLedger()

    res = await run_dag(_llm_dag(), ExecutionContext("run_llm_ok"), _llm_router(adapter), ledger)

    assert res.outputs["j1"].output["content"] == "ok"
    rows = ledger.records_for_run("run_llm_ok")
    assert [(r.node_id, r.status) for r in rows] == [("j1", "ok"), ("emit", "ok")]
    assert rows[0].cost_myr == pytest.approx(0.5)
    assert ledger.total_cost("run_llm_ok") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# No false positives: legitimate non-LLM work is untouched.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
async def test_healthy_run_of_every_non_llm_kind_has_only_ok_rows(monkeypatch, parallel):
    """document_ref, tool_call (three siblings), rag_retrieve, rag_rerank,
    rag_answer, a2a_call (no target: returns an ok output, never raises) and
    emit all complete with exactly one ok row each and zero spend."""
    _patch_tools(monkeypatch, {})
    corpus = [{"id": "1", "text": "Cortex dag_runner executes nodes"}]
    nodes = [
        {"id": "dref", "kind": "document_ref", "context_key": "seed_doc", "inputs": []},
        *[{"id": nid, "kind": "TOOL_CALL", "tool_name": nid, "inputs": []} for nid in TOOLS],
        {"id": "ret", "kind": "RAG_RETRIEVE", "inputs": []},
        {"id": "rr", "kind": "RAG_RERANK", "inputs": ["ret"]},
        {"id": "ans", "kind": "RAG_ANSWER", "inputs": ["rr"]},
        {"id": "a2a", "kind": "a2a_call", "inputs": ["dref"]},
        {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["dref", *TOOLS, "ans", "a2a"]},
    ]
    dag = _parse(nodes, entry="dref")
    ledger = CostLedger()
    run_id = f"run_healthy_{parallel}"
    ctx = ExecutionContext(
        run_id,
        {"seed_doc": {"k": 1}, "query": "dag_runner nodes", "rag_corpus": corpus},
    )

    res = await run_dag(dag, ctx, _router(), ledger, workflow_cost_ceiling_myr=1.0, parallel=parallel)

    expected = {n["id"] for n in nodes}
    assert set(res.outputs) == expected
    assert res.outputs["a2a"].output["ok"] is False  # missing target is an output, not a failure
    rows = ledger.records_for_run(run_id)
    assert sorted(r.node_id for r in rows) == sorted(expected)
    assert all(r.status == "ok" and r.error is None and r.cost_myr == 0.0 for r in rows)
    assert ledger.total_cost(run_id) == 0.0


# ---------------------------------------------------------------------------
# Verifier finding (#252): replay rows must not break the KEV label join.
# A fresh worker resuming a run writes a ``replayed`` row next to the
# original ``ok`` row in node_executions. The llm_judged node made one
# routing decision (one decision-log entry), so ``build_labels`` must still
# label it rather than exclude the key as ``ambiguous_ledger_join``.
# ---------------------------------------------------------------------------


def _decision_entry(run_id: str, node_id: str = "j1") -> dict:
    return {
        "run_id": run_id,
        "node_id": node_id,
        "status": "ok",
        "tier": "T3",
        "probabilities": {"T3": 0.9},
        "served_tier": "T3",
        "backend": "x",
    }


@pytest.mark.asyncio
async def test_resumed_llm_run_on_fresh_worker_still_labels_its_one_decision(tmp_path, monkeypatch):
    from netie.decision.labels import build_labels
    from netie.execution import step_journal

    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")
    run_id = "run_llm_resume_labels"
    adapter = _Adapter(fail=False)

    first = CostLedger()
    await run_dag(_llm_dag(), ExecutionContext(run_id), _llm_router(adapter), first)
    second = CostLedger()
    res2 = await run_dag(
        _llm_dag(), ExecutionContext(run_id), _llm_router(adapter), second, resume=True
    )

    # One real attempt, served from the journal on resume.
    assert adapter.calls == 1
    assert res2.outputs["j1"].output["content"] == "ok"
    # What node_executions holds across the two workers.
    db_rows = first.records_for_run(run_id) + second.records_for_run(run_id)
    assert [(r.node_id, r.status) for r in db_rows] == [
        ("j1", "ok"),
        ("emit", "ok"),
        ("j1", "replayed"),
        ("emit", "replayed"),
    ]

    labelled = build_labels([_decision_entry(run_id)], ledger_records=db_rows)
    assert dict(labelled.excluded) == {}
    assert [(r.run_id, r.node_id, r.label, r.source) for r in labelled.rows] == [
        (run_id, "j1", 1, "ledger_status")
    ]
    assert labelled.rows[0].p_served == pytest.approx(0.9)


def test_replayed_ledger_row_never_consumes_a_decision_log_row():
    from netie.decision.labels import build_labels

    entry = _decision_entry("r_unit")
    rows = [
        {"run_id": "r_unit", "node_id": "j1", "status": "ok", "error": None},
        {"run_id": "r_unit", "node_id": "j1", "status": "replayed", "error": None},
    ]
    labelled = build_labels([entry], ledger_records=rows)
    assert dict(labelled.excluded) == {}
    assert [(r.node_id, r.label) for r in labelled.rows] == [("j1", 1)]

    # Order-independent: replay row first, then the original.
    labelled = build_labels([entry], ledger_records=list(reversed(rows)))
    assert dict(labelled.excluded) == {}
    assert [(r.node_id, r.label) for r in labelled.rows] == [("j1", 1)]

    # A replay never stands in for the attempt: an errored attempt plus a
    # replay row still labels from the error, never from the replay.
    err_rows = [
        {"run_id": "r_unit", "node_id": "j1", "status": "error", "error": "bad output"},
        {"run_id": "r_unit", "node_id": "j1", "status": "replayed", "error": None},
    ]
    labelled = build_labels([entry], ledger_records=err_rows)
    assert dict(labelled.excluded) == {}
    assert [(r.node_id, r.label) for r in labelled.rows] == [("j1", 0)]

    # Two real attempts against one log row is still ambiguous (unchanged).
    two_ok = rows[:1] * 2
    labelled = build_labels([entry], ledger_records=two_ok)
    assert labelled.rows == []
    assert dict(labelled.excluded) == {"ambiguous_ledger_join": 1}


def test_kev_calibration_report_counts_a_resumed_node_with_replay_rows(tmp_path):
    """The operator-facing report reads node_executions as JSONL; a replayed
    row next to the original ok row must not drop the node from ``n``."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "kev_calibration_report.py"
    log = tmp_path / "decisions.jsonl"
    log.write_text(json.dumps(_decision_entry("r_report")) + "\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"run_id": "r_report", "node_id": "j1", "status": "ok", "error": None},
                {"run_id": "r_report", "node_id": "emit", "status": "ok", "error": None},
                {"run_id": "r_report", "node_id": "j1", "status": "replayed", "error": None},
                {"run_id": "r_report", "node_id": "emit", "status": "replayed", "error": None},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script), "--log", str(log), "--ledger-jsonl", str(ledger)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    lines = proc.stdout.splitlines()
    assert "entries_read=1" in lines, proc.stdout + proc.stderr
    assert "n=1" in lines, proc.stdout + proc.stderr
    assert "excluded none=0" in lines, proc.stdout + proc.stderr
    assert not any(line.startswith("excluded ambiguous_ledger_join") for line in lines)


# ---------------------------------------------------------------------------
# Judge finding (#252): de-duplication is per attempt, not per node. A
# same-worker resume (POST /api/workflows/resume reuses app.state.ledger and
# the run_id) re-executes a node that failed; its retry must get its own row
# next to the earlier error row, never be swallowed by it.
# ---------------------------------------------------------------------------


def _rows_by_node(ledger: CostLedger, run_id: str) -> dict[str, list[tuple[str, str | None]]]:
    out: dict[str, list[tuple[str, str | None]]] = {}
    for r in ledger.records_for_run(run_id):
        out.setdefault(r.node_id, []).append((r.status, r.error))
    return out


def _journal_on(tmp_path, monkeypatch) -> None:
    from netie.execution import step_journal

    monkeypatch.setattr(step_journal, "DEFAULT_DB", tmp_path / "journal.db")
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "1")


@pytest.mark.asyncio
async def test_same_ledger_resume_after_failed_tool_records_error_then_ok(tmp_path, monkeypatch):
    _journal_on(tmp_path, monkeypatch)
    run_id = "run_retry_ok"
    ledger = CostLedger()

    _patch_tools(monkeypatch, {"t2": _BoomTool("tool t2 exploded")})
    with pytest.raises(_BoomTool):
        await run_dag(
            _tool_layer_dag(), ExecutionContext(run_id), _router(), ledger, parallel=True
        )

    # The failure is cleared; the same worker resumes the same run_id.
    calls = _patch_tools(monkeypatch, {})
    res = await run_dag(
        _tool_layer_dag(), ExecutionContext(run_id), _router(), ledger, parallel=True, resume=True
    )

    # The run completes and t2's output is the retry's, not a replay.
    assert set(res.outputs) == {*TOOLS, "emit"}
    assert res.outputs["t2"].output == {"ok": True, "tool": "t2"}
    assert calls == ["t2"], "t1/t3 are served from the journal, only t2 re-runs"

    rows = _rows_by_node(ledger, run_id)
    # One row per attempt: the failed attempt keeps its error row and the
    # successful retry adds its ok row.
    assert rows["t2"] == [("error", "_BoomTool: tool t2 exploded"), ("ok", None)]
    # Replayed siblings keep their single original row on this worker.
    assert rows["t1"] == [("ok", None)]
    assert rows["t3"] == [("ok", None)]
    assert rows["emit"] == [("ok", None)]
    assert ledger.total_cost(run_id) == 0.0


@pytest.mark.asyncio
async def test_same_ledger_resume_that_fails_again_records_two_error_rows(tmp_path, monkeypatch):
    _journal_on(tmp_path, monkeypatch)
    run_id = "run_retry_err"
    ledger = CostLedger()

    _patch_tools(monkeypatch, {"t2": _BoomTool("first")})
    with pytest.raises(_BoomTool, match="first"):
        await run_dag(
            _tool_layer_dag(), ExecutionContext(run_id), _router(), ledger, parallel=True
        )
    _patch_tools(monkeypatch, {"t2": _BoomTool("second")})
    with pytest.raises(_BoomTool, match="second"):
        await run_dag(
            _tool_layer_dag(),
            ExecutionContext(run_id),
            _router(),
            ledger,
            parallel=True,
            resume=True,
        )

    rows = _rows_by_node(ledger, run_id)
    assert rows["t2"] == [("error", "_BoomTool: first"), ("error", "_BoomTool: second")]
    assert rows["t1"] == [("ok", None)]
    assert rows["t3"] == [("ok", None)]
    assert "emit" not in rows


@pytest.mark.asyncio
async def test_llm_node_retry_on_same_ledger_has_one_row_per_attempt(tmp_path, monkeypatch):
    """The executor owns LLM rows; the per-attempt guard must neither double a
    row within an attempt nor drop the retry's row. The KEV label join pairs
    the two attempts ordinally with their two decision-log rows."""
    from netie.decision.labels import build_labels

    _journal_on(tmp_path, monkeypatch)
    run_id = "run_llm_retry"
    ledger = CostLedger()
    adapter = _Adapter(fail=True)

    with pytest.raises(RuntimeError, match="provider down"):
        await run_dag(_llm_dag(), ExecutionContext(run_id), _llm_router(adapter), ledger)
    adapter.fail = False
    res = await run_dag(
        _llm_dag(), ExecutionContext(run_id), _llm_router(adapter), ledger, resume=True
    )

    assert adapter.calls == 2
    assert res.outputs["j1"].output["content"] == "ok"
    rows = ledger.records_for_run(run_id)
    assert [(r.node_id, r.status) for r in rows] == [("j1", "error"), ("j1", "ok"), ("emit", "ok")]
    assert ledger.total_cost(run_id) == pytest.approx(0.5)

    # Two attempts, two log rows, two ledger rows: paired, never ambiguous.
    entries = [
        {**_decision_entry(run_id), "status": "error", "error": "bad output"},
        _decision_entry(run_id),
    ]
    labelled = build_labels(entries, ledger_records=rows)
    assert dict(labelled.excluded) == {}
    assert [(r.node_id, r.label, r.source) for r in labelled.rows] == [
        ("j1", 0, "ledger_status"),
        ("j1", 1, "ledger_status"),
    ]
