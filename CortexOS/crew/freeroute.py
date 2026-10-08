"""OpenVault FreeRoute for Crew - an async adapter over the one Cortex core.

Arming, credential, candidate order and the measured route all live in
``CortexOS.integrations.freeroute`` (sync, stdlib). This module holds no
routing logic of its own: it runs the core on a dedicated executor so the crew
event loop stays free, maps crew purposes onto core task keys, and keeps the
#214 HTTP shapes (purposes, identity labels, ``complete()`` result dict).

Insights / generative-ask / prompt-think-act go through here when a model is
needed. Unarmed is fail-closed and named. No invent-green keys. Attribution is
OpenVault's: nothing Cortex writes about itself carries authority (KB A-0009).

The DMS #180 numbers stay cited as a cross-system baseline only; they score the
DMS offline bind_plan lane with a badge-only judge and say nothing about any
model FreeRoute serves.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from CortexOS.crew.llm import LLMError
from CortexOS.dms.sql_extract import extract_select
from CortexOS.integrations import freeroute as core

# Stable in-Cortex attribution label. Not a credential; OpenVault never reads it.
CORTEX_IDENTITY = "cortex:crew"
PURPOSES = ("prompt", "think", "act", "insights", "generative_ask")

DMS_180_BASELINE = {
    "cite": "DMS #180 Formal GREEN @ d2f116a6",
    "gen": "57.69%",
    "exact": "38.46%",
    "wrong": 0,
    "note": (
        "DMS offline bind_plan lane scored by a badge-only judge; cited "
        "cross-system as a baseline, not a model score"
    ),
}

CREW_OFF_REASON = "CREW_OPENVAULT=0 (no invent-green keys)"
_UNARMED_SUFFIX = " (no silent fallback; no invent-green keys)"

# Purpose -> core task key. Validity verdicts are scored per task, so the SQL
# lane ranks models on SQL and the chat lanes on their own outcomes.
_TASKS = {
    "prompt": "crew-prompt",
    "think": "crew-think",
    "act": "crew-act",
    "insights": "crew-insights",
    "generative_ask": "crew-insights-sql",
}
_MAX_TOKENS = {"generative_ask": 600}
_DEFAULT_MAX_TOKENS = 2048

T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def executor() -> ThreadPoolExecutor:
    """The crew's FreeRoute thread pool. Core calls block on OpenVault HTTP."""
    global _executor
    with _executor_lock:
        if _executor is None:
            workers = int(os.environ.get("CREW_FREEROUTE_WORKERS") or 8)
            _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="crew-freeroute")
        return _executor


async def run_core(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a sync core call off the loop, carrying the caller's context.

    ``copy_context`` keeps the core journal / shadow / transport contextvars
    visible on the worker thread, so a ``core.journal()`` opened by the caller
    collects the stamps written inside.
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(executor(), ctx.run, functools.partial(fn, *args, **kwargs))


@dataclass(frozen=True)
class RoutePick:
    label: str
    model: str
    kind: str
    why: str
    connector: str = "openvault"
    score: float | None = None
    identity: str = CORTEX_IDENTITY

    def as_public(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "kind": self.kind,
            "why": self.why,
            "connector": self.connector,
            "score": self.score,
            "identity": self.identity,
        }


def identity_for(purpose: str = "") -> str:
    """Attribution label for logs and envelopes. Never sent as a header."""
    raw = _purpose_key(purpose)
    if raw in PURPOSES:
        return f"{CORTEX_IDENTITY}:{raw.replace('_', '-')}"
    return CORTEX_IDENTITY


def _purpose_key(purpose: str) -> str:
    return (purpose or "").strip().lower().replace("-", "_")


def task_for(purpose: str) -> str:
    return _TASKS.get(_purpose_key(purpose), "crew-think")


def reset_measurements() -> None:
    """Test hook: drop the core's in-process caches. The route store stays."""
    core.reset()


# -- arming ---------------------------------------------------------------------


def crew_off() -> bool:
    return os.environ.get("CREW_OPENVAULT", "1") == "0"


def arm() -> core.Arming:
    """Cortex's own credential. ``CREW_OPENVAULT=0`` never probes the vault."""
    if crew_off():
        return core.unarmed(CREW_OFF_REASON)
    return core.arming()


def _arming_view(arming_: core.Arming) -> dict[str, Any]:
    return {
        "ok": arming_.armed,
        "armed": arming_.armed,
        "vault_live": arming_.sealed is not None,
        "sealed": arming_.sealed,
        "pooled_keys": arming_.pooled_keys,
        "spendable_hops": arming_.spendable_hops,
        "labels": sorted({str(h.get("provider") or "") for h in arming_.hops_public} - {""}),
        "detail": arming_.reason,
        "custody": "openvault",
        "url": arming_.url,
        "credential": core.identity(),
        "live_5000_ci": False,
    }


def arming() -> dict[str, Any]:
    """Armed means OpenVault's own status says so. Fail-closed, reason named."""
    return _arming_view(arm())


def require_armed() -> dict[str, Any]:
    snap = arming()
    if not snap.get("armed"):
        raise LLMError(
            "OpenVault FreeRoute unarmed: " + str(snap.get("detail") or "unreachable") + _UNARMED_SUFFIX
        )
    return snap


def public_identity() -> dict[str, Any]:
    """Stable Cortex attribution plus the credential view. No network, no token."""
    return {
        "ok": True,
        "identity": CORTEX_IDENTITY,
        "surfaces": {p: identity_for(p) for p in PURPOSES},
        "custody": "openvault",
        "authority": False,
        "credential": core.identity(),
        "mint": False,
        "token_returned": False,
        "seeded_cortex_primary": "disabled (HTTP 404 seed is not this identity)",
        "live_5000_ci": False,
        "measured_baseline": dict(DMS_180_BASELINE),
    }


# -- candidates and pick --------------------------------------------------------


def _provider_for(arming_: core.Arming, model: str) -> str:
    for provider, models in arming_.catalogue:
        if model in models:
            return provider
    return "openvault"


def candidates(purpose: str = "insights") -> list[dict[str, Any]]:
    """Live hops x OpenVault catalogue, in core order. Empty when unarmed."""
    arming_ = arm()
    if not arming_.armed:
        return []
    models, source = core.candidates(arming_)
    return [
        {"label": _provider_for(arming_, m), "model": m, "kind": "freeroute", "source": source}
        for m in models
    ]


def pick_route(*, purpose: str = "think", arming_: core.Arming | None = None) -> RoutePick:
    """The core's measured pick for this purpose. Never a hardcoded vendor."""
    current = arming_ or arm()
    if not current.armed:
        raise LLMError("OpenVault FreeRoute unarmed: " + current.reason + _UNARMED_SUFFIX)
    picked = core.pick(task_for(purpose), current)
    return RoutePick(
        label=_provider_for(current, picked.requested),
        model=picked.requested,
        kind="freeroute",
        why=f"{picked.reason} ({picked.source})",
        score=picked.measured_score,
        identity=identity_for(purpose),
    )


def _route_from_stamp(arming_: core.Arming, stamp: core.RouteStamp, purpose: str) -> RoutePick | None:
    if not stamp.requested:
        return None
    return RoutePick(
        label=_provider_for(arming_, stamp.requested),
        model=stamp.requested,
        kind="freeroute",
        why=f"{stamp.pick_reason} ({stamp.source})",
        score=stamp.measured_score,
        identity=identity_for(purpose),
    )


# -- SQL helpers ----------------------------------------------------------------

extract_sql = extract_select


def _cte_names(stmt: Any) -> set[str]:
    from sqlglot import exp

    return {str(cte.alias_or_name or "").lower() for cte in stmt.find_all(exp.CTE)} - {""}


def validate_sql(
    sql: str,
    allowed_tables: set[str],
    *,
    columns: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Static, ontology-constrained SELECT check. Crew never executes it.

    Table scope always; the column guardrail only when the ranking listed
    columns for every referenced table. ``check`` says which ran. This is not
    EXPLAIN, not the manifest enforcer, and not a run - the customer text says so.
    """
    import sqlglot
    from sqlglot import exp

    from CortexOS.dms import sql_guardrail
    from CortexOS.dms.l2_plausibility import sql_table_names

    def refuse(reason: str, tables: list[str] | None = None) -> dict[str, Any]:
        return {"ok": False, "sql": None, "tables": tables or [], "reason": reason, "check": "refused"}

    allowed = {str(t).lower() for t in allowed_tables if t}
    if not allowed:
        return refuse("no ranked ontology tables; cannot prove the SQL stays in scope")
    text = (sql or "").strip()
    if not text:
        return refuse("empty sql")
    try:
        statements = sqlglot.parse(text, read="duckdb")
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        return refuse(f"sql parse error: {core.redact(exc, limit=160)}")
    if not statements or statements[0] is None:
        return refuse("empty sql")
    if len(statements) > 1:
        return refuse("more than one statement")
    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        return refuse("not a select")
    for node in stmt.walk():
        if isinstance(node, sql_guardrail.FORBIDDEN):
            return refuse(f"non-select sql refused ({type(node).__name__.lower()})")
    tables = sorted(sql_table_names(text) - _cte_names(stmt))
    if not tables:
        return refuse("select has no from/join table")
    extra = sorted(set(tables) - allowed)
    if extra:
        return refuse("sql reads tables outside ontology ranking: " + ",".join(extra), tables)
    listed = {str(t).lower(): [str(c) for c in cols or []] for t, cols in (columns or {}).items()}
    bare = [t for t in tables if not listed.get(t)]
    if columns is not None and not bare:
        result = sql_guardrail.validate_sql(
            text, {"tables": {t: {"columns": listed[t]} for t in tables}}
        )
        if not result.passed or not result.safe_sql:
            return refuse("column guardrail: " + ", ".join(result.violations or ["rejected"]), tables)
        return {"ok": True, "sql": result.safe_sql, "tables": tables, "reason": "", "check": "column guardrail"}
    return {
        "ok": True,
        "sql": text,
        "tables": tables,
        "reason": "",
        "check": f"table-level check only (ranking listed no columns for {bare[0] if bare else tables[0]})",
    }


# -- status ---------------------------------------------------------------------


def public_status(purpose: str = "insights") -> dict[str, Any]:
    """Core status plus the crew's chosen/refused view. Hop rows are public-only."""
    arming_ = arm()
    snap = _arming_view(arming_)
    task = task_for(purpose)
    chosen: dict[str, Any] | None = None
    refused: str | None = None
    if arming_.armed:
        status = core.public_status(task)
        chosen = pick_route(purpose=purpose, arming_=arming_).as_public()
    else:
        # Never call the core status when crew is off: it would probe the vault.
        status = {
            "layer": "OpenVault FreeRoute (one Cortex model layer)",
            "impl": core.IMPL,
            "arming": arming_.public(),
            "identity": core.identity(),
            "candidates": [],
            "candidate_source": "",
            "measured": {},
            "scoreboard": core.scoreboard(task),
            "store": str(core.store_path()),
            "store_error": core.store_error(),
        }
        refused = arming_.reason
    return {
        "ok": arming_.armed,
        "armed": arming_.armed,
        "custody": "openvault",
        "identity": public_identity(),
        "chosen": chosen,
        "candidates": candidates(purpose) if arming_.armed else [],
        "refused": refused,
        "arming": snap,
        "task": task,
        "core": status,
        "live_5000_ci": False,
        "measured_baseline": dict(DMS_180_BASELINE),
        "layer": "OpenVault FreeRoute is the one Cortex model layer when a model is needed",
    }


# -- complete -------------------------------------------------------------------

_PURPOSE_SYSTEM = {
    "prompt": (
        "You write one prompt for a later model call. Return the prompt only. "
        "Do not invent API keys, numbers, or live CI."
    ),
    "think": (
        "You think through the operator ask. No tools. Do not invent numbers. "
        "gen_cfsm and dag_runner already exist on Cortex; do not clone them."
    ),
    "act": (
        "You may call offered tools. Do not invent keys, numbers, or live CI. "
        "Refuse when OpenVault would be required and is unarmed."
    ),
    "insights": (
        "You help Cortex Insights. Ontology is already ranked. "
        "Do not invent warehouse numbers."
    ),
    "generative_ask": (
        "You write a single DuckDB SELECT for the ranked ontology tables. "
        "Use ONLY those tables and columns. No DDL/DML. SQL only. "
        "Do not invent numeric answers in prose."
    ),
}


async def complete_core(task: str, messages: list[dict[str, Any]], **kwargs: Any) -> core.Completion:
    """One core call on the crew executor. ``CREW_OPENVAULT=0`` never sends."""
    if crew_off():
        return core.Completion(ok=False, reason="FreeRoute not armed: " + CREW_OFF_REASON)
    return await run_core(core.complete, task, messages, **kwargs)


def _refusal(purpose: str, refused: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "status": "REFUSE",
        "purpose": purpose,
        "identity": identity_for(purpose),
        "refused": refused,
        "values": [],
        "live_5000_ci": False,
    }
    body.update(extra)
    return body


async def complete(
    messages: list[dict[str, Any]] | None = None,
    *,
    purpose: str = "think",
    prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
    pick: RoutePick | None = None,
    timeout: int = 180,
    bearer: str | None = None,
) -> dict[str, Any]:
    """Generate / think / act via OpenVault FreeRoute. Fail-closed when unarmed.

    ``bearer=None`` spends Cortex's own credential (in-process callers). An HTTP
    relay passes the caller's ``ov_`` key or ``""`` for the loopback tier; the
    Cortex key is never lent to a relayed call.
    """
    purpose_key = _purpose_key(purpose or "think")
    if purpose_key not in PURPOSES:
        return _refusal(purpose, f"unknown purpose '{purpose}'")
    arming_ = await run_core(arm)
    snap = _arming_view(arming_)
    if not arming_.armed:
        return _refusal(
            purpose_key,
            "OpenVault FreeRoute unarmed: " + arming_.reason + _UNARMED_SUFFIX,
            arming=snap,
            measured_baseline=dict(DMS_180_BASELINE),
        )

    body = list(messages or [])
    if prompt.strip():
        body.append({"role": "user", "content": prompt.strip()})
    if not body:
        return _refusal(
            purpose_key,
            "empty messages",
            route=pick.as_public() if pick else None,
            arming=snap,
        )
    if not any(m.get("role") == "system" for m in body):
        body = [{"role": "system", "content": _PURPOSE_SYSTEM[purpose_key]}, *body]

    completion = await complete_core(
        task_for(purpose_key),
        body,
        max_tokens=_MAX_TOKENS.get(purpose_key, _DEFAULT_MAX_TOKENS),
        timeout=float(timeout),
        tools=tools if purpose_key == "act" else None,
        pin=pick.model if pick else "",
        pin_source="caller pick" if pick else "",
        egress="leave" if purpose_key == "generative_ask" else "",
        bearer=bearer,
    )
    stamp = completion.stamp
    route = pick or (_route_from_stamp(arming_, stamp, purpose_key) if stamp else None)
    if not completion.ok:
        return _refusal(
            purpose_key,
            completion.reason,
            route=route.as_public() if route else None,
            arming=snap,
            stamp=stamp.public() if stamp else None,
            measured_baseline=dict(DMS_180_BASELINE),
        )
    usage = completion.usage
    return {
        "ok": True,
        "status": "OK",
        "purpose": purpose_key,
        "identity": identity_for(purpose_key),
        "route": route.as_public() if route else None,
        "text": completion.text,
        "model": stamp.served if stamp else "",
        "tool_calls": list(completion.message.get("tool_calls") or []),
        "values": [],
        "arming": snap,
        "stamp": stamp.public() if stamp else None,
        "live_5000_ci": False,
        "measured_baseline": dict(DMS_180_BASELINE),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }
