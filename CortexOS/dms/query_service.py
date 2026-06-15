"""DMS query routing, SQL generation, and answer synthesis."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from CortexOS.dms.sql_guardrail import audit_log, guard_and_execute, validate_sql
from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection, load_semantic_layer

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "data" / "samples" / "supplier_contracts"
CHANGELOG_PATH = ROOT / "data" / "samples" / "inventory_changelog.jsonl"

SQL_KEYWORDS = re.compile(
    r"\b(how many|which|list|total|average|count|below|above|low stock|skus?|inventory|"
    r"warehouse|supplier|shipment|location|cctv|capacity|expired|alert|risk|delayed|transit|cold)\b",
    re.I,
)
RAG_KEYWORDS = re.compile(
    r"\b(what does|explain|according to|contract|supplier agreement|terms)\b",
    re.I,
)
DESTRUCTIVE = re.compile(r"\b(drop|delete|truncate|alter|insert|update|create)\b", re.I)


def route_question(question: str) -> Literal["sql", "rag", "blocked"]:
    q = question.strip()
    if DESTRUCTIVE.search(q):
        return "blocked"
    if RAG_KEYWORDS.search(q):
        return "rag"
    if SQL_KEYWORDS.search(q):
        return "sql"
    return "sql"


def _detect_warehouse_code(question: str) -> str | None:
    q = question.lower()
    for letter in "abcdefghijklmnopqrst":
        code = f"WH-{letter.upper()}"
        if code.lower() in q or f"warehouse {letter}" in q:
            return code
    if "shah alam" in q:
        return "WH-A"
    if "penang" in q:
        return "WH-B"
    if "johor" in q:
        return "WH-C"
    return None


def _wh_location_clause(alias: str, question: str) -> str:
    wh = _detect_warehouse_code(question)
    if not wh:
        return ""
    return f" AND {alias}.location_code = '{wh}'"


def generate_sql(question: str, semantic: dict[str, Any]) -> str:
    """Heuristic SQL generator for demo (LLM would generate in prod)."""
    q = question.lower()
    if "drop" in q and "table" in q:
        return "DROP TABLE inventory"
    if "delete" in q:
        return "DELETE FROM inventory WHERE sku='X'"
    if "password" in q:
        return "SELECT * FROM passwords"

    wh_clause_inv = ""
    wh = _detect_warehouse_code(question)
    if wh:
        wh_clause_inv = f" AND l.location_code = '{wh}'"

    if "capacity" in q and "warehouse" in q:
        return (
            "SELECT l.location_code, l.location_name, l.current_load_kg, l.capacity_kg, "
            "ROUND(100.0 * l.current_load_kg / l.capacity_kg, 1) AS pct_used "
            "FROM locations l ORDER BY pct_used DESC"
        )

    if "above 90" in q and "capacity" in q:
        return (
            "SELECT l.location_code, l.location_name, l.current_load_kg, l.capacity_kg, "
            "ROUND(100.0 * l.current_load_kg / l.capacity_kg, 1) AS pct_used "
            "FROM locations l "
            "WHERE 100.0 * l.current_load_kg / l.capacity_kg > 90 "
            "ORDER BY pct_used DESC"
        )

    if "cold storage" in q:
        return (
            "SELECT location_code, location_name, city, is_cold_storage "
            "FROM locations WHERE is_cold_storage = true ORDER BY location_code"
        )

    if "active alert" in q or ("alert" in q and "warehouse" in q):
        return (
            "SELECT a.alert_id, a.alert_type, a.severity, l.location_code, a.message "
            "FROM alerts a "
            "LEFT JOIN locations l ON a.related_location = l.location_id "
            "WHERE a.resolved = false ORDER BY a.severity DESC, a.created_at DESC"
        )

    if "delayed" in q and "shipment" in q:
        return (
            "SELECT shipment_id, sku, status, carrier, expected_arrival, quantity_kg "
            "FROM shipments WHERE status = 'DELAYED' ORDER BY expected_arrival ASC"
        )

    if "in transit" in q or ("shipment" in q and "transit" in q):
        return (
            "SELECT shipment_id, sku, carrier, expected_arrival, quantity_kg, destination_location_id "
            "FROM shipments WHERE status = 'IN_TRANSIT' ORDER BY expected_arrival ASC"
        )

    if "arriving this week" in q:
        end = (date.today() + timedelta(days=7)).isoformat()
        return (
            "SELECT shipment_id, sku, expected_arrival, carrier, quantity_kg "
            f"FROM shipments WHERE status = 'IN_TRANSIT' AND expected_arrival <= '{end}' "
            "ORDER BY expected_arrival ASC"
        )

    if "carrier" in q and "delayed" in q:
        return (
            "SELECT carrier, COUNT(*) AS delayed_count "
            "FROM shipments WHERE status = 'DELAYED' GROUP BY carrier ORDER BY delayed_count DESC"
        )

    if "cost" in q and "destination" in q:
        return (
            "SELECT l.location_code, SUM(s.cost_myr) AS total_cost_myr "
            "FROM shipments s JOIN locations l ON s.destination_location_id = l.location_id "
            "GROUP BY l.location_code ORDER BY total_cost_myr DESC"
        )

    if "risk score" in q and ("above" in q or "0.7" in q):
        return (
            "SELECT supplier_id, supplier_name, risk_score, country, lead_time_days "
            "FROM suppliers WHERE risk_score > 0.7 ORDER BY risk_score DESC"
        )

    if "audit" in q and "overdue" in q:
        cutoff = (date.today() - timedelta(days=90)).isoformat()
        return (
            "SELECT supplier_id, supplier_name, last_audit_date, risk_score "
            f"FROM suppliers WHERE last_audit_date < '{cutoff}' ORDER BY last_audit_date ASC"
        )

    if "spend" in q and "country" in q:
        return (
            "SELECT s.country, SUM(i.quantity_kg * i.unit_cost_myr) AS total_spend_myr "
            "FROM inventory i JOIN suppliers s ON i.supplier_id = s.supplier_id "
            "GROUP BY s.country ORDER BY total_spend_myr DESC"
        )

    if "longest lead time" in q or ("lead time" in q and "supplier" in q and "average" not in q):
        return (
            "SELECT supplier_name, lead_time_days, country, payment_terms "
            "FROM suppliers ORDER BY lead_time_days DESC LIMIT 10"
        )

    if "average lead time" in q or ("lead time" in q and "supplier" in q):
        return (
            "SELECT supplier_name, lead_time_days, country "
            "FROM suppliers ORDER BY lead_time_days ASC"
        )

    if "high-risk" in q and "pending" in q:
        return (
            "SELECT DISTINCT s.supplier_name, s.risk_score, sh.shipment_id, sh.status "
            "FROM suppliers s "
            "JOIN shipments sh ON s.supplier_id = sh.supplier_id "
            "WHERE s.risk_score > 0.7 AND sh.status IN ('PENDING', 'IN_TRANSIT') "
            "ORDER BY s.risk_score DESC"
        )

    if "expired" in q:
        today = date.today().isoformat()
        return (
            "SELECT sku, sku_name, expiry_date, quantity_kg, location_id "
            f"FROM inventory WHERE expiry_date IS NOT NULL AND expiry_date < '{today}' "
            "ORDER BY expiry_date ASC"
        )

    if "stock value" in q and "category" in q:
        return (
            "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
            "FROM inventory GROUP BY category ORDER BY total_value_myr DESC"
        )

    if "restocked" in q and "30 days" in q:
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        return (
            "SELECT sku, sku_name, last_restocked, quantity_kg, location_id "
            f"FROM inventory WHERE last_restocked < '{cutoff}' ORDER BY last_restocked ASC"
        )

    if "chemicals" in q and "inventory" in q:
        return (
            "SELECT sku, sku_name, quantity_kg, location_id, storage_bin "
            "FROM inventory WHERE category = 'CHEMICALS' ORDER BY sku"
        )

    if "delayed incoming" in q and "warehouse" in q:
        return (
            "SELECT l.location_code, COUNT(*) AS delayed_incoming "
            "FROM shipments s "
            "JOIN locations l ON s.destination_location_id = l.location_id "
            "WHERE s.status = 'DELAYED' "
            "GROUP BY l.location_code ORDER BY delayed_incoming DESC"
        )

    if "cctv" in q or ("camera" in q and "warehouse" in q):
        loc = wh or "WH-A"
        return (
            "SELECT location_code, location_name, cctv_camera_id, latitude, longitude "
            f"FROM locations WHERE location_code = '{loc}'"
        )

    if "below reorder" in q or "low stock" in q or "below reorder level" in q:
        return (
            "SELECT i.sku, i.sku_name, i.quantity_kg, i.reorder_level_kg, "
            "l.location_code, i.storage_bin, i.category "
            "FROM inventory i "
            "JOIN locations l ON i.location_id = l.location_id "
            f"WHERE i.quantity_kg < i.reorder_level_kg AND i.reorder_level_kg > 0{wh_clause_inv} "
            "ORDER BY i.quantity_kg ASC"
        )

    if "how many" in q and "sku" in q:
        return "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"

    if "by category" in q or "per category" in q:
        return (
            "SELECT category, COUNT(*) AS sku_count, SUM(quantity_kg) AS total_kg "
            "FROM inventory GROUP BY category ORDER BY total_kg DESC"
        )

    return (
        "SELECT sku, quantity_kg, location_id, reorder_level_kg, category "
        "FROM inventory ORDER BY sku LIMIT 100"
    )


def _format_low_stock_answer(rows: list[dict], wh: str | None) -> str:
    loc = f" in {wh}" if wh else ""
    lines = [f"{len(rows)} SKU(s) below reorder level{loc}:"]
    for row in rows[:8]:
        sku = row.get("sku", "?")
        qty = row.get("quantity_kg", "?")
        reorder = row.get("reorder_level_kg", row.get("reorder_level", "?"))
        name = row.get("sku_name") or row.get("category") or ""
        bin_id = row.get("storage_bin", "")
        wh_code = row.get("location_code", "")
        detail = f"  · {sku}"
        if name:
            detail += f" ({name})"
        detail += f" — {qty} kg on hand vs {reorder} kg reorder"
        if wh_code:
            detail += f" @ {wh_code}"
        if bin_id:
            detail += f"-{bin_id}"
        lines.append(detail)
    if len(rows) > 8:
        lines.append(f"  · …and {len(rows) - 8} more (see chart)")
    return "\n".join(lines)


def _format_capacity_answer(rows: list[dict]) -> str:
    if not rows:
        return "No warehouse capacity data available."
    over90 = [r for r in rows if float(r.get("pct_used", 0) or 0) > 90]
    lines = [
        f"{len(over90)} of {len(rows)} warehouses are above 90% capacity.",
        "Top utilisation:",
    ]
    for row in rows[:5]:
        lines.append(
            f"  · {row.get('location_code')}: {row.get('pct_used')}% "
            f"({row.get('current_load_kg')} / {row.get('capacity_kg')} kg)"
        )
    return "\n".join(lines)


def _format_generic_answer(rows: list[dict], question: str) -> str:
    del question
    if not rows:
        return "No rows matched your query."
    if len(rows) == 1 and len(rows[0]) == 1:
        key, val = next(iter(rows[0].items()))
        return f"Result: {key} = {val}"
    preview = rows[:5]
    lines = [f"Found {len(rows)} row(s)."]
    for row in preview:
        parts = [f"{k}={v}" for k, v in list(row.items())[:5]]
        lines.append("  · " + ", ".join(parts))
    if len(rows) > 5:
        lines.append(f"  · …and {len(rows) - 5} more rows")
    return "\n".join(lines)


def synthesize_answer(rows: list[dict], question: str) -> str:
    q = question.lower()
    if not rows:
        if "below reorder" in q or "low stock" in q:
            wh = _detect_warehouse_code(question)
            hint = f" in {wh}" if wh else ""
            return f"No SKUs below reorder level{hint}. All monitored stock is above threshold."
        return "No rows matched your query."

    if "below reorder" in q or "low stock" in q:
        return _format_low_stock_answer(rows, _detect_warehouse_code(question))

    if "capacity" in q:
        return _format_capacity_answer(rows)

    return _format_generic_answer(rows, question)


def _is_time_dimension(name: str) -> bool:
    n = name.lower()
    return any(t in n for t in ("date", "time", "arrival", "restocked", "timestamp", "created_at"))


def build_chart_spec(rows: list[dict], question: str) -> dict[str, Any] | None:
    if not rows:
        return None

    q = question.lower()

    if len(rows) == 1 and len(rows[0]) == 1:
        key, val = next(iter(rows[0].items()))
        return {
            "type": "bignum",
            "value": val,
            "label": key.replace("_", " ").upper(),
            "title": question[:80],
            "data": [],
        }

    if ("below reorder" in q or "low stock" in q) and len(rows) <= 20:
        wh = _detect_warehouse_code(question) or "ALL"
        data = [
            {"name": r.get("sku", "?"), "value": float(r.get("quantity_kg") or 0)}
            for r in rows[:10]
        ]
        return {
            "type": "bar",
            "data": data,
            "x_label": "sku",
            "y_label": "quantity_kg",
            "title": f"Low Stock Items — {wh}",
        }

    keys = list(rows[0].keys())
    name_key = None
    value_key = None

    for candidate in ("location_code", "sku", "sku_name", "category", "carrier", "supplier_name", "country"):
        if candidate in keys:
            name_key = candidate
            break
    if not name_key:
        name_key = keys[0]

    for candidate in ("pct_used", "utilisation_pct", "quantity_kg", "total_value_myr", "total_cost_myr", "delayed_count", "total_spend_myr", "sku_count", "total_kg", "lead_time_days", "risk_score"):
        if candidate in keys:
            value_key = candidate
            break
    if not value_key:
        value_key = keys[-1]

    chart_type = "line" if _is_time_dimension(name_key) else "bar"
    slice_rows = rows[:20] if "capacity" in q else rows[:10]
    data = [{"name": str(r.get(name_key, "")), "value": float(r.get(value_key) or 0)} for r in slice_rows]

    title = question[:80]
    if "capacity" in q:
        title = "Warehouse Capacity Utilisation"

    return {
        "type": chart_type,
        "data": data,
        "x_label": name_key,
        "y_label": value_key,
        "title": title,
        "more_count": max(0, len(rows) - len(slice_rows)),
    }


def get_alerts_summary(db_path: Path | None = None) -> dict[str, int]:
    con = get_connection(db_path or DEFAULT_DB)
    try:
        crit = con.execute(
            "SELECT COUNT(*) FROM alerts WHERE resolved = false AND severity = 'CRITICAL'"
        ).fetchone()[0]
        high = con.execute(
            "SELECT COUNT(*) FROM alerts WHERE resolved = false AND severity = 'HIGH'"
        ).fetchone()[0]
        return {"critical": int(crit), "high": int(high)}
    except Exception:
        return {"critical": 0, "high": 0}
    finally:
        con.close()


def rag_answer(question: str) -> tuple[str, list[str]]:
    sources: list[str] = []
    snippets: list[str] = []
    if CONTRACTS_DIR.is_dir():
        for path in sorted(CONTRACTS_DIR.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            sources.append(path.name)
            if any(w in text.lower() for w in question.lower().split()[:3]):
                snippets.append(text[:400])
    if not snippets and CONTRACTS_DIR.is_dir():
        for path in sorted(CONTRACTS_DIR.glob("*.txt")):
            snippets.append(path.read_text(encoding="utf-8")[:300])
            sources.append(path.name)
            break
    answer = snippets[0][:500] if snippets else "No supplier contract documents indexed."
    return answer, sources


def answer_question(question: str, *, session_id: str | None = None) -> dict[str, Any]:
    del session_id
    audit_id = str(uuid.uuid4())
    route = route_question(question)
    semantic = load_semantic_layer()

    if route == "blocked":
        sql = generate_sql(question, semantic)
        result = validate_sql(sql, semantic)
        return {
            "answer": "That operation is not permitted.",
            "sql_used": None,
            "chart_spec": None,
            "audit_id": audit_id,
            "violations_blocked": result.violations or ["DDL_ATTEMPT"],
            "route": "blocked",
            "rows": [],
            "source_table": None,
        }

    if route == "rag":
        answer, sources = rag_answer(question)
        return {
            "answer": answer,
            "sql_used": None,
            "chart_spec": None,
            "audit_id": audit_id,
            "violations_blocked": [],
            "route": "rag",
            "sources": sources,
            "rows": [],
            "source_table": None,
        }

    sql = generate_sql(question, semantic)
    con = get_connection(DEFAULT_DB)
    try:
        guard_result, rows, entry = guard_and_execute(sql, semantic, con)
    finally:
        con.close()

    alerts_summary = get_alerts_summary()
    show_alerts = any(w in question.lower() for w in ("location", "inventory", "warehouse", "capacity", "alert"))

    if not guard_result.passed:
        return {
            "answer": "That operation is not permitted.",
            "sql_used": sql,
            "chart_spec": None,
            "audit_id": audit_id,
            "violations_blocked": guard_result.violations,
            "route": "sql",
            "rows": [],
            "source_table": _infer_source_table(sql),
            "alerts_summary": alerts_summary if show_alerts else None,
        }

    answer = synthesize_answer(rows, question)
    chart = build_chart_spec(rows, question)

    if ("below reorder" in question.lower() or "low stock" in question.lower()) and rows:
        wh = _detect_warehouse_code(question) or "ALL"
        chart = {
            "type": "bignum",
            "value": len(rows),
            "label": f"SKUS BELOW REORDER LEVEL AT {wh}",
            "title": f"Low Stock — {wh}",
            "data": [
                {"name": r.get("sku", "?"), "value": float(r.get("quantity_kg") or 0)}
                for r in rows[:10]
            ],
        }

    return {
        "answer": answer,
        "sql_used": guard_result.safe_sql,
        "chart_spec": chart,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "sql",
        "row_count": len(rows),
        "rows": rows,
        "source_table": _infer_source_table(sql),
        "alerts_summary": alerts_summary if show_alerts else None,
        "audit": {
            "timestamp": entry.timestamp,
            "passed": entry.passed,
            "violations": entry.violations,
        },
    }


def _infer_source_table(sql: str) -> str | None:
    sql_l = sql.lower()
    for table in ("inventory", "shipments", "suppliers", "locations", "transactions", "alerts"):
        if f"from {table}" in sql_l or f"join {table}" in sql_l:
            return table
    return "inventory"


def propose_edits(changes: list[dict[str, Any]], *, approved_by: str) -> dict[str, Any]:
    proposed_id = str(uuid.uuid4())
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    for i, ch in enumerate(changes):
        entry = {
            "proposed_id": proposed_id,
            "table": ch.get("table", "query_result"),
            "row_id": ch.get("row_id", i),
            "col": ch.get("field", ch.get("col")),
            "old_value": ch.get("old_val", ch.get("old_value")),
            "new_value": ch.get("new_val", ch.get("new_value")),
            "rule_id": "propose_edit",
            "approved_by": approved_by,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with CHANGELOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return {"proposed_id": proposed_id, "count": len(changes)}


def list_audit_entries() -> list[dict[str, Any]]:
    return [
        {
            "timestamp": e.timestamp,
            "original_sql": e.original_sql,
            "safe_sql": e.safe_sql,
            "violations": e.violations,
            "passed": e.passed,
            "row_count": e.row_count,
        }
        for e in audit_log()
    ]
