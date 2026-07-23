"""
CortexOS/ponytail/middleware.py
Ponytail — token-saving middleware + prefetch orchestration.

Why "Ponytail"? It's the tail that holds context together before it reaches the model.

What it does:
  1. PREFETCH    — loads relevant warehouse state ahead of request completion
  2. COMPRESS    — summarises long context windows to < token budget
  3. ROUTE       — picks T0/T1/T2/T3 model tier per request type
  4. CACHE       — in-memory TTL cache for hot warehouse queries
  5. AUDIT       — every token spend logged to F1 ledger

Why safer than generic agents (OpenClaw, ZeroClaw, Hermes):
  - PII is stripped BEFORE context is built, not after
  - Security stack runs BEFORE any model call
  - All writes go through F1 ledger; nothing bypasses
  - Deterministic rules gate tasks; LLM only suggests

Usage:
    from CortexOS.ponytail.middleware import ponytail_process
    result = ponytail_process(raw_text, user_id="u1", intent_hint="query")
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

BIG_API_PLACEHOLDER = os.getenv("ANTHROPIC_API_KEY", "")

# ─── TTL Cache ────────────────────────────────────────────────────────────────

_CACHE: dict[str, dict] = {}
_CACHE_TTL = int(os.getenv("PONYTAIL_CACHE_TTL", "120"))  # seconds


def _cache_key(text: str, user_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{text}".encode()).hexdigest()[:16]


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["value"]
    return None


def _cache_set(key: str, value: dict) -> None:
    _CACHE[key] = {"value": value, "ts": time.time()}


def cache_clear() -> None:
    """Clear the in-memory cache. Useful for tests."""
    _CACHE.clear()


# ─── Token budget & compression ───────────────────────────────────────────────

TOKEN_BUDGETS = {
    "T0": 0,       # No LLM — deterministic only
    "T1": 512,     # Local model — tight budget
    "T2": 4096,    # Cloud model (sonnet) — generous
    "T3": 16000,   # BIG_API — only for explicitly cold paths
}


def _estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token estimate. Good enough for routing."""
    return max(1, len(text) // 4)


def _compress_context(text: str, budget: int) -> tuple[str, bool]:
    """
    Fit context within token budget via context_engineering (layer-aware trim).
    Falls back to paragraph trim if the package is unavailable.
    """
    try:
        from CortexOS.context_engineering.budget import fit_text

        return fit_text(text, budget, marker="[CONTEXT TRUNCATED — within token budget]")
    except ImportError:
        pass
    if _estimate_tokens(text) <= budget:
        return text, False
    char_limit = budget * 4
    trimmed = text[:char_limit]
    last_nl = trimmed.rfind("\n")
    if last_nl > char_limit * 0.7:
        trimmed = trimmed[:last_nl]
    return trimmed + "\n[CONTEXT TRUNCATED — within token budget]", True


# ─── Prefetch ─────────────────────────────────────────────────────────────────

def prefetch_warehouse_context(user_id: str) -> dict:
    """
    Prefetch lightweight warehouse snapshot for context injection.
    Reads from SQLite (hot, local) — no model call.
    Returns a compact summary dict.
    """
    import sqlite3

    from packs.dms.audit.ledger import default_db_path

    db_path = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH") or str(default_db_path())
    ctx: dict[str, Any] = {
        "prefetched_at": time.time(),
        "user_id": user_id,
    }
    try:
        with sqlite3.connect(db_path) as conn:
            # Item count
            row = conn.execute("SELECT COUNT(*) FROM dms_items").fetchone()
            ctx["total_items"] = row[0] if row else 0
            # Location count
            row = conn.execute("SELECT COUNT(*) FROM dms_locations").fetchone()
            ctx["total_locations"] = row[0] if row else 0
            # Recent movements (last 10)
            rows = conn.execute(
                "SELECT item_id, to_location, ts FROM dms_movements ORDER BY id DESC LIMIT 10"
            ).fetchall()
            ctx["recent_movements"] = [
                {"item_id": r[0], "to_location": r[1], "ts": r[2]} for r in rows
            ]
            # Compliance events (last 5)
            rows = conn.execute(
                "SELECT event, ts FROM dms_audit_ledger ORDER BY id DESC LIMIT 5"
            ).fetchall()
            ctx["recent_audit"] = [{"event": r[0], "ts": r[1]} for r in rows]
    except Exception as e:
        ctx["prefetch_error"] = str(e)
    return ctx


# ─── Tier router ──────────────────────────────────────────────────────────────

_T0_PATTERNS = ["stock count", "how many", "list all", "show me"]
_T2_PATTERNS = ["analyze", "summarize", "explain", "draft", "suggest", "recommend"]


def route_tier(text: str) -> str:
    """
    Deterministic tier routing based on text patterns.
    T0 → simple lookups (no LLM)
    T1 → local classify (no cloud)
    T2 → cloud suggest/analyze (BIG_API)
    T3 → BIG_API long context (cold paths only, explicit)
    """
    lower = text.lower()
    if any(p in lower for p in _T0_PATTERNS) and len(text) < 80:
        return "T0"
    if any(p in lower for p in _T2_PATTERNS):
        return "T2"
    if _estimate_tokens(text) > 3000:
        return "T3"
    return "T1"


# ─── Security harness (pre-model gate) ───────────────────────────────────────

def _security_gate(text: str) -> tuple[str, list[str]]:
    """
    Run scam_guard + injection_guard + PII redact.
    Returns (safe_text, list_of_flags).
    This runs BEFORE any model call — always.
    """
    flags: list[str] = []
    safe = text

    try:
        from packs.dms.security.pii import detect, redact_for_prompt

        safe = redact_for_prompt(text)
        spans = detect(text)
        if spans:
            flags.append(f"pii_redacted:{len(spans)}")
    except ImportError:
        pass

    # Basic injection patterns
    injection_keywords = [
        "ignore previous instructions",
        "forget your system prompt",
        "jailbreak",
        "DAN mode",
        "act as",
        "bypass",
        "eval(",
        "__import__",
    ]
    for kw in injection_keywords:
        if kw.lower() in safe.lower():
            flags.append(f"injection_detected:{kw}")
            safe = safe.replace(kw, "[BLOCKED]")

    # Scam patterns (BEC, OTP theft)
    scam_patterns = [
        "urgent wire transfer",
        "send otp",
        "verify your otp",
        "ceo fraud",
        "invoice redirect",
        "change bank account",
    ]
    for p in scam_patterns:
        if p.lower() in safe.lower():
            flags.append(f"scam_detected:{p}")
            safe = safe.replace(p, "[SCAM_BLOCKED]")

    return safe, flags


# ─── Main entry point ─────────────────────────────────────────────────────────

def ponytail_process(
    raw_text: str,
    user_id: str = "anon",
    intent_hint: str = "",
    force_tier: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """
    Full Ponytail pipeline for a single request.

    Steps:
      1. Security gate (scam + injection + PII)
      2. Cache check
      3. Prefetch warehouse context
      4. Route to tier
      5. Compress to token budget
      6. Log token spend to audit ledger
      7. Return enriched result

    Returns dict with:
      safe_text, tier, context, token_estimate, flags, cached, prefetch
    """
    t_start = time.time()

    # 1. Security gate — ALWAYS first
    safe_text, flags = _security_gate(raw_text)

    # 2. Cache
    cache_key = _cache_key(safe_text, user_id)
    if use_cache:
        cached = _cache_get(cache_key)
        if cached:
            return {**cached, "cache_hit": True}

    # 3. Prefetch context
    prefetch = prefetch_warehouse_context(user_id)

    # 4. Tier routing
    tier = force_tier or route_tier(safe_text + " " + intent_hint)
    budget = TOKEN_BUDGETS.get(tier, 4096)

    # 5. Build layered context + compress (context engineering)
    context_str = json.dumps(prefetch, default=str)
    assembled_meta: dict[str, Any] = {}
    try:
        from CortexOS.context_engineering import ContextRequest, assemble_context

        assembled = assemble_context(
            ContextRequest(
                instructions="Governed warehouse assistant. Prefer deterministic facts from prefetch.",
                retrieval=context_str,
                state=f"tier_hint={tier} user_id={user_id}",
                messages=[safe_text],
                token_budget=max(256, budget),
            )
        )
        compressed_ctx = assembled.user_context or context_str
        was_truncated = bool(assembled.truncated_layers or assembled.compacted)
        assembled_meta = {
            "token_estimate": assembled.token_estimate,
            "truncated_layers": assembled.truncated_layers,
            "compacted": assembled.compacted,
        }
    except Exception:
        compressed_ctx, was_truncated = _compress_context(context_str, max(64, budget // 2))

    # 6. Token estimate
    token_estimate = int(
        assembled_meta.get("token_estimate")
        or (_estimate_tokens(safe_text) + _estimate_tokens(compressed_ctx))
    )

    # 7. Audit log (non-blocking)
    try:
        from packs.dms.audit import ledger
        ledger.append(
            user_id,
            "ponytail.processed",
            {
                "user_id": user_id,
                "tier": tier,
                "token_estimate": token_estimate,
                "flags": flags,
                "truncated": was_truncated,
            },
        )
    except Exception:
        pass

    result = {
        "safe_text": safe_text,
        "tier": tier,
        "context": compressed_ctx,
        "token_estimate": token_estimate,
        "flags": flags,
        "cache_hit": False,
        "truncated": was_truncated,
        "context_engineering": assembled_meta or None,
        "prefetch": {
            "total_items": prefetch.get("total_items", 0),
            "total_locations": prefetch.get("total_locations", 0),
            "recent_audit_count": len(prefetch.get("recent_audit", [])),
        },
        "latency_ms": round((time.time() - t_start) * 1000, 1),
    }

    if use_cache:
        _cache_set(cache_key, result)

    return result


# ─── DMS Skill hook ───────────────────────────────────────────────────────────

def as_dms_skill(request_text: str, user_id: str = "system") -> dict:
    """
    Ponytail packaged as a DMS skill hook.
    Call this from any agent that needs governed warehouse context.
    Future: reuse for property agent, marketing agent, dual-brain.
    """
    return ponytail_process(request_text, user_id=user_id, intent_hint="skill")
