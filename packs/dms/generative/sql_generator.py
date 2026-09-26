"""C7-full L2 SQL generator — FreeRoute large-model tier only.

Schema retrieval → FreeRoute chat completions → literal normalization.
Never fall back to the L1 keyword cascade or a smaller model silently.
If FreeRoute is not armed or the leave-machine gate refuses → empty candidates
(caller abstains, and the abstain names the OpenVault cause).

Model calls go through ``CortexOS.integrations.freeroute``: the one arming rule,
the measured route (no hardcoded model), and the Cortex OpenVault credential.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

from CortexOS.dms.sql_extract import extract_select
from CortexOS.integrations import freeroute
from packs.dms.generative import few_shot
from packs.dms.generative.literal_normalize import normalize_sql_literals
from packs.dms.generative.schema_retrieval import retrieve, schema_prompt_block

_SYSTEM_PROMPT = (
    "You write a single DuckDB SELECT for a warehouse analytics app. "
    "Use ONLY tables/columns in the reduced schema. "
    "No DDL/DML. No comments. Prefer LIMIT 50. "
    "Return SQL only."
)
_PIN_ENVS = ("DMS_L2_MODEL", "OPENVAULT_SQL_MODEL")
#: Total characters for the EXAMPLES block (header included).
EXAMPLES_CHAR_CAP = 1500
_EXAMPLES_HEADER = "EXAMPLES (verified question \u2192 SQL):"
_last_few_shot: ContextVar[int] = ContextVar("l2_last_few_shot_count", default=0)
# #271: the plan the FreeRoute pre-spend gate is asked about for this proposal.
_plan_var: ContextVar[dict[str, Any] | None] = ContextVar("l2_prespend_plan", default=None)


def few_shot_enabled() -> bool:
    """``DMS_L2_FEW_SHOT=0`` turns examples off (A/B against the shadow log)."""
    return os.environ.get("DMS_L2_FEW_SHOT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def last_few_shot_count() -> int:
    """Examples placed in the most recent prompt built in this context."""
    return _last_few_shot.get()


def _examples_block(examples: list[tuple[str, str]]) -> tuple[str, int]:
    """Render examples under the char cap. Whole pairs only; returns (block, count)."""
    lines = [_EXAMPLES_HEADER]
    used = len(_EXAMPLES_HEADER)
    count = 0
    for question, sql in examples:
        pair = [f"Q: {' '.join(question.split())}", f"SQL: {' '.join(sql.split())}"]
        cost = sum(len(line) + 1 for line in pair)
        if used + cost > EXAMPLES_CHAR_CAP:
            continue
        lines.extend(pair)
        used += cost
        count += 1
    if not count:
        return "", 0
    return "\n".join(lines), count


def _l2_flag_on() -> bool:
    enabled = os.environ.get("DMS_L2_ENABLED", "").lower() in ("1", "true", "yes")
    shadow = os.environ.get("DMS_L2_SHADOW", "").lower() in ("1", "true", "yes")
    return enabled or shadow


def is_configured() -> bool:
    """True when L2 is enabled or shadowed, and FreeRoute is armed in OpenVault."""
    if not _l2_flag_on():
        return False
    return freeroute.arming().armed


def unarmed_reason() -> str:
    """Why generation cannot run, for the customer abstain. '' when it can."""
    if not _l2_flag_on():
        return "DMS_L2_ENABLED / DMS_L2_SHADOW not set"
    arm = freeroute.arming()
    return "" if arm.armed else f"FreeRoute not armed: {arm.reason}"


def _leave_machine_allowed() -> tuple[bool, str]:
    """FreeRoute SQL leaves the box — OpenVault gate must allow ``leave``.

    Live OpenVault ``GateCheckBody.action`` is retrieve|run|deploy|leave|connect.
    ``llm`` / ``leave_machine`` 422 that schema; Cortex then treated the 422 as
    unreachable and never asked ``leave``. Do not fall back to ``run``: that is
    a local-run gate, not leave-machine permission.
    """
    return freeroute.leave_gate()


# One extractor for engine and Crew; kept under the old name for callers and tests.
_extract_sql = extract_select


def _pin() -> tuple[str, str]:
    for name in _PIN_ENVS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, name
    return "", ""


def _freeroute_complete(prompt: str) -> str | None:
    """One FreeRoute SQL proposal. None on any refusal (named on the journal stamp)."""
    pin, pin_source = _pin()
    # Do not send OpenAI ``metadata``: OpenVault extra=allow forwards it to
    # Google AI Studio, which 400s (non_retryable) and Cortex sees NO_CANDIDATE.
    out = freeroute.complete(
        "gen-ask-sql",
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=600,
        timeout=45.0,
        accept=lambda text: extract_select(text) is not None,
        pin=pin,
        pin_source=pin_source,
        egress="leave",
        predict_state=_plan_var.get(),
    )
    return out.text if out.ok else None


def _plan_state(question: str, schema: dict[str, Any], *, retry: bool) -> dict[str, Any]:
    """What the #271 pre-spend gate is asked about: tables, joins, retry. No rows."""
    return {
        "task": "gen-ask-sql",
        "question": question,
        "tables": sorted((schema.get("tables") or {}).keys()),
        "joins": [
            f"{j.get('from_table')}.{j.get('from_column')}={j.get('to_table')}.{j.get('to_column')}"
            for j in schema.get("joins") or []
            if isinstance(j, dict)
        ],
        "retry": retry,
    }


def _build_prompt(
    question: str,
    schema_context: dict[str, Any],
    *,
    prior_violations: list[str] | None,
    examples: list[tuple[str, str]] | None = None,
) -> str:
    parts = [schema_prompt_block(schema_context), ""]
    block, count = _examples_block(list(examples or []))
    _last_few_shot.set(count)
    if block:
        parts.extend([block, ""])
    parts.extend([
        f"QUESTION: {question}",
        "",
        "Emit one DuckDB SELECT. Encode categorical filters with exact warehouse values "
        "(e.g. SKU-BETA not BETA; WH-A / WAREHOUSE A as stored).",
    ])
    if prior_violations:
        parts.append("PREVIOUS VALIDATION ERRORS (fix these):")
        parts.extend(f"- {v}" for v in prior_violations[:8])
    return "\n".join(parts)


def _select_examples(question: str, schema: dict[str, Any]) -> list[tuple[str, str]]:
    """Verified pairs restricted to the reduced schema. Never blocks generation."""
    if not few_shot_enabled():
        return []
    try:
        return few_shot.select_examples(
            question, tables=(schema.get("tables") or {}).keys()
        )
    except Exception:  # noqa: BLE001 - examples are an aid, not a gate
        return []


def generate_candidates(
    question: str,
    schema_context: dict | None = None,
    *,
    n: int = 3,
    prior_violations: list[str] | None = None,
) -> list[str]:
    """Return normalized SQL candidates. Empty → caller must abstain."""
    _ = n  # FreeRoute returns one proposal; retries feed prior_violations.
    _last_few_shot.set(0)
    if not is_configured():
        return []

    # The leave-machine gate runs inside freeroute.complete(egress="leave"):
    # a denial sends nothing and never degrades to a smaller/local model.
    schema = schema_context if schema_context is not None else retrieve(question)
    prompt = _build_prompt(
        question,
        schema,
        prior_violations=prior_violations,
        examples=_select_examples(question, schema),
    )
    plan_token = _plan_var.set(_plan_state(question, schema, retry=bool(prior_violations)))
    try:
        raw = _freeroute_complete(prompt)
    finally:
        _plan_var.reset(plan_token)
    if not raw:
        return []
    sql = _extract_sql(raw)
    if not sql:
        return []

    norm = normalize_sql_literals(sql)
    if not norm.ok or not norm.sql:
        # Unresolvable literal — abstain (empty list); violations surface via gate.
        return []
    return [norm.sql]


def generate_with_detail(
    question: str,
    schema_context: dict | None = None,
    *,
    prior_violations: list[str] | None = None,
) -> dict[str, Any]:
    """Diagnostics helper for tests — never used to bypass abstain rules."""
    configured = is_configured()
    allowed, gate_reason = _leave_machine_allowed() if configured else (False, "not_configured")
    schema = schema_context if schema_context is not None else retrieve(question)
    with freeroute.journal() as stamps:
        cands = generate_candidates(question, schema, prior_violations=prior_violations)
    return {
        "configured": configured,
        "unarmed_reason": unarmed_reason() if not configured else "",
        "gate_allowed": allowed,
        "gate_reason": gate_reason,
        "schema_tables": list((schema.get("tables") or {}).keys()),
        "candidates": cands,
        "few_shot_count": last_few_shot_count(),
        "routes": [stamp.public() for stamp in stamps],
    }


__all__ = [
    "generate_candidates",
    "generate_with_detail",
    "few_shot_enabled",
    "is_configured",
    "last_few_shot_count",
    "unarmed_reason",
]
