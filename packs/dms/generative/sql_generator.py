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


def is_configured() -> bool:
    """True when L2 is enabled and OpenVault answers (FreeRoute path exists)."""
    if os.environ.get("DMS_L2_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        from CortexOS.integrations.openvault_client import ping

        return bool(ping(timeout=1.5))
    except Exception:  # noqa: BLE001
        return False


def _leave_machine_allowed() -> tuple[bool, str]:
    """FreeRoute SQL leaves the box — OpenVault gate must allow it.

    Local OpenVault ``/v1/chat/completions`` still counts as leave-machine when
    it forwards to a cloud provider. Deny-by-default if OV is offline.
    """
    try:
        from CortexOS.integrations.openvault_gate import check_gate

        # OpenVault's gate accepts exactly retrieve | run | deploy | leave |
        # connect. This asked for "llm" and "leave_machine", which are not among
        # them, so every call 422'd, came back empty, and was read as "OpenVault
        # unreachable" — the deny-by-default branch. L2 was therefore hard-denied
        # on a machine whose gate was open with six providers ready, and the
        # denial looked like a policy decision rather than a typo.
        #
        # "leave" is the honest action: schema and question go off the box to
        # FreeRoute. "run" is the fallback for an older gate that predates it.
        for action, destination in (
            ("leave", "freeroute"),
            ("run", "openvault"),
        ):
            gate = check_gate(
                action=action,
                destination=destination,
                required_providers=[],
            )
            if gate.get("allowed") is True:
                return True, f"ok:{action}"
            # Unreachable OV → hard deny (do not try softer actions as bypass).
            if gate.get("ok") is False and "unreachable" in str(gate.get("reasons") or "").lower():
                reasons = gate.get("reasons") or ["OpenVault unreachable"]
                return False, "; ".join(str(r) for r in reasons)[:240]
        reasons = gate.get("reasons") or ["leave-machine gate denied"]
        return False, "; ".join(str(r) for r in reasons)[:240]
    except Exception as exc:  # noqa: BLE001
        return False, f"gate error: {exc}"[:240]


def _extract_sql(text: str) -> str | None:
    if not text:
        return None
    m = _SQL_FENCE.search(text)
    body = (m.group(1) if m else text).strip()
    m2 = _SELECT.search(body)
    if not m2:
        return None
    sql = m2.group(1).strip().rstrip(";")
    # Single statement only
    if ";" in sql:
        sql = sql.split(";", 1)[0].strip()
    if not sql.upper().startswith("SELECT"):
        return None
    return sql


def _freeroute_complete(prompt: str) -> str | None:
    """POST OpenVault /v1/chat/completions — large-model tier. None on any failure."""
    from CortexOS.integrations.openvault_client import openvault_base_url, post_json

    # Default must be a model FreeRoute's configured providers actually serve.
    # Named Groq ids (e.g. llama-3.3-70b-versatile) often 404 non-retryable on the
    # live vault chain; ``auto`` lets FreeRoute pick a working upstream (bakeoff).
    model = (
        os.environ.get("DMS_L2_MODEL")
        or os.environ.get("OPENVAULT_SQL_MODEL")
        or "auto"
    )
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
    # `metadata` is not part of the OpenAI chat-completions body that FreeRoute
    # forwards. Groq rejects the whole request with HTTP 400 non-retryable, so
    # post_json returned None and L2 abstained — silently, and only when the
    # router happened to pick Groq. L2 therefore appeared to work or not
    # depending on provider selection, which made every wrong-rate measurement
    # of it meaningless. The identity travels as a header instead, where a
    # provider that does not know it will ignore rather than refuse it — and
    # post_json takes no headers today, so the identity simply is not sent.
    # Prefer OpenVault proxy; fall back to direct path variants.
    data = post_json("/v1/chat/completions", body, timeout=45.0, base=openvault_base_url())
    if not data:
        return None
    try:
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}).get("content") if choices else None
        return str(msg) if msg else None
    except (IndexError, AttributeError, TypeError, KeyError):
        return None


#: Enumerate a categorical column in the prompt only when it is small enough to
#: read as a closed set. Past this it is a lookup, not an enum, and listing it
#: would crowd out the schema.
MAX_ENUM_VALUES = 25


def _value_ground_truth(schema_context: dict[str, Any]) -> list[str]:
    """The values each categorical column actually holds, verbatim.

    Told only "use exact warehouse values", the model has to guess the encoding,
    and it guesses plausibly: ``txn_type = 'SALE'`` for a revenue question
    against a warehouse that stores ``OUT``. The literal gate catches it and the
    answer abstains — correct, and a wasted round-trip for a question that was
    answerable.

    Casing and spelling are the same class of problem: ``nasi lemak``,
    ``Nasi Lemak`` and ``NASI LEMAK`` are one value to a person and three to a
    ``=``. Handing over the stored spelling removes the guess instead of
    correcting it afterwards, and what is listed here is read from the warehouse,
    so it cannot drift from what is really in the column.
    """
    try:
        from packs.dms.semantic import values as valuedict
    except Exception:  # noqa: BLE001
        return []

    lines: list[str] = []
    wanted = set((schema_context.get("tables") or {}).keys())
    for column, table in getattr(valuedict, "VALUE_COLUMNS", {}).items():
        if table not in wanted:
            continue
        try:
            allowed = valuedict.values_for(column)
        except Exception:  # noqa: BLE001 — a prompt hint must never break the ask
            continue
        if not allowed or len(allowed) > MAX_ENUM_VALUES:
            continue
        rendered = ", ".join(repr(str(v)) for v in allowed)
        lines.append(f"- {table}.{column} is one of: {rendered}")
    return lines


def _build_prompt(
    question: str,
    schema_context: dict[str, Any],
    *,
    prior_violations: list[str] | None,
) -> str:
    parts = [
        schema_prompt_block(schema_context),
    ]
    ground_truth = _value_ground_truth(schema_context)
    if ground_truth:
        parts += [
            "",
            "EXACT COLUMN VALUES (copy these spellings and casing verbatim; "
            "never invent a category):",
            *ground_truth,
        ]
    parts += [
        "",
        f"QUESTION: {question}",
        "",
        "Emit one DuckDB SELECT. Encode categorical filters with exact warehouse values "
        "(e.g. SKU-BETA not BETA; WH-A / WAREHOUSE A as stored). "
        "If the question asks to skip or exclude the leading rows, use OFFSET. "
        "If no listed column can answer it, emit nothing rather than guessing a column.",
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
