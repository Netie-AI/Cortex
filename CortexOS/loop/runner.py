"""C-LOOP (#291) analysis loop runner.

One ask, worked the way an analyst would, every step stamped in run order:

1. ``plan``            — is there a signed grant, is the question allowed, which steps run.
2. ``memory_lookup``   — per-Space table, formula and tool memory (C-MEM #290).
3. ``ontology_lookup`` — the ``data_map`` tool: granted tables, columns, joins.
4. ``sql``             — re-run a stored solution's SQL, else the engine's governed
                         cascade proposes and runs SQL on the session extract.
5. ``check``           — the SQL reads only mapped tables; the result is non-empty,
                         not all-null and finite.
6. ``evaluate``        — a steward formula that applies is re-run and must agree.
7. ``chart_spec``      — optional tool, on checked rows only.
8. ``memory_write``    — which steps and tools passed, as tool memory. Refused by the
                         C-MEM guard on a scored round or scored pack.
9. ``answer`` or ``abstain`` — an abstain always names a :class:`LoopAbstainReason`.

The runner holds no model, no provider choice and no database handle
(import-linter contract 5). SQL runs through the executor the caller injects;
the proposer is the engine cascade, whose model calls (if any) go through
OpenVault FreeRoute and are never chosen here.

Formula memory body: ``{"metric", "phrases": [...], "sql"}``. A formula applies
when one of its phrases (or its key) appears in the question.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlglot
from cortex_contract.answer import AnalysisLoop, LoopAbstainReason, LoopStep, LoopToolOutput
from sqlglot import exp

from CortexOS.loop.tools import ToolRegistry, ToolRun, default_registry
from CortexOS.memory.space_memory import (
    MemoryEntry,
    MemoryRefused,
    SpaceMemory,
    question_key,
)

LOOP_ACTOR = "cortex:analysis-loop"
Reason = LoopAbstainReason
Executor = Callable[[str], Sequence[Mapping[str, Any]]]

_CHART_ASK = re.compile(r"\b(chart|plot|graph|visuali[sz]e|trend|over time)\b", re.I)


@dataclass(frozen=True, slots=True)
class Candidate:
    """SQL and rows one source proposed for the question, or why it could not."""

    sql: str | None
    rows: list[dict[str, Any]]
    served_by: str
    abstain: LoopAbstainReason | None = None
    reason: str = ""
    envelope: dict[str, Any] = field(default_factory=dict)


Proposer = Callable[[str], Candidate]


@dataclass(slots=True)
class LoopPorts:
    space_id: str | None
    granted: tuple[str, ...]
    schema: Mapping[str, Any]
    execute: Executor
    propose: Proposer
    memory: SpaceMemory | None = None
    grant_error: str = ""
    blocked: bool = False
    registry: ToolRegistry = field(default_factory=default_registry)


@dataclass(slots=True)
class LoopResult:
    outcome: str
    abstain_reason: LoopAbstainReason | None
    detail: str
    steps: list[LoopStep]
    tools: list[LoopToolOutput]
    sql_run: list[str]
    memory_reads: list[tuple[MemoryEntry, str]]
    candidate: Candidate | None
    reused: bool

    @property
    def memory_ids_read(self) -> list[str]:
        return list(dict.fromkeys(e.id for e, _ in self.memory_reads))

    def record(self) -> AnalysisLoop:
        return AnalysisLoop(
            outcome=self.outcome,  # type: ignore[arg-type]
            abstain_reason=self.abstain_reason,
            steps=self.steps,
            tools=self.tools,
            sql_run=self.sql_run,
            memory_ids_read=self.memory_ids_read,
        )


class _Abstain(Exception):
    def __init__(self, reason: LoopAbstainReason, detail: str) -> None:
        if not isinstance(reason, LoopAbstainReason):
            raise TypeError(f"an abstain must name a LoopAbstainReason, got {reason!r}")
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sql_tables(sql: str) -> set[str] | None:
    """Physical tables ``sql`` reads (CTE names removed). ``None`` if unparseable."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.SqlglotError:
        return None
    if tree is None:
        return None
    ctes = {(c.alias_or_name or "").lower() for c in tree.find_all(exp.CTE)}
    names = {(t.name or "").lower() for t in tree.find_all(exp.Table)}
    return {n for n in names if n and n not in ctes}


def _value_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return str(a).strip() == str(b).strip()


def same_result(actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]) -> bool:
    """Same rows as a multiset of value tuples (column names ignored, order kept)."""
    if len(actual) != len(expected):
        return False

    def key(row: Mapping[str, Any]) -> str:
        return repr([str(v) if not isinstance(v, (int, float)) else round(float(v), 6) for v in row.values()])

    left = sorted((dict(r) for r in actual), key=key)
    right = sorted((dict(r) for r in expected), key=key)
    for a, b in zip(left, right, strict=True):
        av, bv = list(a.values()), list(b.values())
        if len(av) != len(bv) or not all(_value_equal(x, y) for x, y in zip(av, bv, strict=True)):
            return False
    return True


def formula_applies(entry: MemoryEntry, question: str) -> bool:
    body = entry.body
    phrases = [str(p) for p in body.get("phrases") or []] or [entry.key, str(body.get("metric") or "")]
    q = f" {question_key(question)} "
    return any(
        p.strip() and re.search(rf"(?<!\w){re.escape(question_key(p))}(?!\w)", q) for p in phrases
    )


class _Round:
    def __init__(self, question: str, ports: LoopPorts, scored_pack_id: str | None, audit_id: str):
        self.question = question
        self.ports = ports
        self.scored_pack_id = scored_pack_id
        self.audit_id = audit_id
        self.steps: list[LoopStep] = []
        self.tools: list[LoopToolOutput] = []
        self.sql_run: list[str] = []
        self.reads: list[tuple[MemoryEntry, str]] = []
        self.candidate: Candidate | None = None
        self.reused = False
        self.want_chart = bool(_CHART_ASK.search(question))
        self.table_mem: list[MemoryEntry] = []
        self.formulas: list[MemoryEntry] = []
        self.nodes: set[str] = set()
        self.looked_up = False
        self.memory = ports.memory if ports.space_id else None
        self.tool_key = f"loop:{question_key(question)}"

    # -- stamping -------------------------------------------------------------
    def stamp(
        self,
        step: str,
        status: str,
        reason: str = "",
        *,
        by: str = LOOP_ACTOR,
        tool: str | None = None,
        memory_ids: Sequence[str] = (),
        sql: Sequence[str] = (),
        row_count: int | None = None,
    ) -> None:
        self.steps.append(
            LoopStep(
                seq=len(self.steps) + 1,
                served_step=step,
                served_status=status,  # type: ignore[arg-type]
                served_by=by,
                served_at=_now(),
                served_reason=reason,
                served_tool=tool,
                served_memory_ids=list(memory_ids),
                served_sql=list(sql),
                served_row_count=row_count,
            )
        )
        self.sql_run.extend(s for s in sql if s)

    def run_tool(self, step: str, tool_id: str, inputs: Mapping[str, Any]) -> ToolRun:
        run = self.ports.registry.run(tool_id, inputs)
        self.tools.append(
            LoopToolOutput(
                tool=tool_id,
                served_status=run.served_status,  # type: ignore[arg-type]
                served_reason=run.served_reason,
                output=run.output,
            )
        )
        self.stamp(step, run.served_status, run.served_reason, by=f"tool:{tool_id}", tool=tool_id)
        return run

    def _read(self, kind: str, key: str | None = None) -> list[MemoryEntry]:
        memory = self.memory
        assert memory is not None and self.ports.space_id
        entries, stamp = memory.read(
            space_id=self.ports.space_id,
            kind=kind,
            actor=LOOP_ACTOR,
            key=key,
            scored_pack_id=self.scored_pack_id,
        )
        self.reads.extend((e, stamp.served_at) for e in entries)
        return entries

    # -- steps ----------------------------------------------------------------
    def plan(self) -> None:
        p = self.ports
        if p.grant_error:
            self.stamp("plan", "abstain", p.grant_error)
            raise _Abstain(Reason.UNGROUNDED_SESSION, p.grant_error)
        if p.blocked:
            self.stamp("plan", "abstain", "destructive or disallowed operation")
            raise _Abstain(Reason.POLICY_BLOCKED, "destructive or disallowed operation")
        scored = bool((self.scored_pack_id or "").strip()) or bool(
            self.memory and self.memory.is_scored_round(self.scored_pack_id)
        )
        planned = ["memory_lookup", "ontology_lookup", "sql", "check", "evaluate"]
        if self.want_chart:
            planned.append("chart_spec")
        planned.append("memory_write")
        self.stamp(
            "plan",
            "ok",
            f"space={p.space_id or '-'}; granted={','.join(p.granted)}; scored_round={scored}; "
            f"steps={','.join(planned)}",
        )

    def memory_lookup(self) -> None:
        if self.memory is None:
            why = "per-Space memory is off" if self.ports.space_id else "no Space on the signed grant"
            self.stamp("memory_lookup", "skipped", why)
            return
        self.looked_up = True
        self.table_mem = self._read("table")
        self.formulas = self._read("formula")
        tools = self._read("tool", key=self.tool_key)
        notes = [f"table={len(self.table_mem)}", f"formula={len(self.formulas)}", f"tool={len(tools)}"]
        if tools and (tools[0].body.get("tools") or {}).get("chart_spec") == "ok" and not self.want_chart:
            self.want_chart = True
            notes.append(f"chart_spec planned from tool memory {tools[0].id}")
        self.stamp(
            "memory_lookup",
            "ok",
            "; ".join(notes),
            memory_ids=[e.id for e in (*self.table_mem, *self.formulas, *tools)],
        )

    def ontology_lookup(self) -> None:
        run = self.run_tool(
            "ontology_lookup",
            "data_map",
            {
                "granted": list(self.ports.granted),
                "schema": dict(self.ports.schema),
                "table_memory": [
                    {"id": e.id, "key": e.key, "body": dict(e.body)} for e in self.table_mem
                ],
            },
        )
        if not run.ok or run.output is None:
            raise _Abstain(Reason.TOOL_FAILED, f"data_map: {run.served_reason}")
        self.nodes = {str(n.get("table")) for n in run.output.get("nodes") or []}

    def sql(self) -> Candidate:
        p = self.ports
        if self.memory is not None:
            assert p.space_id
            reuse = self.memory.reuse_solution(
                space_id=p.space_id,
                question=self.question,
                execute=p.execute,
                actor=LOOP_ACTOR,
                scored_pack_id=self.scored_pack_id,
            )
            if reuse is not None:
                self.reads.append((reuse.entry, reuse.stamps[0].served_at))
                if reuse.ok and reuse.rows is not None:
                    self.reused = True
                    self.candidate = Candidate(
                        sql=reuse.sql,
                        rows=list(reuse.rows),
                        served_by=f"memory:{reuse.entry.id}@v{reuse.entry.version}",
                        envelope={"validation": reuse.entry.validation},
                    )
                    self.stamp(
                        "sql",
                        "ok",
                        "re-ran a stored solution on current data (reuse is not a validation)",
                        by=self.candidate.served_by,
                        memory_ids=[reuse.entry.id],
                        sql=[reuse.sql],
                        row_count=len(reuse.rows),
                    )
                    return self.candidate
                self.stamp(
                    "sql",
                    "failed",
                    f"stored solution re-run failed, asking the engine: {reuse.error}",
                    by=f"memory:{reuse.entry.id}",
                    memory_ids=[reuse.entry.id],
                    sql=[reuse.sql],
                )
        cand = p.propose(self.question)
        self.candidate = cand
        if cand.abstain is not None:
            self.stamp("sql", "abstain", cand.reason, by=cand.served_by, sql=[cand.sql] if cand.sql else [])
            raise _Abstain(cand.abstain, cand.reason)
        if not cand.sql:
            self.stamp("sql", "abstain", "the engine answered without SQL", by=cand.served_by)
            raise _Abstain(Reason.NOT_A_SQL_ANSWER, cand.reason or "the engine answered without SQL")
        self.stamp("sql", "ok", cand.reason, by=cand.served_by, sql=[cand.sql], row_count=len(cand.rows))
        return cand

    def check(self, cand: Candidate) -> None:
        assert cand.sql
        tables = sql_tables(cand.sql)
        if tables is None or not tables:
            self.stamp("check", "abstain", "the SQL could not be analysed for the tables it reads")
            raise _Abstain(Reason.SQL_NOT_ANALYSABLE, "the SQL could not be analysed")
        outside = sorted(tables - self.nodes)
        if outside:
            detail = f"SQL reads {', '.join(outside)}, not on this session's data map"
            self.stamp("check", "abstain", detail)
            raise _Abstain(Reason.UNGRANTED_TABLE, detail)
        if not cand.rows:
            self.stamp("check", "abstain", "the SQL returned no rows", row_count=0)
            raise _Abstain(Reason.EMPTY_RESULT, "the SQL returned no rows")
        values = [v for r in cand.rows for v in r.values()]
        if all(v is None for v in values):
            self.stamp("check", "abstain", "every value in the result is NULL", row_count=len(cand.rows))
            raise _Abstain(Reason.NULL_RESULT, "every value in the result is NULL")
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            self.stamp("check", "abstain", "the result holds a non-finite number", row_count=len(cand.rows))
            raise _Abstain(Reason.NON_FINITE_VALUE, "the result holds a non-finite number")
        self.stamp(
            "check",
            "ok",
            f"reads {', '.join(sorted(tables))}; {len(cand.rows)} row(s), finite, not all-null",
            row_count=len(cand.rows),
        )

    def evaluate(self, cand: Candidate) -> None:
        applies = [f for f in self.formulas if formula_applies(f, self.question) and f.body.get("sql")]
        if not applies:
            self.stamp("evaluate", "skipped", "no steward formula in this Space applies to the question")
            return
        formula = applies[0]
        fsql = str(formula.body["sql"])
        try:
            expected = [dict(r) for r in self.ports.execute(fsql)]
        except Exception as exc:  # noqa: BLE001 — an unverifiable answer is not served
            detail = f"steward formula {formula.id} could not be re-run: {type(exc).__name__}: {exc}"
            self.stamp("evaluate", "abstain", detail, memory_ids=[formula.id], sql=[fsql])
            raise _Abstain(Reason.FORMULA_UNVERIFIABLE, detail) from exc
        if not same_result(cand.rows, expected):
            detail = (
                f"result disagrees with steward formula {formula.id} "
                f"({formula.body.get('metric') or formula.key})"
            )
            self.stamp(
                "evaluate", "abstain", detail, memory_ids=[formula.id], sql=[fsql], row_count=len(expected)
            )
            raise _Abstain(Reason.FORMULA_MISMATCH, detail)
        self.stamp(
            "evaluate",
            "ok",
            f"agrees with steward formula {formula.id}",
            memory_ids=[formula.id],
            sql=[fsql],
            row_count=len(expected),
        )

    def chart(self, cand: Candidate) -> None:
        if self.want_chart:
            self.run_tool("chart_spec", "chart_spec", {"rows": cand.rows, "question": self.question})

    def memory_write(self, outcome: str, reason: LoopAbstainReason | None) -> None:
        memory = self.memory
        if memory is None or not self.ports.space_id or not self.looked_up:
            return
        body = {
            "question": self.question,
            "outcome": outcome,
            "abstain_reason": reason.value if reason else None,
            "steps": {s.served_step: s.served_status for s in self.steps},
            "tools": {t.tool: t.served_status for t in self.tools},
        }
        try:
            entry, _ = memory.write(
                space_id=self.ports.space_id,
                kind="tool",
                key=self.tool_key,
                body=body,
                source=f"loop:{self.audit_id}",
                actor=LOOP_ACTOR,
                scored_pack_id=self.scored_pack_id,
            )
        except MemoryRefused as exc:
            self.stamp("memory_write", "refused", exc.code)
            return
        self.stamp("memory_write", "ok", "recorded steps and tools as tool memory", memory_ids=[entry.id])


def run_analysis_loop(
    question: str,
    *,
    ports: LoopPorts,
    audit_id: str,
    scored_pack_id: str | None = None,
) -> LoopResult:
    r = _Round(question, ports, scored_pack_id, audit_id)
    outcome, reason, detail = "answer", None, ""
    try:
        r.plan()
        r.memory_lookup()
        r.ontology_lookup()
        cand = r.sql()
        r.check(cand)
        r.evaluate(cand)
        r.chart(cand)
    except _Abstain as stop:
        outcome, reason, detail = "abstain", stop.reason, stop.detail
    r.memory_write(outcome, reason)
    if reason is None:
        rows = len(r.candidate.rows) if r.candidate else None
        r.stamp("answer", "ok", "checked and evaluated rows served", row_count=rows)
    else:
        r.stamp("abstain", "abstain", f"{reason.value}: {detail}")
    return LoopResult(
        outcome=outcome,
        abstain_reason=reason,
        detail=detail,
        steps=r.steps,
        tools=r.tools,
        sql_run=r.sql_run,
        memory_reads=r.reads,
        candidate=r.candidate,
        reused=r.reused,
    )
