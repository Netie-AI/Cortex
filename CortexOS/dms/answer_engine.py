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

from CortexOS.dms.sql_guardrail import (
    MAX_LIMIT,
    AuditEntry,
    guard_and_execute,
    log_audit,
)
from CortexOS.dms.warehouse_db import (
    DEFAULT_DB,
    get_connection,
    load_semantic_layer,
    read_only_queries_enabled,
)
from CortexOS.execution.manifest import VerifiedManifest

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


def _wants_aggregate(q: str) -> bool:
    """Count/avg/how-many — must beat listing synonyms like bare 'expired'."""
    return bool(
        re.search(
            r"\b(how many|number of|count of|\bcount\b|average|avg|mean|total)\b",
            q,
        )
    )


def _calendar_month(q: str) -> str | None:
    """Return 'last' | 'this' when the question names a calendar month window."""
    if re.search(r"\b(last|previous|prior)\s+month\b", q):
        return "last"
    if re.search(r"\bthis\s+month\b", q):
        return "this"
    return None


def _pct(q: str, default: int = 90) -> int:
    m = re.search(r"(?:above|over|more than|>)\s*(\d{1,3})\s*(?:percent|%)", q)
    return int(m.group(1)) if m else default


def _location(question: str) -> str | None:
    from packs.dms.semantic import values as vd

    res = vd.resolve(question, "location_code")
    return res.value if res.ok else None


_EXCLUSION_STOP = re.compile(
    r"\b(?:what|show|list|give|find|get|top|bottom|best|worst|highest|lowest|"
    r"ranked?|numbers?|ranks?|selling|sold)\b",
    re.I,
)
_EXCLUSION_SKIP = frozenset(
    {"THE", "A", "AN", "SKU", "AND", "OR", "FROM", "BY", "OF", "ALL", "ANY"}
)


def _excluded_skus(q: str) -> list[str]:
    """Named SKUs to drop from a ranking.

    Captures the full exclusion clause so ``excluding SKU-A and SKU-B`` keeps
    both tokens (the old regex only took the first token after the verb).
    """
    out: list[str] = []
    for m in re.finditer(
        r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|remov(?:e|ing)|drop(?:ping)?|without|except)\s+(?:the\s+)?(.+)",
        q,
        flags=re.I,
    ):
        clause = m.group(1)
        stop = _EXCLUSION_STOP.search(clause)
        if stop:
            clause = clause[: stop.start()]
        for token in re.split(r"\s*(?:,|/|\band\b|\bor\b)\s*", clause, flags=re.I):
            t = token.strip().strip("'\"")
            tm = re.match(r"^([A-Za-z0-9][\w-]*)$", t)
            if not tm:
                continue
            t = tm.group(1).upper()
            if t in _EXCLUSION_SKIP or len(t) < 2:
                continue
            if t not in out:
                out.append(t)
    return out


def _rank_window(q: str) -> tuple[int, int] | None:
    """Parse '6-10', '6th to 10th', 'numbers 6 to 10', 'ranks 6-10' → (start, end) 1-based."""
    m = (
        re.search(
            r"\b(?:number|numbers|nos?|ranks?|positions?)\s*"
            r"(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b",
            q,
            flags=re.I,
        )
        or re.search(
            r"\b(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b"
            r".*\b(?:sku|skus|rank|revenue|sales)\b",
            q,
            flags=re.I,
        )
        or re.search(
            r"\b(?:sku|skus|rank|revenue|sales)\b.*\b"
            r"(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b",
            q,
            flags=re.I,
        )
    )
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if start > end:
        start, end = end, start
    if start < 1 or end > 1000 or (end - start + 1) > 100:
        return None
    return start, end


def _sales_rank_slots(q_raw: str) -> dict[str, Any]:
    window = _rank_window(q_raw)
    excluded = _excluded_skus(q_raw)
    slots: dict[str, Any] = {"direction": _direction(q_raw), "offset_clause": 0}
    if window:
        start, end = window
        slots["offset_clause"] = start - 1
        slots["limit"] = end - start + 1
    else:
        slots["limit"] = _extract_limit(q_raw, 5)
    if excluded:
        slots["exclude_skus"] = excluded
    return slots


def _wants_sales_rank(q: str, q_raw: str) -> bool:
    if re.search(r"\b(top|best|highest|most)\b", q) and re.search(
        r"\b(sell|sold|revenue|sales|sku|skus|revnue)\b", q
    ):
        return True
    if re.search(r"\btop\s+\d+\b", q):
        return True
    if _rank_window(q_raw) and re.search(r"\b(sku|skus|revenue|sales|sell)\b", q):
        return True
    return False


# ── L1 metric router (ordered; specific rules before generic) ────────────────
def route_to_metric(question: str) -> MetricPlan | None:
    """Pick a governed metric + its slots.

    Two views of the question, deliberately kept apart:
      ``q``     — normalized into router vocabulary; decides WHICH metric.
      ``q_raw`` — the untouched question; supplies every SLOT (limits,
                  thresholds, directions, day windows, percentages, locations).

    Slots must never come from the normalized text: normalization exists to
    widen recall over wording, and it must not be able to move a number, a
    threshold or a direction. See packs/dms/semantic/vocabulary.py.
    """
    from packs.dms.semantic.vocabulary import normalize_for_routing

    q_raw = question.lower()
    q = normalize_for_routing(question)

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

    # revenue — calendar month before rolling-day window; bare total before ranked "top sales"
    if re.search(r"\b(revenue|sales|sold)\b", q) and _calendar_month(q) == "last":
        return MetricPlan("revenue_last_month", {}, "revenue in the previous calendar month")
    if re.search(r"\b(revenue|sales|sold)\b", q) and re.search(r"\b(last|past|within|previous)\b.*\bday", q):
        return MetricPlan("revenue_windowed", {"days": _days(q_raw, 30)}, "revenue over a rolling window")
    # G6 — bare total revenue (no month/window); must not fall through to abstain
    if re.search(r"\btotal\b", q) and re.search(r"\b(revenue|sales)\b", q):
        return MetricPlan("revenue_total", {}, "total outbound revenue")
    if re.search(r"\b(revenue|sales)\b", q) and not re.search(
        r"\b(top|best|highest|most|sku|skus|rank|per|by|each)\b", q
    ):
        return MetricPlan("revenue_total", {}, "total outbound revenue")

    # supplier risk threshold
    if re.search(r"\brisk\b", q) and re.search(r"\b(above|over|below|under|greater|less|more than|exceed|>|<)\b", q):
        return MetricPlan("suppliers_by_risk",
                          {"threshold": _threshold(q_raw), "op": _threshold_op(q_raw)},
                          "suppliers filtered by risk-score threshold")

    # average lead time by country
    if re.search(r"\baverage\b|\bmean\b|\bavg\b", q) and "lead time" in q:
        return MetricPlan("avg_lead_time_by_country", {}, "average lead time grouped by country")

    # free capacity ranking
    if re.search(r"\bfree\b|\bspare\b|\bavailable\b", q) and "capacit" in q:
        return MetricPlan("free_capacity",
                          {"limit": _explicit_limit(q_raw) or 1, "direction": _direction(q_raw)},
                          "warehouses ranked by free capacity")

    # capacity above a percentage
    if "capacit" in q and re.search(r"\b(above|over|more than)\b.*\d", q):
        return MetricPlan("capacity_above", {"pct": _pct(q_raw)}, "locations above a capacity threshold")
    # utilis\w* / utiliz\w*, not utilis\b — the trailing \b made the word
    # "utilisation" itself fail to match, so this branch was only ever reachable
    # by the stem alone. The golden question hits L0 certified, which is why the
    # dead branch went unnoticed.
    if "capacit" in q and re.search(r"\b(utilis\w*|utiliz\w*|how full|usage)\b", q):
        return MetricPlan("capacity_utilisation", {}, "capacity utilisation per location")

    # arriving window
    if "arriving" in q or ("incoming" in q and re.search(r"\bweek|\bdays?\b", q)):
        return MetricPlan("arriving_window", {"days": _days(q_raw, 7)}, "in-transit shipments arriving within a window")

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
        return MetricPlan("stale_restock", {"days": _days(q_raw, 30)}, "items not restocked within a window")

    # expired — aggregate / calendar month BEFORE bare listing
    if "expired" in q or "past expiry" in q or "out of date" in q:
        month = _calendar_month(q)
        if month == "last" or (_wants_aggregate(q) and month == "last"):
            return MetricPlan("expired_last_month", {}, "count of items that expired last month")
        if _wants_aggregate(q):
            return MetricPlan("expired_count", {}, "count of currently expired inventory")
        return MetricPlan("expired_items", {}, "expired inventory listing")

    # active alerts
    if "alert" in q and re.search(r"\b(active|open|unresolved|current)\b", q):
        return MetricPlan("active_alerts", {}, "unresolved alerts")

    # sales ranking (after month/window scalars so "last month sales" never ranks)
    if _wants_sales_rank(q, q_raw):
        slots = _sales_rank_slots(q_raw)
        if re.search(r"\b(quantity|volume|kg|kilograms?|weight|units?)\b", q):
            return MetricPlan(
                "sales_by_volume",
                slots,
                "SKUs ranked by quantity sold",
            )
        return MetricPlan("sales_by_value", slots, "SKUs ranked by sales value")
    # unranked "last month sales" catch-all if earlier branch missed phrasing
    if re.search(r"\b(sales|revenue)\b", q) and _calendar_month(q) == "last":
        return MetricPlan("revenue_last_month", {}, "revenue in the previous calendar month")

    return None


# ── truncation-honest total ──────────────────────────────────────────────────
def _true_count(
    sql: str,
    con=None,
    *,
    verified: VerifiedManifest | None = None,
) -> int | None:
    """COUNT(*) over the query with LIMIT/ORDER stripped — the honest total
    behind a possibly-capped listing. Returns None if it can't be computed.

    When ``verified`` is set (contract live ask), the count runs through the
    C4 submit executor so predicates apply. Legacy callers still pass ``con``.
    """
    if verified is not None:
        from CortexOS.execution.submit import execute_count

        return execute_count(verified, sql)
    if con is None:
        return None
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
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


# ── session memory (follow-up anaphora) ───────────────────────────────────────
# Keyed by session_id + space_id so Space A never sees Space B's prior SQL (C6).
_SESSION: dict[str, dict[str, Any]] = {}


def _session_key(session_id: str | None, space_id: str | None = None) -> str:
    sid = (session_id or "demo").strip() or "demo"
    sp = (space_id or "").strip()
    return f"{sid}::space:{sp}" if sp else sid


def _remember(
    session_id: str | None,
    turn: dict[str, Any],
    *,
    space_id: str | None = None,
) -> None:
    _SESSION[_session_key(session_id, space_id)] = turn


def clear_session(session_id: str | None = None, *, space_id: str | None = None) -> None:
    if session_id is None and space_id is None:
        _SESSION.clear()
    else:
        _SESSION.pop(_session_key(session_id, space_id), None)


def _is_anaphora(q: str) -> bool:
    """Follow-up pronouns / arithmetic over the prior result set."""
    return bool(
        re.search(
            r"\b(them|those|these)\b|"
            r"\b(average|avg|mean|total|sum|count|how many)\s+of\s+(them|those|these|it)\b|"
            r"\bwhat is the average of (them|those|these|it)\b|"
            r"\baverage of (them|those|these)\b|"
            r"\bdivid(?:e|ed|ing)\b.*?\bby\s+\d|"
            r"\bmultipl(?:y|ied|ying)\b.*?\bby\s+\d|"
            r"(?:/|÷|×|\*)\s*\d|"
            r"\bone\s+fifth\b",
            q,
        )
    )


def _scale_factor(q: str) -> tuple[str, float] | None:
    """Return ('div'|'mul', factor) for session arithmetic follow-ups."""
    m = re.search(r"\bdivid(?:e|ed|ing)\b.*?\bby\s+(\d+(?:\.\d+)?)", q)
    if m:
        return "div", float(m.group(1))
    m = re.search(r"\bone\s+fifth\b", q)
    if m:
        return "div", 5.0
    m = re.search(r"(?:/|÷)\s*(\d+(?:\.\d+)?)", q)
    if m:
        return "div", float(m.group(1))
    m = re.search(r"\bmultipl(?:y|ied|ying)\b.*?\bby\s+(\d+(?:\.\d+)?)", q)
    if m:
        return "mul", float(m.group(1))
    m = re.search(r"(?:×|\*)\s*(\d+(?:\.\d+)?)", q)
    if m:
        return "mul", float(m.group(1))
    return None


def _numeric_columns(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    cols: list[str] = []
    for k, v in rows[0].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            cols.append(k)
        elif isinstance(v, str):
            try:
                float(v)
                cols.append(k)
            except ValueError:
                pass
    return cols


def _pick_measure(nums: list[str]) -> str | None:
    for prefer in (
        "sales_value_myr",
        "total_sold_kg",
        "revenue_myr",
        "quantity_kg",
        "total_value_myr",
        "ranking_score",
        "free_kg",
        "pct_used",
    ):
        if prefer in nums:
            return prefer
    for c in nums:
        if not re.search(r"(^id$|_id$|count$)", c, re.I):
            return c
    return None


def _prior_skus(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        sku = row.get("sku")
        if sku is None:
            continue
        text = str(sku).strip().upper()
        if text and text not in out:
            out.append(text)
    return out


def _low_stock_over_prior(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Filter inventory to prior-turn SKUs that are below reorder."""
    skus = _prior_skus(rows)
    if not skus:
        raise ValueError("prior result has no SKUs to check for low stock")
    quoted = ", ".join("'" + s.replace("'", "''") + "'" for s in skus)
    sql = (
        "SELECT sku, quantity_kg, reorder_level_kg, location_id, category, storage_bin "
        "FROM inventory "
        f"WHERE UPPER(sku) IN ({quoted}) AND quantity_kg < reorder_level_kg "
        "ORDER BY quantity_kg ASC"
    )
    return sql, []  # rows filled by execute


def _aggregate_prior(
    prior_sql: str,
    question: str,
    rows: list[dict[str, Any]],
    *,
    total_count: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Follow-up aggregate / scale over the prior result.

    COUNT uses a guarded subquery wrap. AVG and divide/multiply are computed
    from the prior row snapshot (literal SELECT so the allowlist still passes).
    """
    q = question.lower()
    wants_avg = bool(re.search(r"\b(average|avg|mean)\b", q))
    scale = _scale_factor(q)
    nums = _numeric_columns(rows)
    measure = _pick_measure(nums)

    if scale is not None:
        op, factor = scale
        if factor == 0 and op == "div":
            raise ValueError("division by zero")
        if not measure or not rows:
            raise ValueError("no numeric prior measure to scale")
        # Scalar prior, or explicit sum/total over multirow — otherwise ambiguous.
        if len(rows) > 1 and not re.search(r"\b(sum|total|altogether)\b", q):
            raise ValueError("ambiguous multirow scale; ask to sum them first")
        vals: list[float] = []
        for row in rows:
            raw = row.get(measure)
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not vals:
            raise ValueError("no numeric values to scale")
        base = sum(vals) if len(vals) > 1 else vals[0]
        result = round(base / factor, 2) if op == "div" else round(base * factor, 2)
        col = f"{'div' if op == 'div' else 'mul'}_{measure}"
        sql = f"SELECT CAST({result} AS DOUBLE) AS {col}"
        return sql, [{col: result}]

    if wants_avg and measure and rows:
        vals = []
        for row in rows:
            raw = row.get(measure)
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if vals:
            avg_val = round(sum(vals) / len(vals), 2)
            col = f"avg_{measure}"
            # Literal SELECT — no unknown column vs warehouse allowlist.
            sql = f"SELECT CAST({avg_val} AS DOUBLE) AS {col}"
            return sql, [{col: avg_val}]

    tree = sqlglot.parse_one(prior_sql, read="duckdb")
    tree.set("limit", None)
    tree.set("order", None)
    inner = tree.sql(dialect="duckdb")
    sql = f"SELECT COUNT(*) AS followup_count FROM ({inner}) _prior"
    if total_count is not None and not wants_avg:
        # Prefer honest total when prior listing was truncated
        return sql, [{"followup_count": int(total_count)}]
    return sql, []  # rows filled by execute


def _honest_plan(
    question: str,
    sql: str | None,
    *,
    layer: str,
    metric_id: str | None = None,
    skill_score: float | None = None,
    assumptions: str = "",
) -> dict[str, Any]:
    from CortexOS.dms.query_service import plan_query

    base = plan_query(question, sql).to_dict()
    # Real route wins over keyword heuristics for UI confidence.
    conf = 0.95 if layer in ("certified", "governed_metric") else 0.85
    if layer == "query_skill" and skill_score is not None:
        conf = min(0.99, max(0.72, float(skill_score)))
    if layer == "session":
        conf = 0.88
    intent = metric_id or layer or base.get("intent") or "unknown"
    base["intent"] = intent
    base["confidence"] = round(conf, 3)
    base["layer"] = layer
    base["metric_id"] = metric_id
    base["skill_score"] = round(skill_score, 3) if skill_score is not None else None
    base["assumptions"] = assumptions
    return base


# ── the engine ────────────────────────────────────────────────────────────────
def answer(
    question: str,
    *,
    session_id: str | None = None,
    space_id: str | None = None,
    verified: VerifiedManifest | None = None,
) -> dict[str, Any]:
    from CortexOS.dms.query_service import (
        _infer_source_table,
        build_chart_spec,
        rag_answer,
        route_question,
        synthesize_answer,
    )
    from packs.dms.semantic import query_skills

    audit_id = str(uuid.uuid4())
    route = route_question(question)

    if route == "blocked":
        return {
            "answer": "That operation is not permitted.", "sql_used": None, "chart_spec": None,
            "audit_id": audit_id, "violations_blocked": ["DDL_ATTEMPT"], "route": "blocked",
            "rows": [], "source_table": None, "layer": "blocked", "badge": "blocked",
            "assumptions": "destructive operation refused", "total_count": 0,
            "query_plan": _honest_plan(question, None, layer="blocked", assumptions="destructive"),
        }
    if route == "rag":
        ans, sources = rag_answer(question)
        return {
            "answer": ans, "sql_used": None, "chart_spec": None, "audit_id": audit_id,
            "violations_blocked": [], "route": "rag", "sources": sources, "rows": [],
            "source_table": None, "layer": "rag", "badge": "document", "assumptions": "",
            "total_count": 0,
            "query_plan": _honest_plan(question, None, layer="rag"),
        }

    layer = badge = ""
    sql: str | None = None
    assumptions = ""
    metric_id: str | None = None
    metric_slots: dict[str, Any] = {}
    skill_score: float | None = None

    q_low = question.lower()
    prior = _SESSION.get(_session_key(session_id, space_id))

    # Session anaphora — "average of them" / "which of those are low stock"
    session_rows: list[dict[str, Any]] | None = None
    if prior and prior.get("sql") and _is_anaphora(q_low):
        try:
            if re.search(r"\b(low stock|below reorder|below\s+reorder)\b", q_low):
                sql, session_rows = _low_stock_over_prior(prior.get("rows") or [])
            else:
                sql, session_rows = _aggregate_prior(
                    prior["sql"],
                    question,
                    prior.get("rows") or [],
                    total_count=prior.get("total_count"),
                )
            layer, badge = "session", "session"
            assumptions = f"follow-up over prior turn ({prior.get('metric_id') or prior.get('layer')})"
            metric_id = prior.get("metric_id")
        except Exception:  # noqa: BLE001
            sql = None
            session_rows = None

    # L0 certified → L1 metric → L-skill → L3 abstain
    # Skills run after governed routes so golden/certified paths stay authoritative.
    if sql is None:
        cq = match_certified(question)
        if cq is not None:
            sql, layer, badge = cq.sql, "certified", "certified"
            assumptions = f"certified query {cq.id}"
            metric_id = cq.id
        else:
            plan = route_to_metric(question)
            if plan is not None:
                from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

                try:
                    sql = compile_metric(load_all(), plan.metric_id, plan.slots)
                    layer, badge = "governed_metric", "governed_metric"
                    assumptions = plan.reason
                    metric_id = plan.metric_id
                    metric_slots = dict(plan.slots)
                except SemanticError as exc:
                    return _abstain(question, audit_id, reason=f"could not resolve inputs: {exc}")

    if sql is None:
        hit = query_skills.find(question)
        if hit is not None:
            skill_score = float(hit["score"])
            if hit.get("metric_id"):
                from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

                # Never replay turn-specific filters from a prior capture.
                # Sales ranks re-derive slots from THIS question; other metrics
                # keep only non-contextual stored params.
                stored = dict(hit.get("params") or {})
                contextual = {
                    "exclude_skus",
                    "offset_clause",
                    "limit",
                    "direction",
                    "days",
                    "location_code",
                    "warehouse",
                }
                if hit["metric_id"] in ("sales_by_value", "sales_by_volume"):
                    params = _sales_rank_slots(question)
                else:
                    params = {k: v for k, v in stored.items() if k not in contextual}
                try:
                    sql = compile_metric(load_all(), hit["metric_id"], params)
                    layer, badge = "query_skill", "query_skill"
                    assumptions = f"query skill match score={skill_score:.3f} → {hit['metric_id']}"
                    metric_id = hit["metric_id"]
                    metric_slots = dict(params)
                except SemanticError:
                    sql = None
            elif hit.get("sql_template"):
                sql = hit["sql_template"]
                layer, badge = "query_skill", "query_skill"
                assumptions = f"query skill match score={skill_score:.3f} (stored sql)"

    if sql is None:
        # C7-full L2: schema retrieval → FreeRoute generate → validate gate.
        # Never fall back to the L1 keyword cascade or a smaller model.
        if os.environ.get("DMS_L2_ENABLED", "").lower() in ("1", "true", "yes"):
            try:
                from CortexOS.dms.l2_registry import L2NotRegistered, resolve_l2
                from CortexOS.dms.sql_validate_gate import SqlGateAbstain, gate_with_retry
            except ImportError:  # noqa: BLE001
                return _abstain(question, audit_id, reason="no verified answer path (L2 import failed)")
            try:
                l2 = resolve_l2()
            except L2NotRegistered:
                return _abstain(question, audit_id, reason="no verified answer path (L2 import failed)")

            if not l2.sql_generator.is_configured():
                return _abstain(question, audit_id, reason="no verified answer path (L2 not wired)")

            semantic_early = load_semantic_layer()
            reduced = l2.schema_retrieval.retrieve(question)
            prior_box: dict[str, list[str]] = {"v": []}

            def _gen(prior: list[str]) -> str | None:
                prior_box["v"] = list(prior)
                cands = l2.sql_generator.generate_candidates(
                    question,
                    reduced,
                    prior_violations=prior,
                )
                return cands[0] if cands else None

            con_explain = None
            try:
                if verified is None:
                    con_explain = get_connection(
                        DEFAULT_DB, read_only=read_only_queries_enabled()
                    )
                gate = gate_with_retry(
                    _gen,
                    question,
                    semantic_early,
                    con=con_explain,
                    max_retries=2,
                )
            except SqlGateAbstain as exc:
                return _abstain(
                    question,
                    audit_id,
                    reason=f"L2 generation failed validation gate: {exc}",
                )
            finally:
                if con_explain is not None:
                    con_explain.close()

            if not gate.passed or not gate.safe_sql:
                return _abstain(question, audit_id, reason="L2 generation failed validation gate")

            sql = gate.safe_sql
            layer, badge = "generated", "L2_VALIDATED"
            assumptions = (
                f"L2 FreeRoute SQL over reduced schema "
                f"tables={list((reduced.get('tables') or {}).keys())}"
            )
            try:
                l2.promotion.record_validated(question, sql)
            except Exception:  # noqa: BLE001 — promotion signal must not block answers
                pass
        else:
            return _abstain(question, audit_id, reason="no governed metric or certified query matched")

    if sql is None:
        return _abstain(question, audit_id, reason="no governed metric or certified query matched")

    semantic = load_semantic_layer()
    # Contract live ask: semantic guardrail then C4 submit (enforce_manifest).
    # Legacy callers keep the old connection + guard_and_execute path.
    if verified is not None:
        from datetime import datetime, timezone

        from CortexOS.dms.sql_validate_gate import SqlGateAbstain, run_gate
        from CortexOS.execution.submit import execute_sql

        gate = run_gate(sql, semantic)
        guard_result = gate  # ValidateGateResult shares passed/safe_sql/violations
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_sql=sql,
            safe_sql=gate.safe_sql,
            violations=gate.violations,
            passed=gate.passed,
        )
        if not gate.passed or not gate.safe_sql:
            log_audit(entry)
            rows = []
            total_count = None
        elif session_rows is not None and len(session_rows) > 0 and layer == "session":
            rows = session_rows
            total_count = len(rows)
            entry.row_count = len(rows)
            log_audit(entry)
        else:
            try:
                rows, _, _ = execute_sql(verified, gate.safe_sql)
            except SqlGateAbstain as exc:
                entry.passed = False
                entry.violations = list(exc.violations)
                log_audit(entry)
                return _abstain(
                    question,
                    audit_id,
                    reason=f"SQL validation gate: {exc}",
                )
            total_count = _true_count(gate.safe_sql, verified=verified)
            entry.row_count = len(rows)
            log_audit(entry)
    else:
        # Every statement that reaches here has passed the read-only guardrail, so a
        # read-only handle is always sufficient. It is opt-in (DMS_READ_ONLY_QUERIES)
        # because it also has to be safe for the writer in this process — see
        # warehouse_db.read_only_queries_enabled.
        con = get_connection(DEFAULT_DB, read_only=read_only_queries_enabled())
        try:
            if session_rows is not None and len(session_rows) > 0 and layer == "session":
                # Precomputed AVG (literal SELECT still guardrail-checked)
                guard_result, rows, entry = guard_and_execute(sql, semantic, con)
                if guard_result.passed:
                    rows = session_rows
                total_count = len(rows) if guard_result.passed else None
            else:
                guard_result, rows, entry = guard_and_execute(sql, semantic, con)
                total_count = (
                    _true_count(guard_result.safe_sql, con) if guard_result.passed else None
                )
        finally:
            con.close()

    if not guard_result.passed:
        return _abstain(question, audit_id,
                        reason=f"internal SQL failed guardrail {guard_result.violations}")

    truncated = total_count is not None and len(rows) >= MAX_LIMIT and total_count > len(rows)
    answer_text = synthesize_answer(rows, question)
    if truncated:
        answer_text = f"{total_count} rows match; showing the first {len(rows)}.\n" + answer_text

    # Remember last successful turn for follow-ups (scoped to Space)
    _remember(
        session_id,
        {
            "question": question,
            "sql": guard_result.safe_sql,
            "metric_id": metric_id,
            "layer": layer,
            "rows": rows[:50],
            "total_count": total_count if total_count is not None else len(rows),
            "source_table": _infer_source_table(sql),
            "space_id": (space_id or "").strip() or None,
        },
        space_id=space_id,
    )

    # Graduate successful non-session answers into the skill store
    if layer in ("certified", "governed_metric", "query_skill"):
        query_skills.capture(
            question,
            metric_id=metric_id if layer != "certified" else None,
            params=metric_slots,
            sql=guard_result.safe_sql,
            layer=layer,
        )

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
        "metric_id": metric_id,
        "query_plan": _honest_plan(
            question,
            guard_result.safe_sql,
            layer=layer,
            metric_id=metric_id,
            skill_score=skill_score,
            assumptions=assumptions,
        ),
        "audit": {"timestamp": entry.timestamp, "passed": entry.passed, "violations": entry.violations},
    }
