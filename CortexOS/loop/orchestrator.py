"""C-LOOP (#291) analysis-loop orchestrator.

One ask, every step stamped ``served_*`` in run order:

1. ``plan``            — is there a signed grant, is the question allowed, which steps run.
2. ``memory_lookup``   — per-Space table, formula and tool memory (C-MEM #290).
3. ``ontology_lookup`` — the ``data_map`` tool: granted tables, columns, joins, table memory.
4. ``sql``             — re-run a stored solution's SQL, else the injected generator's
                         SQL; either way through the injected executor.
5. ``check``           — the SQL reads only data-map tables; the rows are non-empty,
                         not all-null and finite.
6. ``evaluate``        — a steward formula that applies is re-run and must agree.
   ``self_correct``    — a failed 4-6 goes to the injected self-correct; every
                         candidate it returns repeats 4-6.
7. ``chart_spec``      — tool, on checked rows, when asked for or planned from tool memory.
8. ``memory_write``    — which steps and tools passed, as tool memory. Refused by the
                         C-MEM guard on a scored round or scored pack.
9. ``answer`` (the injected packager) or ``abstain`` with a named code.

The orchestrator holds no model, no provider choice and no database handle.
Generator, self-correct and packager are injected (:mod:`CortexOS.loop.interfaces`).

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
from cortex_contract.answer import AnalysisLoop, LoopStep
from sqlglot import exp

from CortexOS.loop.interfaces import (
    Abstained,
    Candidate,
    Checked,
    Failure,
    Generator,
    LoopReason,
    Packager,
    PlanContext,
    SelfCorrect,
    named,
    no_self_correct,
)
from CortexOS.loop.tools import ToolRegistry, ToolRun, default_registry
from CortexOS.memory.space_memory import (
    MemoryEntry,
    MemoryRefused,
    SpaceMemory,
    question_key,
)

LOOP_ACTOR = "cortex:analysis-loop"
Reason = LoopReason
Executor = Callable[[str], Sequence[Mapping[str, Any]]]

_CHART_ASK = re.compile(r"\b(chart|plot|graph|visuali[sz]e|trend|over time)\b", re.I)


@dataclass(slots=True)
class LoopPorts:
    space_id: str | None
    granted: tuple[str, ...]
    schema: Mapping[str, Any]
    execute: Executor
    generate: Generator
    package: Packager
    self_correct: SelfCorrect = no_self_correct
    memory: SpaceMemory | None = None
    grant_error: str = ""
    blocked: bool = False
    registry: ToolRegistry = field(default_factory=default_registry)


@dataclass(slots=True)
class LoopResult:
    outcome: str
    reason: str | None
    detail: str
    steps: list[LoopStep]
    memory_reads: list[tuple[MemoryEntry, str]]
    candidate: Candidate | None
    checked: Checked | None
    envelope: dict[str, Any] | None
    reused: bool

    @property
    def memory_ids_read(self) -> list[str]:
        return list(dict.fromkeys(e.id for e, _ in self.memory_reads))

    def record(self) -> AnalysisLoop:
        return AnalysisLoop(outcome=self.outcome, steps=self.steps)  # type: ignore[arg-type]


class _Abstain(Exception):
    def __init__(self, reason: str | LoopReason, detail: str) -> None:
        self.code = named(reason)
        self.detail = detail.strip() or "no detail given"
        super().__init__(f"{self.code}: {self.detail}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(fn: object) -> str:
    return str(getattr(fn, "served_by", None) or getattr(fn, "__qualname__", None) or type(fn).__name__)


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


def _entry(e: MemoryEntry) -> dict[str, Any]:
    return {"id": e.id, "key": e.key, "body": dict(e.body)}


class _Round:
    def __init__(self, question: str, ports: LoopPorts, scored_pack_id: str | None, audit_id: str):
        self.question = question
        self.ports = ports
        self.scored_pack_id = scored_pack_id
        self.audit_id = audit_id
        self.steps: list[LoopStep] = []
        self.tool_runs: list[ToolRun] = []
        self.tool_outputs: dict[str, Mapping[str, Any]] = {}
        self.reads: list[tuple[MemoryEntry, str]] = []
        self.candidate: Candidate | None = None
        self.reused = False
        self.want_chart = bool(_CHART_ASK.search(question))
        self.table_mem: list[MemoryEntry] = []
        self.formulas: list[MemoryEntry] = []
        self.nodes: set[str] = set()
        self.context: PlanContext | None = None
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

    def run_tool(self, step: str, tool_id: str, inputs: Mapping[str, Any]) -> ToolRun:
        run = self.ports.registry.run(tool_id, inputs)
        self.tool_runs.append(run)
        if run.ok and run.output is not None:
            self.tool_outputs[tool_id] = run.output
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
            self.stamp("plan", "refused", p.grant_error)
            raise _Abstain(Reason.UNGROUNDED_SESSION, p.grant_error)
        if p.blocked:
            self.stamp("plan", "refused", "destructive or disallowed operation")
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
                "table_memory": [_entry(e) for e in self.table_mem],
            },
        )
        if not run.ok or run.output is None:
            raise _Abstain(Reason.TOOL_FAILED, f"data_map: {run.served_reason}")
        self.nodes = {str(n.get("table")) for n in run.output.get("nodes") or []}
        self.context = PlanContext(
            question=self.question,
            space_id=self.ports.space_id,
            granted=self.ports.granted,
            data_map=run.output,
            table_memory=[_entry(e) for e in self.table_mem],
            formula_memory=[_entry(e) for e in self.formulas],
        )

    def reuse(self) -> tuple[Candidate, list[dict[str, Any]]] | None:
        p = self.ports
        if self.memory is None:
            return None
        assert p.space_id
        reuse = self.memory.reuse_solution(
            space_id=p.space_id,
            question=self.question,
            execute=p.execute,
            actor=LOOP_ACTOR,
            scored_pack_id=self.scored_pack_id,
        )
        if reuse is None:
            return None
        self.reads.append((reuse.entry, reuse.stamps[0].served_at))
        if not (reuse.ok and reuse.rows is not None):
            self.stamp(
                "sql",
                "failed",
                f"stored solution re-run failed, asking the generator: {reuse.error}",
                by=f"memory:{reuse.entry.id}",
                memory_ids=[reuse.entry.id],
                sql=[reuse.sql],
            )
            return None
        cand = Candidate(
            sql=reuse.sql,
            served_by=f"memory:{reuse.entry.id}@v{reuse.entry.version}",
            envelope={"validation": reuse.entry.validation},
        )
        self.candidate, self.reused = cand, True
        rows = [dict(r) for r in reuse.rows]
        self.stamp(
            "sql",
            "ok",
            "re-ran a stored solution on current data (reuse is not a validation)",
            by=cand.served_by,
            memory_ids=[reuse.entry.id],
            sql=[reuse.sql],
            row_count=len(rows),
        )
        return cand, rows

    def accept(self, cand: Candidate) -> Candidate:
        self.candidate, self.reused = cand, False
        if cand.abstain is not None:
            code = named(cand.abstain)
            detail = cand.reason or f"{cand.served_by} declined"
            self.stamp("sql", "refused", detail, by=cand.served_by, sql=[cand.sql] if cand.sql else [])
            raise _Abstain(code, detail)
        if not cand.sql:
            detail = cand.reason or "the generator answered without SQL"
            self.stamp("sql", "refused", detail, by=cand.served_by)
            raise _Abstain(Reason.NOT_A_SQL_ANSWER, detail)
        return cand

    def run_sql(self, cand: Candidate) -> list[dict[str, Any]] | Failure:
        assert cand.sql
        try:
            rows = [dict(r) for r in self.ports.execute(cand.sql)]
        except Exception as exc:  # noqa: BLE001 — a refused or failed statement is never an answer
            detail = f"{type(exc).__name__}: {exc}"
            self.stamp("sql", "failed", detail, by=cand.served_by, sql=[cand.sql])
            return Failure("sql", Reason.SQL_FAILED.value, detail, cand.sql)
        self.stamp("sql", "ok", cand.reason, by=cand.served_by, sql=[cand.sql], row_count=len(rows))
        return rows

    def check(self, cand: Candidate, rows: list[dict[str, Any]]) -> Failure | None:
        assert cand.sql

        def fail(reason: LoopReason, detail: str, row_count: int | None = None) -> Failure:
            self.stamp("check", "failed", detail, row_count=row_count)
            return Failure("check", reason.value, detail, cand.sql)

        tables = sql_tables(cand.sql)
        if not tables:
            return fail(Reason.SQL_NOT_ANALYSABLE, "the SQL could not be analysed for the tables it reads")
        outside = sorted(tables - self.nodes)
        if outside:
            return fail(Reason.UNGRANTED_TABLE, f"SQL reads {', '.join(outside)}, not on this session's data map")
        if not rows:
            return fail(Reason.EMPTY_RESULT, "the SQL returned no rows", 0)
        values = [v for r in rows for v in r.values()]
        if all(v is None for v in values):
            return fail(Reason.NULL_RESULT, "every value in the result is NULL", len(rows))
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            return fail(Reason.NON_FINITE_VALUE, "the result holds a non-finite number", len(rows))
        self.stamp(
            "check",
            "ok",
            f"reads {', '.join(sorted(tables))}; {len(rows)} row(s), finite, not all-null",
            row_count=len(rows),
        )
        return None

    def evaluate(self, cand: Candidate, rows: list[dict[str, Any]]) -> Failure | None:
        applies = [f for f in self.formulas if formula_applies(f, self.question) and f.body.get("sql")]
        if not applies:
            self.stamp("evaluate", "skipped", "no steward formula in this Space applies to the question")
            return None
        formula = applies[0]
        fsql = str(formula.body["sql"])
        try:
            expected = [dict(r) for r in self.ports.execute(fsql)]
        except Exception as exc:  # noqa: BLE001 — an unverifiable answer is not served
            detail = f"steward formula {formula.id} could not be re-run: {type(exc).__name__}: {exc}"
            self.stamp("evaluate", "failed", detail, memory_ids=[formula.id], sql=[fsql])
            return Failure("evaluate", Reason.FORMULA_UNVERIFIABLE.value, detail, cand.sql)
        if not same_result(rows, expected):
            detail = (
                f"result disagrees with steward formula {formula.id} "
                f"({formula.body.get('metric') or formula.key})"
            )
            self.stamp("evaluate", "failed", detail, memory_ids=[formula.id], sql=[fsql], row_count=len(expected))
            return Failure("evaluate", Reason.FORMULA_MISMATCH.value, detail, cand.sql)
        self.stamp(
            "evaluate",
            "ok",
            f"agrees with steward formula {formula.id}",
            memory_ids=[formula.id],
            sql=[fsql],
            row_count=len(expected),
        )
        return None

    def attempt(self, cand: Candidate, rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | Failure:
        if rows is None:
            ran = self.run_sql(cand)
            if isinstance(ran, Failure):
                return ran
            rows = ran
        failure = self.check(cand, rows) or self.evaluate(cand, rows)
        return failure if failure is not None else rows

    def correct(self, cand: Candidate, failure: Failure) -> Candidate:
        assert self.context is not None
        fix = self.ports.self_correct
        by = f"self_correct:{_name(fix)}"
        nxt = fix(self.context, cand, failure)
        if isinstance(nxt, Candidate):
            self.stamp(
                "self_correct",
                "ok",
                f"new candidate after {failure.step} {failure.reason}",
                by=by,
                sql=[nxt.sql] if nxt.sql else [],
            )
            return self.accept(nxt)
        if isinstance(nxt, Abstained):
            code = named(nxt.reason)
            self.stamp("self_correct", "refused", f"{code}: {nxt.detail}", by=by)
            raise _Abstain(code, nxt.detail)
        self.stamp("self_correct", "skipped", f"no correction for {failure.step} {failure.reason}", by=by)
        raise _Abstain(failure.reason, failure.detail)

    def solve(self) -> tuple[Candidate, list[dict[str, Any]]]:
        assert self.context is not None
        first = self.reuse()
        if first is not None:
            cand, rows = first[0], first[1]
        else:
            cand, rows = self.accept(self.ports.generate(self.context)), None
        while True:
            got = self.attempt(cand, rows)
            if not isinstance(got, Failure):
                return cand, got
            cand, rows = self.correct(cand, got), None

    def chart(self, rows: list[dict[str, Any]]) -> None:
        if self.want_chart:
            self.run_tool("chart_spec", "chart_spec", {"rows": rows, "question": self.question})

    def memory_write(self, outcome: str, code: str | None) -> None:
        memory = self.memory
        if memory is None or not self.ports.space_id or not self.looked_up:
            return
        body = {
            "question": self.question,
            "outcome": outcome,
            "abstain_reason": code,
            "steps": {s.served_step: s.served_status for s in self.steps},
            "tools": {t.tool: t.served_status for t in self.tool_runs},
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
    outcome, code, detail = "answer", None, ""
    checked: Checked | None = None
    envelope: dict[str, Any] | None = None
    try:
        r.plan()
        r.memory_lookup()
        r.ontology_lookup()
        cand, rows = r.solve()
        r.chart(rows)
        assert cand.sql and r.context is not None
        checked = Checked(cand, cand.sql, rows, r.reused, dict(r.tool_outputs))
        try:
            envelope = dict(ports.package(r.context, checked))
        except Exception as exc:  # noqa: BLE001 — an answer that cannot be packaged is not served
            raise _Abstain(Reason.PACKAGE_FAILED, f"{type(exc).__name__}: {exc}") from exc
    except _Abstain as stop:
        outcome, code, detail = "abstain", stop.code, stop.detail
        envelope = None
    r.memory_write(outcome, code)
    if code is None:
        assert checked is not None
        r.stamp(
            "answer",
            "ok",
            "checked and evaluated rows packaged",
            by=f"packager:{_name(ports.package)}",
            row_count=len(checked.rows),
        )
    else:
        r.stamp("abstain", "abstain", f"{code}: {detail}")
    return LoopResult(
        outcome=outcome,
        reason=code,
        detail=detail,
        steps=r.steps,
        memory_reads=r.reads,
        candidate=r.candidate,
        checked=checked if code is None else None,
        envelope=envelope,
        reused=r.reused,
    )
