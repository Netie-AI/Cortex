"""Racing router — predict top-3 architectures, probe light, scale the winner.

The G1 racing gate: for a novel goal the scoreboard prior ranks candidate
presets, each runs one cheap token-capped probe through the existing
execute_run_plan dispatch (no third orchestrator), probes are scored with
predicates first (an LLM judge may only refine or fill in — a failed
predicate is always a 0), and the winner is re-run at full scale. Every
probe and scaled run lands in the scoreboard; every scaled run teaches the
family centroid, which is how known SOPs stop racing and route direct.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from CortexOS.execution import scoreboard
from CortexOS.execution.preset_router import plan_for_request
from CortexOS.execution.run_plan import execute_run_plan

PROBE_TOKEN_CAP = 256
COLD_START_ORDER: tuple[str, ...] = ("minimal", "sequential", "dag")

Judge = Callable[[Mapping[str, Any]], float]


def rank_candidates(task_family: str, *, k: int = 3) -> list[str]:
    """History first (score desc, cost asc, runs desc), cold-start fill after.

    Presets whose adapter is not implemented are dropped. Every probe costs a
    real token budget and records a run, so racing a permanent 501 both wastes
    the budget and writes a loss that drags the family's score down — teaching
    the scoreboard something about the adapter rather than about the task.
    """
    from CortexOS.execution.architecture_presets import resolve_runner, runner_available

    def _executable(preset: str) -> bool:
        try:
            return runner_available(str(resolve_runner(preset)["runner"]))
        except Exception:  # noqa: BLE001 — an unknown preset is not raceable either
            return False

    stats = scoreboard.family_stats(task_family)
    ranked = [
        preset
        for preset, s in sorted(
            stats.items(),
            key=lambda kv: (-kv[1]["mean_score"], kv[1]["mean_cost_myr"], -kv[1]["runs"]),
        )
        if s["mean_score"] > 0
    ]
    for preset in COLD_START_ORDER:
        if preset not in ranked:
            ranked.append(preset)
    return [p for p in ranked if _executable(p)][:k]


def eval_predicates(output: Any, predicates: Sequence[Mapping[str, Any]]) -> bool:
    """Deterministic goal checks. Unknown predicate types fail closed."""
    text = "" if output is None else str(output)
    for pred in predicates:
        kind = str(pred.get("type") or "")
        if kind == "nonempty":
            if not output:
                return False
        elif kind == "contains":
            if str(pred.get("value") or "") not in text:
                return False
        elif kind == "equals":
            if output != pred.get("value") and text != str(pred.get("value")):
                return False
        else:
            return False
    return True


def score_probe(
    result: Mapping[str, Any],
    predicates: Sequence[Mapping[str, Any]] | None = None,
    judge: Judge | None = None,
) -> dict[str, Any]:
    """Predicate outranks judge: a predicate fail is 0 no matter the judge."""
    if not result.get("ok"):
        return {"score": 0.0, "predicates_pass": False, "judge_score": None}

    judge_score: float | None = None
    if judge is not None:
        judge_score = max(0.0, min(1.0, float(judge(result))))

    if predicates:
        passed = eval_predicates(result.get("output"), predicates)
        if not passed:
            return {"score": 0.0, "predicates_pass": False, "judge_score": judge_score}
        score = 1.0 if judge_score is None else 0.75 + 0.25 * judge_score
        return {"score": score, "predicates_pass": True, "judge_score": judge_score}

    if judge_score is not None:
        return {"score": judge_score, "predicates_pass": None, "judge_score": judge_score}
    return {
        "score": 1.0 if result.get("output") else 0.5,
        "predicates_pass": None,
        "judge_score": None,
    }


async def _run_preset(preset: str, body: Mapping[str, Any]) -> dict[str, Any]:
    if preset == "gen_cfsm":  # P1 made generated FSMs a legal racing candidate
        from CortexOS.execution.gen_cfsm import execute_cfsm, generate_ir  # lazy: avoids cycle

        goal = str(body.get("prompt") or "")
        try:
            report = await execute_cfsm(generate_ir(goal, 3), dict(body))
        except Exception as exc:
            return {"ok": False, "runner": "gen_cfsm", "error": str(exc)}
        return {
            "ok": bool(report.get("ok")),
            "runner": "gen_cfsm",
            "status": "completed" if report.get("ok") else str(report.get("verdict") or "failed"),
            "output": report.get("output"),
            "cfsm": {"verdict": report.get("verdict"), "collapse": report.get("collapse")},
        }
    try:
        plan = plan_for_request(preset, dict(body))
        return await execute_run_plan(plan, dict(body))
    except Exception as exc:  # probe failure is data, not a crash
        return {"ok": False, "runner": preset, "error": str(exc)}


async def _probe(
    preset: str,
    body: Mapping[str, Any],
    *,
    race_id: str,
    task_family: str,
    predicates: Sequence[Mapping[str, Any]] | None,
    judge: Judge | None,
    probe_token_cap: int,
) -> dict[str, Any]:
    probe_body = dict(body)
    params = dict(probe_body.get("params") or {})
    params.update({"probe": True, "max_tokens": probe_token_cap})
    probe_body["params"] = params
    probe_body["session_id"] = f"{race_id}:{preset}"

    started = time.perf_counter()
    result = await _run_preset(preset, probe_body)
    latency_ms = int((time.perf_counter() - started) * 1000)

    scored = score_probe(result, predicates, judge)
    scoreboard.record_run(
        f"{race_id}:{preset}",
        task_family,
        preset,
        mode="probe",
        score=scored["score"],
        predicates_pass=scored["predicates_pass"],
        judge_score=scored["judge_score"],
        latency_ms=latency_ms,
    )
    return {
        "preset": preset,
        "ok": bool(result.get("ok")),
        "latency_ms": latency_ms,
        **scored,
    }


async def race(
    goal: str,
    body: Mapping[str, Any] | None = None,
    *,
    family: str | None = None,
    goal_vec: list[float] | None = None,
    candidates: Sequence[str] | None = None,
    predicates: Sequence[Mapping[str, Any]] | None = None,
    judge: Judge | None = None,
    probe_token_cap: int = PROBE_TOKEN_CAP,
    scale: bool = True,
) -> dict[str, Any]:
    scoreboard.init()
    run_body = dict(body or {})
    run_body.setdefault("prompt", goal)
    task_family = family or scoreboard.family_id(goal)
    vec = goal_vec or scoreboard.embed_goal(goal)
    pool = list(candidates or rank_candidates(task_family))
    race_id = "race-" + uuid.uuid4().hex[:8]

    probes = await asyncio.gather(
        *(
            _probe(
                preset,
                run_body,
                race_id=race_id,
                task_family=task_family,
                predicates=predicates,
                judge=judge,
                probe_token_cap=probe_token_cap,
            )
            for preset in pool
        )
    )

    all_failed = all(p["score"] <= 0.0 for p in probes)
    winner = pool[0] if all_failed else max(
        probes, key=lambda p: (p["score"], -pool.index(p["preset"]))
    )["preset"]

    scaled: dict[str, Any] | None = None
    if scale and not all_failed:
        scale_body = dict(run_body)
        scale_body["session_id"] = f"{race_id}:scaled"
        started = time.perf_counter()
        result = await _run_preset(winner, scale_body)
        latency_ms = int((time.perf_counter() - started) * 1000)
        scored = score_probe(result, predicates, judge)
        scoreboard.record_run(
            f"{race_id}:scaled",
            task_family,
            winner,
            mode="scaled",
            score=scored["score"],
            predicates_pass=scored["predicates_pass"],
            judge_score=scored["judge_score"],
            latency_ms=latency_ms,
        )
        scoreboard.upsert_family(task_family, vec)
        scaled = {"result": result, **scored, "latency_ms": latency_ms}

    return {
        "ok": True,
        "mode": "raced",
        "race_id": race_id,
        "family": task_family,
        "candidates": pool,
        "probes": list(probes),
        "winner": winner,
        "all_probes_failed": all_failed,
        "scaled": scaled,
    }


async def auto_route(
    goal: str,
    body: Mapping[str, Any] | None = None,
    *,
    predicates: Sequence[Mapping[str, Any]] | None = None,
    judge: Judge | None = None,
    sim_threshold: float = 0.80,
    min_runs: int = 3,
    probe_token_cap: int = PROBE_TOKEN_CAP,
    scale: bool = True,
) -> dict[str, Any]:
    """The full gate: family confidence (feature hash, not JEPA) → direct route or race."""
    scoreboard.init()
    gate = scoreboard.should_race(goal, sim_threshold=sim_threshold, min_runs=min_runs)
    task_family = gate["family"] or scoreboard.family_id(goal)

    if not gate["race"]:
        run_body = dict(body or {})
        run_body.setdefault("prompt", goal)
        started = time.perf_counter()
        result = await _run_preset(gate["winner"], run_body)
        latency_ms = int((time.perf_counter() - started) * 1000)
        scored = score_probe(result, predicates, judge)
        scoreboard.record_run(
            "direct-" + uuid.uuid4().hex[:8],
            task_family,
            gate["winner"],
            mode="scaled",
            score=scored["score"],
            predicates_pass=scored["predicates_pass"],
            judge_score=scored["judge_score"],
            latency_ms=latency_ms,
        )
        scoreboard.upsert_family(task_family, gate["vector"])
        return {
            "ok": True,
            "mode": "direct",
            "family": task_family,
            "similarity": gate["similarity"],
            "reason": gate["reason"],
            "winner": gate["winner"],
            "result": result,
            **scored,
        }

    out = await race(
        goal,
        body,
        family=task_family,
        goal_vec=gate["vector"],
        predicates=predicates,
        judge=judge,
        probe_token_cap=probe_token_cap,
        scale=scale,
    )
    out["similarity"] = gate["similarity"]
    out["reason"] = gate["reason"]
    return out
