"""CORTEX-COT-CLIMB: CoT / route / improve above FreeRoute (Cortex #212).

Consumes the public Crew FreeRoute adapter (``arming``, ``complete``,
``extract_sql``, ``validate_sql``). Does not own arming, measured pick,
identity, or engine generative-ask wiring. Those are PR #215 / #211.

Think-path loop:
  think (FreeRoute purpose=think) -> generate SQL (purpose=generative_ask)
  -> ontology validate -> gen_cfsm.route_step -> improve or stop.

Reuses G1 already on tip: ``generate_ir`` / ``compile_ir`` / ``execute_cfsm``
(``dag_runner``) / ``route_step`` / ``collapse_score``. JEPA stays the cosine
proxy (PARKING_LOT P21). Unarmed is fail-closed: no invented CoT, no SQL, no
values. Validated SQL is ABSTAIN (not executed). Never CERTIFIED. Never
COMPLETE. DMS #180 gen 57.69% / exact 38.46% WRONG=0 @ d2f116a6 is the frozen
baseline; this module does not replace it with a better %.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from CortexOS.execution.gen_cfsm import (
    ALLOWED_HORIZONS,
    DECISION_AUDIT_FAIL,
    DECISION_CONTINUE,
    DECISION_FORCE_AUDIT,
    DECISION_REGENERATE,
    DECISION_TERMINATE,
    collapse_score,
    compile_ir,
    execute_cfsm,
    generate_ir,
    route_step,
)
from CortexOS.execution.scoreboard import embed_goal

HORIZON = 3
JEPA_PATH = "proxy"
SLICE = "CORTEX-COT-CLIMB"


def _baseline() -> dict[str, Any]:
    from CortexOS.crew import freeroute as fr

    body = dict(fr.DMS_180_BASELINE)
    body.setdefault("cite", "DMS #180 Formal GREEN @ d2f116a6")
    body.setdefault("gen", "57.69%")
    body.setdefault("exact", "38.46%")
    body.setdefault("wrong", 0)
    return body


def public_map() -> dict[str, Any]:
    """GET-shaped law. No model call. No numbers invented."""
    return {
        "ok": True,
        "slice": SLICE,
        "issue": 212,
        "execute": "POST /crew/insights {generate:true} -> cot_climb.climb",
        "insights_wire": (
            "generate=true runs CoT/route/improve via climb; FreeRoute G1-G6 stay on #215 @ 50267289"
        ),
        "complete": False,
        "status": "INCOMPLETE",
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "jepa": "proxy cosine via gen_cfsm.collapse_score; no trained path named",
        "gencfsm": (
            "reuse generate_ir, compile_ir, execute_cfsm/dag_runner, route_step"
        ),
        "freeroute": "consume crew.freeroute public API only; unarmed fail-closed",
        "excel_ppt": "deferred #197 #198 #199",
        "live_5000_ci": False,
        "dms_sot": False,
        "issue_211_complete": False,
        "issue_212_complete": False,
    }


def _ranked_columns(ranking: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in ranking.get("locations") or []:
        where = row.get("where") or {}
        table = str(where.get("table") or row.get("id") or "").lower()
        if not table:
            continue
        cols = [str(c) for c in (where.get("columns") or []) if str(c).strip()]
        if cols:
            seen = out.setdefault(table, [])
            seen.extend(c for c in cols if c not in seen)
    return out


async def _call_runner(
    runner: Any,
    *,
    purpose: str,
    prompt: str,
    bearer: str | None,
) -> dict[str, Any]:
    try:
        out = await runner(None, purpose=purpose, prompt=prompt, bearer=bearer)
    except TypeError:
        out = await runner(None, purpose=purpose, prompt=prompt)
    return out if isinstance(out, dict) else {}


def _allowed_tables(ranking: Mapping[str, Any]) -> set[str]:
    tables: set[str] = set()
    for row in ranking.get("locations") or []:
        table = ((row.get("where") or {}).get("table") or row.get("id") or "")
        if table:
            tables.add(str(table).lower())
    for row in list(ranking.get("metrics") or []) + list(ranking.get("certified") or []):
        for table in (row.get("where") or {}).get("tables") or []:
            tables.add(str(table).lower())
    return tables


def _ontology_lines(ranking: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in ranking.get("locations") or []:
        where = row.get("where") or {}
        table = where.get("table") or row.get("id")
        cols = where.get("columns") or []
        lines.append(f"- {table}: {', '.join(str(c) for c in cols[:24])}")
    return lines


def _think_prompt(intent: str, ranking: Mapping[str, Any]) -> str:
    lines = [
        "Think which ontology tables and metrics answer the intent.",
        "Do not emit SQL. Do not invent warehouse numbers, keys, or live CI.",
        "ONTOLOGY:",
        *_ontology_lines(ranking),
        f"INTENT: {intent}",
    ]
    return "\n".join(lines)


def _sql_prompt(intent: str, ranking: Mapping[str, Any], critique: str = "") -> str:
    lines = [
        "ONTOLOGY (use only these tables and columns):",
        *_ontology_lines(ranking),
        "",
        f"INTENT: {intent}",
        "Emit one DuckDB SELECT. SQL only. No invented numeric answers.",
    ]
    if critique.strip():
        lines.extend(["", "PRIOR REFUSAL (improve, do not repeat):", critique.strip()])
    return "\n".join(lines)


def _pct(num: int, den: int) -> str:
    if den <= 0:
        return "0.00%"
    return f"{(100.0 * num / den):.2f}%"


def coverage_report(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Report this-run fixture coverage vs frozen #180 baseline. Never replaces it."""
    n = len(outcomes)
    validated = sum(1 for row in outcomes if row.get("valid"))
    wrong = sum(1 for row in outcomes if row.get("wrong"))
    gen_label = _pct(validated, n)
    return {
        "ok": True,
        "complete": False,
        "status": "INCOMPLETE",
        "slice": SLICE,
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "like_with_like": False,
        "this_run": {
            "n": n,
            "validated": validated,
            "wrong": wrong,
            "gen": gen_label,
            "corpus": "cortex-cot-climb fixture, not DMS #180 curated 26",
        },
        "vs_baseline": {
            "baseline_gen": "57.69%",
            "baseline_exact": "38.46%",
            "baseline_wrong": 0,
            "this_run_gen": gen_label,
            "note": "different corpus; do not replace DMS #180 numbers",
        },
        "jepa": JEPA_PATH,
        "issue_211_complete": False,
        "issue_212_complete": False,
        "outcomes": [dict(row) for row in outcomes],
    }


def _envelope(
    *,
    ok: bool,
    status: str,
    arm: Mapping[str, Any],
    identity: str,
    climb: Mapping[str, Any],
    sql: str | None = None,
    valid: bool = False,
    route: Any = None,
    refuse_reason: str = "",
    tables: list[str] | None = None,
    note: str = "",
    stamp: Any = None,
    check: str = "",
    validator: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "phase": "generate",
        "sql": sql,
        "valid": valid,
        "values": [],
        "identity": identity,
        "route": route,
        "stamp": stamp if isinstance(stamp, dict) else None,
        "check": check,
        "validator": validator,
        "refuse_reason": refuse_reason,
        "arming": dict(arm),
        "text": "",
        "tables": tables or [],
        "note": note,
        "climb": dict(climb),
        "measured_baseline": _baseline(),
        "complete": False,
    }
    return body


def _climb_meta(**extra: Any) -> dict[str, Any]:
    body = {
        "slice": SLICE,
        "horizon": HORIZON,
        "jepa": JEPA_PATH,
        "gencfsm": "reused",
        "complete": False,
        "status": "INCOMPLETE",
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "steps": [],
        "final": None,
        "g1": None,
    }
    body.update(extra)
    return body


async def _g1_skeleton(intent: str, run_id: str) -> dict[str, Any]:
    """Reuse G1 GENERATE -> COMPILE -> dag_runner execute. Not a model call."""
    ir = generate_ir(intent, HORIZON)
    compiled = compile_ir(ir)
    if not compiled["ok"]:
        return {
            "ok": False,
            "stage": "compile",
            "errors": compiled.get("errors") or [],
            "horizon": HORIZON,
            "node_count": len(ir.get("nodes") or []),
        }
    try:
        report = await execute_cfsm(
            ir,
            {"prompt": intent, "session_id": run_id},
            predicates=[{"type": "nonempty"}],
        )
    except Exception as exc:  # noqa: BLE001 - skeleton must not invent-green
        return {
            "ok": False,
            "stage": "execute",
            "errors": [f"execute:{exc}"],
            "horizon": HORIZON,
        }
    return {
        "ok": bool(report.get("ok")),
        "stage": report.get("stage"),
        "verdict": report.get("verdict"),
        "horizon": report.get("horizon") or HORIZON,
        "node_count": report.get("node_count"),
        "errors": report.get("errors") or [],
    }


async def climb(
    intent: str,
    ranking: Mapping[str, Any],
    *,
    complete: Any | None = None,
    bearer: str | None = None,
) -> dict[str, Any]:
    """CoT/route/improve through FreeRoute. Fail-closed when unarmed."""
    from CortexOS.crew import freeroute as fr

    text = (intent or "").strip()
    identity = fr.identity_for("generative_ask")
    arm = fr.arming()
    if not arm.get("armed"):
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=identity,
            climb=_climb_meta(final="UNARMED", reason="FreeRoute unarmed"),
            refuse_reason=(
                "OpenVault FreeRoute unarmed: "
                + str(arm.get("detail") or "unreachable")
                + " (no invent-green keys; no invent-green CoT)"
            ),
        )

    run_id = "cot-" + uuid.uuid4().hex[:8]
    g1 = await _g1_skeleton(text or "empty", run_id)
    if not g1.get("ok") and g1.get("stage") == "compile":
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=identity,
            climb=_climb_meta(final="G1_COMPILE_FAIL", g1=g1),
            refuse_reason="gen_cfsm compile refused the think-path IR",
        )

    allowed = _allowed_tables(ranking)
    if not allowed:
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=identity,
            climb=_climb_meta(final="NO_ONTOLOGY", g1=g1),
            refuse_reason=(
                "no ranked ontology tables; cannot prove SQL stays in scope "
                "(no invent-green CoT)"
            ),
        )
    runner = complete or fr.complete
    columns = _ranked_columns(ranking)
    steps: list[dict[str, Any]] = []
    think_text = ""
    think = await _call_runner(
        runner, purpose="think", prompt=_think_prompt(text, ranking), bearer=bearer
    )
    if not think.get("ok"):
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=think.get("identity") or identity,
            route=think.get("route"),
            stamp=think.get("stamp"),
            climb=_climb_meta(final="THINK_REFUSE", g1=g1, steps=steps),
            refuse_reason=str(think.get("refused") or "FreeRoute think refused"),
        )
    think_text = str(think.get("text") or "")
    steps.append(
        {
            "step": 0,
            "kind": "think",
            "purpose": "think",
            "ok": True,
            "decision": DECISION_CONTINUE,
        }
    )

    critique = ""
    prev_collapse = 0.0
    stall = 0
    last_reason = "no sql"
    goal_blob = text + " " + " ".join(sorted(allowed))
    goal_vec = embed_goal(goal_blob)

    for step in range(1, HORIZON + 1):
        stamps: list[Any] = []
        try:
            from CortexOS.integrations import freeroute as core

            journal = core.journal()
        except Exception:  # noqa: BLE001 - journal is optional on this consumer
            journal = None
        if journal is not None:
            with journal as stamps:
                gen = await _call_runner(
                    runner,
                    purpose="generative_ask",
                    prompt=_sql_prompt(text, ranking, critique),
                    bearer=bearer,
                )
        else:
            gen = await _call_runner(
                runner,
                purpose="generative_ask",
                prompt=_sql_prompt(text, ranking, critique),
                bearer=bearer,
            )
        if not gen.get("ok"):
            return _envelope(
                ok=False,
                status="REFUSE",
                arm=arm,
                identity=gen.get("identity") or identity,
                route=gen.get("route"),
                stamp=gen.get("stamp"),
                climb=_climb_meta(
                    final="GENERATE_REFUSE",
                    g1=g1,
                    steps=steps,
                ),
                refuse_reason=str(gen.get("refused") or "FreeRoute generative_ask refused"),
            )

        sql = fr.extract_sql(str(gen.get("text") or ""))
        checked = fr.validate_sql(sql or "", allowed, columns=columns)
        try:
            from CortexOS.integrations import freeroute as core

            core.note_verdict(stamps, "static_valid" if checked.get("ok") else "static_fail")
        except Exception:  # noqa: BLE001 - scoring miss is not invent-green SQL
            pass
        predicates_pass = bool(checked.get("ok"))
        state_blob = think_text + " " + str(checked.get("sql") or sql or "")
        collapse = collapse_score(embed_goal(state_blob), goal_vec)
        routed = route_step(
            collapse=collapse,
            prev_collapse=prev_collapse,
            predicates_pass=predicates_pass,
            step_count=step,
            horizon=HORIZON,
            stall_count=stall,
        )
        stall = int(routed.get("stall_count") or 0)
        prev_collapse = collapse
        last_reason = str(checked.get("reason") or last_reason)
        granted = DECISION_TERMINATE if predicates_pass else routed["decision"]
        steps.append(
            {
                "step": step,
                "kind": "generate",
                "purpose": "generative_ask",
                "ok": predicates_pass,
                "collapse": round(collapse, 6),
                "decision": routed["decision"],
                "granted": granted,
                "reason": last_reason,
            }
        )
        if predicates_pass:
            return _envelope(
                ok=True,
                status="ABSTAIN",
                arm=arm,
                identity=gen.get("identity") or identity,
                route=gen.get("route"),
                stamp=gen.get("stamp"),
                sql=str(checked.get("sql") or ""),
                valid=True,
                tables=list(checked.get("tables") or []),
                check=str(checked.get("check") or ""),
                note=(
                    "Validated SQL via FreeRoute CoT/route/improve. "
                    "Numbers not certified (not executed). not COMPLETE."
                ),
                climb=_climb_meta(
                    final=DECISION_TERMINATE,
                    g1=g1,
                    steps=steps,
                    attempts=step,
                ),
            )
        if granted in (DECISION_FORCE_AUDIT,) or step >= HORIZON:
            break
        if granted in (
            DECISION_CONTINUE,
            DECISION_REGENERATE,
            DECISION_AUDIT_FAIL,
        ):
            critique = last_reason
            continue
        break

    return _envelope(
        ok=False,
        status="REFUSE",
        arm=arm,
        identity=identity,
        climb=_climb_meta(
            final=DECISION_FORCE_AUDIT,
            g1=g1,
            steps=steps,
            attempts=HORIZON,
        ),
        refuse_reason=(
            "CoT/route/improve exhausted horizon without ontology-valid SQL: "
            + last_reason
            + " (no invent-green SQL; not COMPLETE)"
        ),
    )


async def measure_climb(cases: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Fixture coverage vs frozen #180 baseline. Not a live :5000 CI claim."""
    outcomes: list[dict[str, Any]] = []
    for case in cases:
        out = await climb(
            str(case.get("intent") or ""),
            case.get("ranking") or {},
            complete=case.get("complete"),
        )
        wrong = bool(out.get("values")) or out.get("status") == "CERTIFIED"
        outcomes.append(
            {
                "id": case.get("id") or "",
                "ok": bool(out.get("ok")),
                "valid": bool(out.get("valid")),
                "wrong": wrong,
                "status": out.get("status"),
                "final": (out.get("climb") or {}).get("final"),
            }
        )
    return coverage_report(outcomes)


assert HORIZON in ALLOWED_HORIZONS
