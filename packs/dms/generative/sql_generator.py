"""C7-full L2 SQL generator — FreeRoute large-model tier only.

Schema retrieval → FreeRoute chat completions → literal normalization.
Never fall back to the L1 keyword cascade or a smaller model silently.
If FreeRoute is down or the leave-machine gate refuses → empty candidates
(caller abstains).
"""

from __future__ import annotations

import os
import re
from typing import Any

from packs.dms.generative.literal_normalize import normalize_sql_literals
from packs.dms.generative.schema_retrieval import retrieve, schema_prompt_block

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.I | re.S)
_SELECT = re.compile(r"(SELECT\b.+)", re.I | re.S)
_FROM = re.compile(r"\bfrom\b", re.I)


def is_configured() -> bool:
    """True when L2 is enabled or shadowed, and OpenVault answers."""
    enabled = os.environ.get("DMS_L2_ENABLED", "").lower() in ("1", "true", "yes")
    shadow = os.environ.get("DMS_L2_SHADOW", "").lower() in ("1", "true", "yes")
    if not (enabled or shadow):
        return False
    try:
        from CortexOS.integrations.openvault_client import ping

        return bool(ping(timeout=1.5))
    except Exception:  # noqa: BLE001
        return False


def _leave_machine_allowed() -> tuple[bool, str]:
    """FreeRoute SQL leaves the box — OpenVault gate must allow ``leave``.

    Live OpenVault ``GateCheckBody.action`` is retrieve|run|deploy|leave|connect.
    ``llm`` / ``leave_machine`` 422 that schema; Cortex then treated the 422 as
    unreachable and never asked ``leave``. Do not fall back to ``run``: that is
    a local-run gate, not leave-machine permission.
    """
    try:
        from CortexOS.integrations.openvault_gate import check_gate

        gate = check_gate(
            action="leave",
            destination="freeroute",
            required_providers=[],
        )
        if gate.get("allowed") is True:
            return True, "ok:leave"
        reasons = gate.get("reasons") or ["leave-machine gate denied"]
        return False, "; ".join(str(r) for r in reasons)[:240]
    except Exception as exc:  # noqa: BLE001
        return False, f"gate error: {exc}"[:240]


def _one_select(body: str) -> str | None:
    m2 = _SELECT.search(body)
    if not m2:
        return None
    sql = m2.group(1).strip().rstrip(";")
    if ";" in sql:
        sql = sql.split(";", 1)[0].strip()
    if not sql.upper().startswith("SELECT"):
        return None
    return sql


def _extract_sql(text: str) -> str | None:
    """Take one SELECT. Prefer a statement that still has FROM.

    Models often fence only the SELECT list and leave ``FROM t i`` outside
    the fence. That parsed as ``SELECT i.sku LIMIT 1000`` and EXPLAIN died
    on alias ``i``. No FROM → None (caller retries / abstains).
    """
    if not text:
        return None
    blobs: list[str] = []
    for m in _SQL_FENCE.finditer(text):
        blobs.append(m.group(1).strip())
    blobs.append(_SQL_FENCE.sub(" ", text).strip())
    blobs.append(text.strip())
    for body in blobs:
        sql = _one_select(body)
        if sql and _FROM.search(sql):
            return sql
    return None


def _freeroute_complete(prompt: str, *, identity: str = "dms:l2-sql") -> str | None:
    """POST OpenVault /v1/chat/completions — large-model tier. None on any failure."""
    from CortexOS.integrations.openvault_client import openvault_base_url, post_json

    model = os.environ.get("DMS_L2_MODEL") or os.environ.get("OPENVAULT_SQL_MODEL") or "gpt-4o-mini"
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write a single DuckDB SELECT for a warehouse analytics app. "
                    "Use ONLY tables/columns in the reduced schema. "
                    "No DDL/DML. No comments. Prefer LIMIT 50. "
                    "Return SQL only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 600,
    }
    # Do not send OpenAI ``metadata``: OpenVault extra=allow forwards it to
    # Google AI Studio, which 400s (non_retryable) and Cortex sees NO_CANDIDATE.
    _ = identity
    data = post_json("/v1/chat/completions", body, timeout=45.0, base=openvault_base_url())
    if not data:
        return None
    try:
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}).get("content") if choices else None
        return str(msg) if msg else None
    except (IndexError, AttributeError, TypeError, KeyError):
        return None


def _build_prompt(
    question: str,
    schema_context: dict[str, Any],
    *,
    prior_violations: list[str] | None,
) -> str:
    parts = [
        schema_prompt_block(schema_context),
        "",
        f"QUESTION: {question}",
        "",
        "Emit one DuckDB SELECT. Encode categorical filters with exact warehouse values "
        "(e.g. SKU-BETA not BETA; WH-A / WAREHOUSE A as stored).",
    ]
    if prior_violations:
        parts.append("PREVIOUS VALIDATION ERRORS (fix these):")
        parts.extend(f"- {v}" for v in prior_violations[:8])
    return "\n".join(parts)


def generate_candidates(
    question: str,
    schema_context: dict | None = None,
    *,
    n: int = 3,
    prior_violations: list[str] | None = None,
) -> list[str]:
    """Return normalized SQL candidates. Empty → caller must abstain."""
    _ = n  # FreeRoute returns one proposal; retries feed prior_violations.
    if not is_configured():
        return []

    allowed, reason = _leave_machine_allowed()
    if not allowed:
        # Fail closed — never silently degrade to a smaller/local model.
        return []

    schema = schema_context if schema_context is not None else retrieve(question)
    prompt = _build_prompt(question, schema, prior_violations=prior_violations)
    raw = _freeroute_complete(prompt)
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
    cands = generate_candidates(
        question, schema, prior_violations=prior_violations
    )
    return {
        "configured": configured,
        "gate_allowed": allowed,
        "gate_reason": gate_reason,
        "schema_tables": list((schema.get("tables") or {}).keys()),
        "candidates": cands,
    }


__all__ = [
    "generate_candidates",
    "generate_with_detail",
    "is_configured",
]
