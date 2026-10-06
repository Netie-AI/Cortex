"""C-MEM (#290): per-Space memory for tables, formulas, tools and solutions.

Must-fails (each also fails with its guard removed — see
test_c_mem_guard_mutations.py):

* ``test_must_fail_scored_pack_write_refused_curated_ceo`` and
  ``test_must_fail_scored_pack_write_refused`` — a write tagged with a scored
  pack id is refused and nothing is stored.
* ``test_must_fail_space_b_never_reads_space_a`` — all four kinds.

No model is called. The tool entry is built from the recorded C-STAMP OpenVault
response (tests/dms/fixtures/c_stamp_289), which carries real ``served_*``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from CortexOS.memory import space_memory as sm
from CortexOS.memory.space_memory import (
    NOT_IN_SPACE,
    NOT_STEWARD,
    REAL_RESULT_EMPTY,
    REAL_RESULT_MISMATCH,
    REUSED_NOT_VALIDATION,
    SCORED_PACK_WRITE,
    SCORED_ROUND_READ,
    SCORED_ROUND_WRITE,
    SOLUTION_UNVALIDATED,
    SPACE_UNBOUND,
    Actor,
    MemoryRefused,
    SpaceMemory,
)

C_STAMP_CAPTURE = Path(__file__).resolve().parents[1] / "dms" / "fixtures" / "c_stamp_289"
STEWARD = Actor("steward-alice", "steward")
VIEWER = Actor("viewer-bob", "viewer")
SKU_Q = "How many SKUs do we have in inventory?"
SKU_SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"


@pytest.fixture(autouse=True)
def _unscored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_PACKS_ENV, raising=False)


@pytest.fixture
def mem() -> SpaceMemory:
    return SpaceMemory()


def _recorded_route() -> dict[str, Any]:
    body = json.loads((C_STAMP_CAPTURE / "resp_body.json").read_text(encoding="utf-8"))
    assert body["served_provider"] and body["served_model"]
    return body


def _seed_kind(mem: SpaceMemory, space: str, kind: str) -> tuple[str, str]:
    """Write one entry of ``kind`` into ``space``; return (entry_id, key)."""
    if kind == "table":
        entry, _ = mem.write(
            space_id=space,
            kind="table",
            key="inventory",
            body={"columns": ["sku", "qty"], "keys": ["sku"], "joins": [], "row_count": 3},
            source="schema:connected_db/inventory",
            actor="cortex:schema-scan",
        )
    elif kind == "formula":
        entry, _ = mem.write(
            space_id=space,
            kind="formula",
            key="sku_count",
            body={"definition": "COUNT(DISTINCT sku)", "confirmed_by": STEWARD.actor_id},
            source="steward:steward-alice",
            actor=STEWARD.actor_id,
        )
    elif kind == "tool":
        route = _recorded_route()
        entry, _ = mem.write(
            space_id=space,
            kind="tool",
            key="listing",
            body={
                "tool": "l2_generation",
                "question_kind": "listing",
                "passed": True,
                "served_provider": route["served_provider"],
                "served_model": route["served_model"],
                "served_local": route["served_local"],
            },
            source=f"transcript:c_stamp_289/{route['id']}",
            actor="cortex:contract-ask",
        )
    else:
        entry, _ = mem.confirm_solution(
            space_id=space,
            question=SKU_Q,
            sql=SKU_SQL,
            steward=STEWARD,
            source="ask:answer-1",
        )
    return entry.id, entry.key


def _count(mem: SpaceMemory, space: str) -> int:
    return len(mem.steward_view(space_id=space, steward=STEWARD)[0])


# -- must-fail: scored packs are never written ------------------------------
def test_must_fail_scored_pack_write_refused_curated_ceo(mem: SpaceMemory) -> None:
    """The DMS 52-question fixture (pack id ``curated_ceo``) never lands in memory."""
    with pytest.raises(MemoryRefused) as caught:
        mem.write(
            space_id="space-a",
            kind="formula",
            key="revenue",
            body={"definition": "SUM(amount)"},
            source="pack:curated_ceo/q07",
            actor="dms:score_curated",
            pack_id="curated_ceo",
        )
    assert caught.value.code == SCORED_PACK_WRITE
    assert "curated_ceo" in str(caught.value)
    assert _count(mem, "space-a") == 0
    refusal = [s for s in mem.stamps(space_id="space-a") if s.served_op == "refuse"]
    assert [s.served_reason for s in refusal] == [SCORED_PACK_WRITE]


SCORED_TAGS = [
    {"pack_id": "curated_ceo"},
    {"pack_id": "bird_minidev"},
    {"pack_id": "BIRD-minidev"},
    {"pack_id": "e63b422e26336c6cae5f824d1af69f68ee5236544160a0685d0f982fad2ceee7"},
    {"pack_id": "heldout_pack_d"},
    {"pack_id": "cca_eval"},
    {"pack_id": "dms-180-curated-26"},
    {"source": "pack:bird_minidev/california_schools"},
    {"scored_pack_id": "pack_d"},
]


@pytest.mark.parametrize("kind", ["table", "formula", "tool", "solution"])
@pytest.mark.parametrize(
    "tag", SCORED_TAGS, ids=lambda t: "-".join(f"{k}={v[:16]}" for k, v in t.items())
)
def test_must_fail_scored_pack_write_refused(
    mem: SpaceMemory, kind: str, tag: dict[str, str]
) -> None:
    source = tag.get("source", "real-use:ask-1")
    pack_id = tag.get("pack_id")
    scored = tag.get("scored_pack_id")
    with pytest.raises(MemoryRefused) as caught:
        if kind == "solution":
            mem.confirm_solution(
                space_id="space-a",
                question=SKU_Q,
                sql=SKU_SQL,
                steward=STEWARD,
                source=source,
                pack_id=pack_id,
                scored_pack_id=scored,
            )
        else:
            mem.write(
                space_id="space-a",
                kind=kind,
                key="k",
                body={"x": 1},
                source=source,
                actor="a",
                pack_id=pack_id,
                scored_pack_id=scored,
            )
    assert caught.value.code == SCORED_PACK_WRITE
    assert _count(mem, "space-a") == 0


def test_scored_pack_check_solution_refused_before_executing(mem: SpaceMemory) -> None:
    calls: list[str] = []

    def execute(sql: str) -> list[dict[str, Any]]:
        calls.append(sql)
        return [{"sku_count": 3}]

    with pytest.raises(MemoryRefused) as caught:
        mem.check_solution(
            space_id="space-a",
            question=SKU_Q,
            sql=SKU_SQL,
            expected_rows=[{"sku_count": 3}],
            execute=execute,
            source="pack:curated_ceo/q01",
            actor="dms:score_curated",
        )
    assert caught.value.code == SCORED_PACK_WRITE
    assert calls == []
    assert _count(mem, "space-a") == 0


def test_scored_pack_list_only_adds_from_env(
    monkeypatch: pytest.MonkeyPatch, mem: SpaceMemory
) -> None:
    monkeypatch.setenv(sm.SCORED_PACKS_ENV, "acme_prove_v2")
    with pytest.raises(MemoryRefused) as caught:
        mem.write(
            space_id="s",
            kind="table",
            key="t",
            body={},
            source="x",
            actor="a",
            pack_id="acme_prove_v2",
        )
    assert caught.value.code == SCORED_PACK_WRITE
    assert sm.is_scored_pack("curated_ceo")


def test_unscored_source_is_written(mem: SpaceMemory) -> None:
    entry, stamp = mem.write(
        space_id="s",
        kind="table",
        key="orders",
        body={"row_count": 9},
        source="schema:orders",
        actor="a",
    )
    assert entry.version == 1 and stamp.served_op == "write"


# -- must-fail: Space isolation ---------------------------------------------
@pytest.mark.parametrize("kind", ["table", "formula", "tool", "solution"])
def test_must_fail_space_b_never_reads_space_a(mem: SpaceMemory, kind: str) -> None:
    a_id, a_key = _seed_kind(mem, "space-a", kind)

    rows, stamp = mem.read(space_id="space-b", kind=kind, actor="b")
    assert rows == []
    assert stamp.served_space_id == "space-b" and stamp.served_entry_ids == ()
    assert mem.read(space_id="space-b", kind=kind, key=a_key, actor="b")[0] == []

    with pytest.raises(MemoryRefused) as caught:
        mem.get(space_id="space-b", entry_id=a_id, actor="b")
    assert caught.value.code == NOT_IN_SPACE

    assert mem.steward_view(space_id="space-b", steward=STEWARD)[0] == []

    with pytest.raises(MemoryRefused) as caught:
        mem.write(
            space_id="space-b",
            kind="table",
            key="x",
            body={},
            source="s",
            actor="b",
            derived_from=[a_id],
        )
    assert caught.value.code == NOT_IN_SPACE

    with pytest.raises(MemoryRefused) as caught:
        mem.steward_delete(space_id="space-b", entry_id=a_id, steward=STEWARD)
    assert caught.value.code == NOT_IN_SPACE

    calls: list[str] = []
    reuse = mem.reuse_solution(
        space_id="space-b", question=SKU_Q, execute=lambda s: calls.append(s) or [], actor="b"
    )
    assert reuse is None and calls == []

    # Nothing about A leaks into B's stamps; A still holds its entry.
    for s in mem.stamps(space_id="space-b"):
        assert a_id not in s.served_entry_ids
    assert mem.get(space_id="space-a", entry_id=a_id, actor="a")[0].kind == kind


def test_blank_space_is_refused(mem: SpaceMemory) -> None:
    for space in ("", "   ", None):
        with pytest.raises(MemoryRefused) as caught:
            mem.read(space_id=space, kind="table", actor="a")  # type: ignore[arg-type]
        assert caught.value.code == SPACE_UNBOUND


# -- provenance + stamps -----------------------------------------------------
@pytest.mark.parametrize("kind", ["table", "formula", "tool", "solution"])
def test_every_entry_carries_source_version_timestamp_space(mem: SpaceMemory, kind: str) -> None:
    entry_id, key = _seed_kind(mem, "space-a", kind)
    entry, stamp = mem.get(space_id="space-a", entry_id=entry_id, actor="a")
    prov = entry.provenance()
    assert prov["space_id"] == "space-a"
    assert prov["source"]
    assert prov["version"] == 1
    assert prov["written_at"]
    assert stamp.served_op == "read" and stamp.served_entry_ids == (entry_id,)
    assert _seed_kind(mem, "space-a", kind)[0] == entry_id
    assert mem.get(space_id="space-a", entry_id=entry_id, actor="a")[0].version == 2


def test_tool_entry_carries_recorded_served_stamp(mem: SpaceMemory) -> None:
    route = _recorded_route()
    entry_id, _ = _seed_kind(mem, "space-a", "tool")
    entry, _ = mem.get(space_id="space-a", entry_id=entry_id, actor="a")
    assert entry.body["served_provider"] == route["served_provider"] == "groq"
    assert entry.body["served_model"] == route["served_model"] == "openai/gpt-oss-120b"
    assert entry.body["passed"] is True
    assert route["id"] in entry.source


def test_every_read_write_delete_and_refusal_is_stamped(mem: SpaceMemory) -> None:
    t_id, _ = _seed_kind(mem, "space-a", "table")
    mem.read(space_id="space-a", kind="table", actor="reader")
    with pytest.raises(MemoryRefused):
        mem.write(space_id="space-a", kind="solution", key="q", body={}, source="s", actor="w")
    mem.steward_delete(space_id="space-a", entry_id=t_id, steward=STEWARD)
    ops = [(s.served_op, s.served_reason) for s in mem.stamps(space_id="space-a")]
    assert ops == [
        ("write", ""),
        ("read", ""),
        ("refuse", SOLUTION_UNVALIDATED),
        ("delete", ""),
    ]
    for s in mem.stamps(space_id="space-a"):
        assert s.served_space_id == "space-a" and s.served_at and s.served_by


# -- steward view / delete cascade ------------------------------------------
def test_steward_delete_cascades_learned_derivatives(mem: SpaceMemory) -> None:
    table, _ = mem.write(
        space_id="space-a",
        kind="table",
        key="inventory",
        body={"row_count": 3},
        source="schema:inv",
        actor="scan",
    )
    formula, _ = mem.write(
        space_id="space-a",
        kind="formula",
        key="sku_count",
        body={"definition": "COUNT(DISTINCT sku)"},
        source="steward:alice",
        actor="alice",
        derived_from=[table.id],
    )
    solution, _ = mem.confirm_solution(
        space_id="space-a",
        question=SKU_Q,
        sql=SKU_SQL,
        steward=STEWARD,
        source="ask:a1",
        derived_from=[formula.id],
    )
    tool, _ = mem.write(
        space_id="space-a",
        kind="tool",
        key="count",
        body={"tool": "governed_metric", "passed": True},
        source="ask:a1",
        actor="ask",
        derived_from=[solution.id],
    )
    unrelated, _ = mem.write(
        space_id="space-a",
        kind="table",
        key="orders",
        body={},
        source="schema:orders",
        actor="scan",
    )
    viewed, view_stamp = mem.steward_view(space_id="space-a", steward=STEWARD)
    assert {e.id for e in viewed} == {table.id, formula.id, solution.id, tool.id, unrelated.id}
    assert view_stamp.served_op == "view"
    assert mem.get(space_id="space-a", entry_id=tool.id, actor="a")[0].derived_from == (
        solution.id,
    )

    deleted, stamp = mem.steward_delete(space_id="space-a", entry_id=table.id, steward=STEWARD)
    assert set(deleted) == {table.id, formula.id, solution.id, tool.id}
    assert stamp.served_op == "delete" and set(stamp.served_entry_ids) == set(deleted)
    remaining = [e.id for e in mem.steward_view(space_id="space-a", steward=STEWARD)[0]]
    assert remaining == [unrelated.id]
    assert (
        mem.reuse_solution(space_id="space-a", question=SKU_Q, execute=lambda s: [], actor="a")
        is None
    )


def test_only_a_steward_views_or_deletes(mem: SpaceMemory) -> None:
    entry_id, _ = _seed_kind(mem, "space-a", "table")
    for call in (
        lambda: mem.steward_view(space_id="space-a", steward=VIEWER),
        lambda: mem.steward_delete(space_id="space-a", entry_id=entry_id, steward=VIEWER),
    ):
        with pytest.raises(MemoryRefused) as caught:
            call()
        assert caught.value.code == NOT_STEWARD
    assert mem.get(space_id="space-a", entry_id=entry_id, actor="a")[0].id == entry_id


# -- solutions ---------------------------------------------------------------
def test_solution_needs_steward_or_real_result(mem: SpaceMemory) -> None:
    with pytest.raises(MemoryRefused) as caught:
        mem.write(
            space_id="s", kind="solution", key="q", body={"sql": SKU_SQL}, source="ask:1", actor="a"
        )
    assert caught.value.code == SOLUTION_UNVALIDATED
    with pytest.raises(MemoryRefused) as caught:
        mem.write(
            space_id="s",
            kind="solution",
            key="q",
            body={"sql": SKU_SQL},
            source="ask:1",
            actor="a",
            validation="steward_confirm",
        )
    assert caught.value.code == SOLUTION_UNVALIDATED
    with pytest.raises(MemoryRefused) as caught:
        mem.confirm_solution(
            space_id="s", question=SKU_Q, sql=SKU_SQL, steward=VIEWER, source="ask:1"
        )
    assert caught.value.code == NOT_STEWARD
    assert _count(mem, "s") == 0

    entry, stamp = mem.confirm_solution(
        space_id="s", question=SKU_Q, sql=SKU_SQL, steward=STEWARD, source="ask:1"
    )
    assert entry.validation == "steward_confirm" and stamp.served_reason == "steward_confirm"
    assert set(entry.body) == {"question", "sql", "confirmed_by"}


def test_real_result_check_runs_sql_and_compares(mem: SpaceMemory) -> None:
    data = {"rows": [{"sku_count": 3}]}

    def execute(sql: str) -> list[dict[str, Any]]:
        assert sql == SKU_SQL
        return list(data["rows"])

    with pytest.raises(MemoryRefused) as caught:
        mem.check_solution(
            space_id="s",
            question=SKU_Q,
            sql=SKU_SQL,
            expected_rows=[{"n": 4}],
            execute=execute,
            source="real-use:finance-close",
            actor="a",
        )
    assert caught.value.code == REAL_RESULT_MISMATCH
    with pytest.raises(MemoryRefused) as caught:
        mem.check_solution(
            space_id="s",
            question=SKU_Q,
            sql=SKU_SQL,
            expected_rows=[],
            execute=execute,
            source="real-use:finance-close",
            actor="a",
        )
    assert caught.value.code == REAL_RESULT_EMPTY
    assert _count(mem, "s") == 0

    entry, _ = mem.check_solution(
        space_id="s",
        question=SKU_Q,
        sql=SKU_SQL,
        expected_rows=[{"n": 3.0}],
        execute=execute,
        source="real-use:finance-close",
        actor="a",
    )
    assert entry.validation == "real_result_check"
    assert "rows" not in entry.body and "answer" not in entry.body


def test_reuse_reruns_sql_on_current_data_and_is_never_a_validation(mem: SpaceMemory) -> None:
    mem.confirm_solution(space_id="s", question=SKU_Q, sql=SKU_SQL, steward=STEWARD, source="ask:1")
    warehouse = {"sku_count": 3}
    executed: list[str] = []

    def execute(sql: str) -> list[dict[str, Any]]:
        executed.append(sql)
        return [dict(warehouse)]

    first = mem.reuse_solution(
        space_id="s", question="how many skus do we have in inventory", execute=execute, actor="ask"
    )
    warehouse["sku_count"] = 5
    second = mem.reuse_solution(space_id="s", question=SKU_Q, execute=execute, actor="ask")
    assert first is not None and second is not None
    assert executed == [SKU_SQL, SKU_SQL]
    assert first.rows == [{"sku_count": 3}] and second.rows == [{"sku_count": 5}]
    assert first.reused is True and first.validated is False
    assert [s.served_op for s in second.stamps] == ["read", "reuse"]

    for attempt in (
        lambda: mem.write(
            space_id="s",
            kind="formula",
            key="k",
            body={},
            source="ask:2",
            actor="a",
            validation="reused",
        ),
        lambda: mem.confirm_solution(
            space_id="s", question="q2", sql=SKU_SQL, steward=STEWARD, source="reuse:mem_x"
        ),
        lambda: mem.check_solution(
            space_id="s",
            question="q2",
            sql=SKU_SQL,
            expected_rows=second.rows or [],
            execute=execute,
            source="reuse:mem_x",
            actor="a",
        ),
    ):
        with pytest.raises(MemoryRefused) as caught:
            attempt()
        assert caught.value.code == REUSED_NOT_VALIDATION


def test_reuse_exec_failure_is_stamped_not_served(mem: SpaceMemory) -> None:
    mem.confirm_solution(space_id="s", question=SKU_Q, sql=SKU_SQL, steward=STEWARD, source="ask:1")

    def boom(sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("table dropped")

    reuse = mem.reuse_solution(space_id="s", question=SKU_Q, execute=boom, actor="ask")
    assert reuse is not None and reuse.ok is False and reuse.rows is None
    assert reuse.stamps[1].served_reason.startswith(sm.REUSE_EXEC_FAILED)


# -- scored rounds -----------------------------------------------------------
def test_scored_round_keeps_solution_memory_empty(
    monkeypatch: pytest.MonkeyPatch, mem: SpaceMemory
) -> None:
    _seed_kind(mem, "s", "table")
    _seed_kind(mem, "s", "formula")
    _seed_kind(mem, "s", "tool")
    _seed_kind(mem, "s", "solution")
    calls: list[str] = []
    scored_reads = {
        "solution": mem.reuse_solution(
            space_id="s",
            question=SKU_Q,
            execute=lambda q: calls.append(q) or [],
            actor="a",
            scored_pack_id="curated_ceo",
        ),
    }
    assert scored_reads["solution"] is None and calls == []

    monkeypatch.setenv(sm.SCORED_ROUND_ENV, "1")
    assert len(mem.read(space_id="s", kind="table", actor="a")[0]) == 1
    assert len(mem.read(space_id="s", kind="formula", actor="a")[0]) == 1
    for kind in ("tool", "solution"):
        rows, stamp = mem.read(space_id="s", kind=kind, actor="a")
        assert rows == [] and stamp.served_reason == SCORED_ROUND_READ
    for call in (
        lambda: mem.write(
            space_id="s", kind="table", key="new", body={}, source="schema:x", actor="a"
        ),
        lambda: mem.confirm_solution(
            space_id="s", question="q9", sql=SKU_SQL, steward=STEWARD, source="ask:9"
        ),
    ):
        with pytest.raises(MemoryRefused) as caught:
            call()
        assert caught.value.code == SCORED_ROUND_WRITE
