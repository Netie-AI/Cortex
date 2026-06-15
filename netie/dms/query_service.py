"""DMS query routing, SQL generation, and answer synthesis."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Literal

from netie.dms.sql_guardrail import GuardrailResult, audit_log, guard_and_execute, validate_sql
from netie.dms.warehouse_db import DEFAULT_DB, get_connection, load_semantic_layer

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "data" / "samples" / "supplier_contracts"

SQL_KEYWORDS = re.compile(
    r"\b(how many|which|list|total|average|count|below|above|low stock|skus?|inventory|warehouse)\b",
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


def generate_sql(question: str, semantic: dict[str, Any]) -> str:
    """Heuristic SQL generator for demo (LLM would generate in prod)."""
    q = question.lower()
    if "drop" in q and "table" in q:
        return "DROP TABLE inventory"
    if "delete" in q:
        return "DELETE FROM inventory WHERE sku='X'"
    if "password" in q:
        return "SELECT * FROM passwords"

    wh = "WH-A" if "wh-a" in q or "warehouse a" in q else None
    wh_clause = f" AND location = '{wh}'" if wh else ""

    if "below reorder" in q or "low stock" in q or "below reorder level" in q:
        return (
            "SELECT sku, product_name, quantity_kg, reorder_level, location "
            f"FROM inventory WHERE quantity_kg < reorder_level{wh_clause} "
            "ORDER BY quantity_kg ASC"
        )
    if "how many" in q and "sku" in q:
        return "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
    return "SELECT sku, quantity_kg, location, reorder_level FROM inventory ORDER BY sku LIMIT 100"


def build_chart_spec(rows: list[dict], question: str) -> dict[str, Any] | None:
    if not rows:
        return None
    if len(rows) == 1 and len(rows[0]) == 1:
        return None
    keys = list(rows[0].keys())
    if "sku" in keys and "quantity_kg" in keys:
        return {
            "type": "bar",
            "dataKey": "quantity_kg",
            "nameKey": "sku",
            "title": question[:80],
            "data": rows[:20],
        }
    return {
        "type": "bar",
        "dataKey": keys[-1],
        "nameKey": keys[0],
        "title": question[:80],
        "data": rows[:20],
    }


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
        }

    sql = generate_sql(question, semantic)
    con = get_connection(DEFAULT_DB)
    try:
        guard_result, rows, entry = guard_and_execute(sql, semantic, con)
    finally:
        con.close()

    if not guard_result.passed:
        return {
            "answer": "That operation is not permitted.",
            "sql_used": sql,
            "chart_spec": None,
            "audit_id": audit_id,
            "violations_blocked": guard_result.violations,
            "route": "sql",
        }

    if not rows:
        answer = "No rows matched your query."
    else:
        answer = f"Found {len(rows)} row(s). Top result: {rows[0]}"

    return {
        "answer": answer,
        "sql_used": guard_result.safe_sql,
        "chart_spec": build_chart_spec(rows, question),
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "sql",
        "row_count": len(rows),
        "audit": {
            "timestamp": entry.timestamp,
            "passed": entry.passed,
            "violations": entry.violations,
        },
    }


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
