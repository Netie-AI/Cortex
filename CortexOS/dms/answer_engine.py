"""Q2 — the adaptive answer engine (Netie Cortex router).

Answers a question at the first layer that can do so *trustworthily*, and
abstains rather than guess. This is the "adaptive, fail-then-escalate" core:

  L0 CERTIFIED   exact (normalized) match against the verified-query repo →
                 deterministic replay. Highest trust, zero LLM.
  L1 METRIC      rule-based intent+slot routing → compile a governed metric
                 template (Q1) → guardrail-verified SQL. Deterministic.
  L2 FREEFORM    (flag DMS_L2_ENABLED, default OFF) sampled LLM SQL →
                 parse+allowlist+execute+vote+rails. Not wired until a model is.
  L3 ABSTAIN     no trustworthy layer fired → clarify + suggest nearest
                 answerable questions. Never the old confident-wrong fallback.

Every answer carries {layer, badge, sql_used, total_count, assumptions} so the
UI can show provenance and disclose truncation honestly.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

import sqlglot

from CortexOS.dms.sql_guardrail import MAX_LIMIT, guard_and_execute
from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection, load_semantic_layer

# Reused from the existing service (loaded lazily to avoid import cycle at module load).
ABSTAIN = "needs_clarification"


@dataclass(slots=True)
class MetricPlan:
    metric_id: str
    slots: dict[str, Any]
    reason: str


# ── normalization + certified index ──────────────────────────────────────────
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _certified_index() -> dict[str, Any]:
    from packs.dms.semantic.loader import load_all

    model = load_all()
    return {_normalize(cq.question): cq for cq in model.certified}


def match_certified(question: str):
    """L0 — EXACT normalized match only (high precision; never fuzzy, so a
    scoped question can't collide with an unscoped certified query)."""
    return _certified_index().get(_normalize(question))


# ── slot extractors ──────────────────────────────────────────────────────────
def _extract_limit(q: str, default: int) -> int:
    from CortexOS.dms.query_service import _extract_limit as base

    return base(q, default=default)


def _explicit_limit(q: str) -> int | None:
    """Only an EXPLICIT count ('top 3', '5 warehouses') — a bare superlative
    ('the most free capacity') is NOT a count and returns None, so a singular
    'which warehouse' correctly resolves to 1."""
    from CortexOS.dms.query_service import NUMBER_WORDS

    m = re.search(r"\b(?:top|bottom|first|last|show|give me)\s+(\d{1,4})\b", q) or \
        re.search(r"\b(\d{1,4})\s+(?:warehouses?|locations?|skus?|suppliers?|rows?|results?|items?)\b", q)
    if m:
        return int(m.group(1))
    mw = re.search(r"\b(?:top|first)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b", q)
    if mw:
        return NUMBER_WORDS[mw.group(1)]
    return None


def _direction(q: str) -> str:
    return "ASC" if re.search(r"\b(least|lowest|smallest|fewest|worst|bottom|min)\b", q) else "DESC"


def _threshold(q: str, default: float = 0.7) -> float:
    m = re.search(r"(?:above|over|greater than|more than|exceed(?:s|ing)?|>=?)\s*(\d*\.?\d+)", q)
    return float(m.group(1)) if m else default


def _threshold_op(q: str) -> str:
    if re.search(r"\b(below|under|less than|lower than)\b|<", q):
        return "<"
    return ">"


def _days(q: str, default: int) -> int:
    if re.search(r"\bthis week\b", q):
        return 7
    m = re.search(r"\b(?:last|past|within|previous)\s+(\d+)\s*days?\b", q)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b(\d+)\s*days?\b", q)
    return int(m2.group(1)) if m2 else default


def _pct(q: str, default: int = 90) -> int:
    m = re.search(r"(?:above|over|more than|>)\s*(\d{1,3})\s*(?:percent|%)", q)
    return int(m.group(1)) if m else default


def _location(question: str) -> str | None:
    from packs.dms.semantic import values as vd

    res = vd.resolve(question, "location_code")
    return res.value if res.ok else None


# ── L1 metric router (ordered; specific rules before generic) ────────────────
def route_to_metric(question: str) -> MetricPlan | None:
    q = question.lower()

    # scalars first — "how many X" must not fall through to a listing
    if re.search(r"\b(how many|number of|count of|count)\b", q) and "cold storage" in q:
        return MetricPlan("cold_storage_count", {}, "count of cold-storage locations")
    if re.search(r"\bhow many\b", q) and re.search(r"\bskus?\b", q) and not re.search(r"\b(category|per|by)\b", q):
        return MetricPlan("sku_count", {}, "distinct SKU count")

    # per-warehouse / per-carrier breakdowns of shipments (before the status listing)
    if "delayed" in q and re.search(r"\bcarrier", q):
        return MetricPlan("count_by_carrier", {"status": "DELAYED"}, "delayed shipments grouped by carrier")
    if re.search(r"\b(per|by|each)\b", q) and re.search(r"\b(warehouse|destination|location)\b", q) \
            and ("delayed" in q or "incoming" in q or "shipment" in q):
        status = "DELAYED" if "delayed" in q else "IN_TRANSIT"
        return MetricPlan("count_by_destination", {"status": status}, f"{status} shipments grouped by destination")

    # revenue over a window
    if re.search(r"\b(revenue|sales|sold)\b", q) and re.search(r"\b(last|past|within|previous)\b.*\bday", q):
        return MetricPlan("revenue_windowed", {"days": _days(q, 30)}, "revenue over a rolling window")

    # supplier risk threshold
    if re.search(r"\brisk\b", q) and re.search(r"\b(above|over|below|under|greater|less|more than|exceed|>|<)\b", q):
        return MetricPlan("suppliers_by_risk",
                          {"threshold": _threshold(q), "op": _threshold_op(q)},
                          "suppliers filtered by risk-score threshold")

    # average lead time by country
    if re.search(r"\baverage\b|\bmean\b|\bavg\b", q) and "lead time" in q:
        return MetricPlan("avg_lead_time_by_country", {}, "average lead time grouped by country")

    # free capacity ranking
    if re.search(r"\bfree\b|\bspare\b|\bavailable\b", q) and "capacit" in q:
        return MetricPlan("free_capacity",
                          {"limit": _explicit_limit(q) or 1, "direction": _direction(q)},
                          "warehouses ranked by free capacity")

    # capacity above a percentage
    if "capacit" in q and re.search(r"\b(above|over|more than)\b.*\d", q):
        return MetricPlan("capacity_above", {"pct": _pct(q)}, "locations above a capacity threshold")
    if "capacit" in q and re.search(r"\b(utilis|utiliz|how full|usage)\b", q):
        return MetricPlan("capacity_utilisation", {}, "capacity utilisation per location")

    # arriving window
    if "arriving" in q or ("incoming" in q and re.search(r"\bweek|\bdays?\b", q)):
        return MetricPlan("arriving_window", {"days": _days(q, 7)}, "in-transit shipments arriving within a window")

    # shipment status listing
    for status in ("delayed", "in transit", "in_transit", "pending", "delivered", "cancelled"):
        if status in q and re.search(r"\bshipments?\b", q):
            norm = "IN_TRANSIT" if status.startswith("in ") or status == "in_transit" else status.upper()
            return MetricPlan("shipments_by_status", {"status": norm}, f"shipments with status {norm}")

    # cold storage listing
    if "cold storage" in q:
        return MetricPlan("cold_storage_list", {}, "cold-storage locations")

    # low stock (optionally warehouse-scoped)
    if re.search(r"\b(below reorder|low stock|understocked|reorder level)\b", q):
        loc = _location(question)
        return MetricPlan("low_stock", {"wh": loc} if loc else {},
                          f"items below reorder level{' at ' + loc if loc else ''}")

    # not restocked window
    if re.search(r"\b(not restocked|stale)\b", q) or ("restock" in q and "not" in q):
        return MetricPlan("stale_restock", {"days": _days(q, 30)}, "items not restocked within a window")

    # expired
    if "expired" in q or "past expiry" in q:
        return MetricPlan("expired_items", {}, "expired inventory")

    # active alerts
    if "alert" in q and re.search(r"\b(active|open|unresolved|current)\b", q):
        return MetricPlan("active_alerts", {}, "unresolved alerts")

    # sales ranking
    if re.search(r"\b(top|best|highest|most)\b", q) and re.search(r"\b(sell|sold|revenue|sales)\b", q):
        if re.search(r"\b(quantity|volume|kg|units?)\b", q):
            return MetricPlan("sales_by_volume", {"limit": _extract_limit(q, 5), "direction": _direction(q)},
                              "SKUs ranked by quantity sold")
        return MetricPlan("sales_by_value", {"limit": _extract_limit(q, 5), "direction": _direction(q)},
                          "SKUs ranked by sales value")

    return None


# ── truncation-honest total ──────────────────────────────────────────────────
def _true_count(safe_sql: str, con) -> int | None:
    """COUNT(*) over the query with LIMIT/ORDER stripped — the honest total
    behind a possibly-capped listing. Returns None if it can't be computed."""
    try:
        tree = sqlglot.parse_one(safe_sql, read="duckdb")
        tree.set("limit", None)
        tree.set("order", None)
        inner = tree.sql(dialect="duckdb")
        return int(con.execute(f"SELECT COUNT(*) AS n FROM ({inner}) _t").fetchone()[0])
    except Exception:  # noqa: BLE001
        return None


# ── suggestions for abstain ──────────────────────────────────────────────────
def _suggestions(question: str, limit: int = 3) -> list[str]:
    """Nearest answerable questions (token overlap over certified + metric synonyms)."""
    from packs.dms.semantic.loader import load_all

    model = load_all()
    qtokens = set(_normalize(question).split())
    scored: list[tuple[float, str]] = []
    for cq in model.certified:
        overlap = len(qtokens & set(_normalize(cq.question).split()))
        if overlap:
            scored.append((overlap, cq.question))
    scored.sort(key=lambda t: -t[0])
    seen: list[str] = []
    for _, qtext in scored:
        if qtext not in seen:
            seen.append(qtext)
        if len(seen) >= limit:
            break
    if not seen:  # cold: offer a stable default trio
        seen = [
            "Top 5 selling SKUs by revenue",
            "Which SKUs are below reorder level in warehouse A?",
            "Show warehouse capacity utilisation",
        ][:limit]
    return seen


def _abstain(question: str, audit_id: str, *, reason: str) -> dict[str, Any]:
    suggestions = _suggestions(question)
    hint = " Try: " + " · ".join(f'"{s}"' for s in suggestions)
    return {
        "answer": (f"I can't answer that from the DMS semantic layer with confidence ({reason})."
                   + hint),
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": ABSTAIN,
        "rows": [],
        "source_table": None,
        "layer": "abstain",
        "badge": "abstain",
        "assumptions": reason,
        "total_count": 0,
        "suggestions": suggestions,
    }


# ── the engine ────────────────────────────────────────────────────────────────
def answer(question: str, *, session_id: str | None = None) -> dict[str, Any]:
    del session_id
    from CortexOS.dms.query_service import (
        _infer_source_table,
        build_chart_spec,
        plan_query,
        rag_answer,
        route_question,
        synthesize_answer,
    )

    audit_id = str(uuid.uuid4())
    route = route_question(question)

    if route == "blocked":
        return {
            "answer": "That operation is not permitted.", "sql_used": None, "chart_spec": None,
            "audit_id": audit_id, "violations_blocked": ["DDL_ATTEMPT"], "route": "blocked",
            "rows": [], "source_table": None, "layer": "blocked", "badge": "blocked",
            "assumptions": "destructive operation refused", "total_count": 0,
        }
    if route == "rag":
        ans, sources = rag_answer(question)
        return {
            "answer": ans, "sql_used": None, "chart_spec": None, "audit_id": audit_id,
            "violations_blocked": [], "route": "rag", "sources": sources, "rows": [],
            "source_table": None, "layer": "rag", "badge": "document", "assumptions": "",
            "total_count": 0,
        }

    # L0 certified → L1 metric → L3 abstain (L2 free-form flag-off)
    layer = badge = ""
    sql: str | None = None
    assumptions = ""

    cq = match_certified(question)
    if cq is not None:
        sql, layer, badge = cq.sql, "certified", "certified"
        assumptions = f"certified query {cq.id}"
    else:
        plan = route_to_metric(question)
        if plan is not None:
            from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

            try:
                sql = compile_metric(load_all(), plan.metric_id, plan.slots)
                layer, badge = "governed_metric", "governed_metric"
                assumptions = plan.reason
            except SemanticError as exc:
                return _abstain(question, audit_id, reason=f"could not resolve inputs: {exc}")
        else:
            if os.environ.get("DMS_L2_ENABLED", "").lower() in ("1", "true", "yes"):
                # L2 hook — intentionally not wired until a local SQL model exists.
                return _abstain(question, audit_id, reason="no verified answer path (L2 not wired)")
            return _abstain(question, audit_id, reason="no governed metric or certified query matched")

    semantic = load_semantic_layer()
    con = get_connection(DEFAULT_DB)
    try:
        guard_result, rows, entry = guard_and_execute(sql, semantic, con)
        total_count = _true_count(guard_result.safe_sql, con) if guard_result.passed else None
    finally:
        con.close()

    if not guard_result.passed:
        return _abstain(question, audit_id,
                        reason=f"internal SQL failed guardrail {guard_result.violations}")

    truncated = total_count is not None and len(rows) >= MAX_LIMIT and total_count > len(rows)
    answer_text = synthesize_answer(rows, question)
    if truncated:
        answer_text = f"{total_count} rows match; showing the first {len(rows)}.\n" + answer_text

    return {
        "answer": answer_text,
        "sql_used": guard_result.safe_sql,
        "chart_spec": build_chart_spec(rows, question),
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "sql",
        "row_count": len(rows),
        "rows": rows,
        "total_count": total_count if total_count is not None else len(rows),
        "truncated": truncated,
        "source_table": _infer_source_table(sql),
        "layer": layer,
        "badge": badge,
        "assumptions": assumptions,
        "query_plan": plan_query(question, sql).to_dict(),
        "audit": {"timestamp": entry.timestamp, "passed": entry.passed, "violations": entry.violations},
    }
