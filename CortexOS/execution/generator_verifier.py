"""Generator–verifier revise loop (Anthropic pattern 1).

distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md

Not a third orchestrator: callers supply generate/verify callables (typically
AGENT_TASK wrappers). This module only owns the loop mechanics Anthropic
requires: explicit criteria, max attempts, structured feedback, and a fallback
when the loop stalls.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

GenerateFn = Callable[[str, int], Awaitable[str] | str]
VerifyFn = Callable[[str, Sequence[str], int], Awaitable[Mapping[str, Any]] | Mapping[str, Any]]

DEFAULT_MAX_ATTEMPTS = 3

_PASS_KEYS = ("passed", "accepted", "ok", "confirmed")
_FAIL_HINT_KEYS = ("refuted", "failed", "rejected")


@dataclass(slots=True)
class VerifyResult:
    passed: bool
    feedback: str
    raw: dict[str, Any] = field(default_factory=dict)
    criteria_checked: list[str] = field(default_factory=list)
    early_victory_risk: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GeneratorVerifierOutcome:
    status: str  # accepted | max_attempts | error
    output: str
    attempts: int
    history: list[dict[str, Any]] = field(default_factory=list)
    fallback: str = ""
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_criteria(criteria: Sequence[str] | None) -> list[str]:
    out = [c.strip() for c in (criteria or []) if isinstance(c, str) and c.strip()]
    if not out:
        raise ValueError(
            "generator-verifier requires explicit criteria; refusing rubber-stamp "
            "verification (Anthropic early-victory mitigation)"
        )
    return out


def parse_verifier_payload(raw: Mapping[str, Any] | str, *, criteria: Sequence[str]) -> VerifyResult:
    """Interpret verifier output; prefer structured JSON when present."""
    data: dict[str, Any]
    text = ""
    if isinstance(raw, str):
        text = raw
        data = _extract_json_object(raw) or {}
    else:
        data = dict(raw)
        text = str(data.get("reason") or data.get("feedback") or data.get("content") or "")

    passed: bool | None = None
    for key in _PASS_KEYS:
        if key in data and isinstance(data[key], bool):
            # confirmed=true means pass for audit.verify; refuted handled below
            if key == "confirmed":
                passed = data[key]
            else:
                passed = data[key]
            break
    if passed is None:
        for key in _FAIL_HINT_KEYS:
            if key in data and isinstance(data[key], bool):
                # research.verify: refuted=true means fail
                passed = not data[key] if key == "refuted" else not data[key]
                break
    if passed is None:
        # No structured verdict — treat as fail (never rubber-stamp)
        passed = False
        text = text or "verifier returned no explicit pass/fail — treating as reject"

    feedback = text or str(data.get("reason") or data.get("issues") or "")
    if not feedback and not passed:
        feedback = "rejected without detailed feedback"

    checked = list(criteria)
    if isinstance(data.get("criteria_checked"), list):
        checked = [str(x) for x in data["criteria_checked"]]

    early = _early_victory_risk(data, criteria)
    return VerifyResult(
        passed=bool(passed),
        feedback=feedback[:4000],
        raw=data if data else {"content": text[:2000]},
        criteria_checked=checked,
        early_victory_risk=early,
    )


def _early_victory_risk(data: Mapping[str, Any], criteria: Sequence[str]) -> bool:
    """Heuristic: verifier claimed pass without addressing criteria / tests."""
    if not data.get("passed") and not data.get("confirmed") and data.get("ok") is not True:
        return False
    blob = json.dumps(data, default=str).lower()
    if "must run" in blob or "full test" in blob:
        return False
    # Passed but no criteria_checked and criteria were many → suspicious
    checked = data.get("criteria_checked")
    if isinstance(checked, list) and len(checked) >= len(criteria):
        return False
    if len(criteria) >= 2 and not checked:
        return True
    if any(k in blob for k in ("looks good", "seems fine", "lgtm", "probably ok")):
        return True
    return False


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


def build_revision_prompt(task: str, prior_output: str, feedback: str, attempt: int) -> str:
    return (
        f"{task.rstrip()}\n\n"
        f"--- previous attempt ({attempt}) ---\n{prior_output[:8000]}\n\n"
        f"--- verifier feedback (must address) ---\n{feedback[:4000]}\n\n"
        "Revise the output to satisfy every criterion. Do not repeat the same answer."
    )


def criteria_block(criteria: Sequence[str]) -> str:
    lines = ["You MUST evaluate against ALL of these criteria before accepting:"]
    for i, c in enumerate(criteria, 1):
        lines.append(f"  {i}. {c}")
    lines.append(
        "Do not mark passed/confirmed after a partial check. "
        "If any criterion fails, reject with concrete feedback naming the criterion."
    )
    return "\n".join(lines)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value  # type: ignore[misc]
    return value


async def run_generator_verifier(
    *,
    task: str,
    criteria: Sequence[str],
    generate: GenerateFn,
    verify: VerifyFn,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    fallback: str = "escalate_human",
) -> GeneratorVerifierOutcome:
    """Run generate→verify→revise until accept or max_attempts.

    ``fallback`` is recorded on the outcome (caller decides escalate / return
    best-with-caveats). Oscillation is bounded by ``max_attempts``.
    """
    crit = normalize_criteria(criteria)
    attempts_cap = max(1, min(10, int(max_attempts)))
    history: list[dict[str, Any]] = []
    best_output = ""
    prompt = task

    for attempt in range(1, attempts_cap + 1):
        try:
            output = await _maybe_await(generate(prompt, attempt))
        except Exception as exc:  # noqa: BLE001 — loop must surface cleanly
            return GeneratorVerifierOutcome(
                status="error",
                output=best_output,
                attempts=attempt,
                history=history,
                fallback=fallback,
                caveats=[f"generate failed: {exc}"[:300]],
            )
        output_s = output if isinstance(output, str) else str(output)
        best_output = output_s

        try:
            raw_v = await _maybe_await(verify(output_s, crit, attempt))
        except Exception as exc:  # noqa: BLE001
            return GeneratorVerifierOutcome(
                status="error",
                output=best_output,
                attempts=attempt,
                history=history,
                fallback=fallback,
                caveats=[f"verify failed: {exc}"[:300]],
            )

        result = parse_verifier_payload(raw_v, criteria=crit)
        history.append(
            {
                "attempt": attempt,
                "output_chars": len(output_s),
                "passed": result.passed,
                "feedback": result.feedback,
                "early_victory_risk": result.early_victory_risk,
                "raw": result.raw,
            }
        )
        if result.passed and not result.early_victory_risk:
            return GeneratorVerifierOutcome(
                status="accepted",
                output=output_s,
                attempts=attempt,
                history=history,
                fallback=fallback,
            )
        if result.passed and result.early_victory_risk:
            # Treat rubber-stamp as reject; force another cycle with explicit ask
            result.feedback = (
                (result.feedback + "\n") if result.feedback else ""
            ) + (
                "EARLY_VICTORY: verifier claimed pass without evidence of checking "
                "all criteria — regenerate and re-verify thoroughly."
            )
            result.passed = False
            history[-1]["passed"] = False
            history[-1]["feedback"] = result.feedback

        if attempt < attempts_cap:
            prompt = build_revision_prompt(task, output_s, result.feedback, attempt)

    caveats = [
        f"Failed verification after {attempts_cap} attempts",
        f"fallback={fallback}",
    ]
    if history:
        caveats.append(f"last_feedback={history[-1].get('feedback', '')[:200]}")
    return GeneratorVerifierOutcome(
        status="max_attempts",
        output=best_output,
        attempts=attempts_cap,
        history=history,
        fallback=fallback,
        caveats=caveats,
    )
