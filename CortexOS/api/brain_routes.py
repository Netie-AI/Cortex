"""
CortexOS/api/brain_routes.py
DMS Brain API routes — governed AI generative tasks.

All routes follow Ponytail pipeline:
  request → security gate → PII redact → tier route → execute → ledger → response

No from __future__ import annotations (FastAPI rule).
Pydantic models at module level.
"""
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/brain", tags=["brain"])


# ─── Request models ───────────────────────────────────────────────────────────

class BrainRunRequest(BaseModel):
    intent: str
    params: dict[str, Any] = {}
    actor: str = "user"


class ChartRequest(BaseModel):
    query: str
    data: dict[str, Any] = {}


class ExportRequest(BaseModel):
    query: str
    table: str = "inventory"
    limit: int = 5000
    format: str = "csv"  # csv | parquet


class EmailRequest(BaseModel):
    request: str
    context: dict[str, Any] = {}
    actor: str = "user"


class WhatsAppRequest(BaseModel):
    request: str
    context: dict[str, Any] = {}


class AnalyzeRequest(BaseModel):
    period: str = "last_7_days"


class ReportRequest(BaseModel):
    query: str
    period: str = "last_7_days"


class TaskSuggestRequest(BaseModel):
    use_llm: bool = False
    trigger_text: str | None = None


class TaskChoiceRequest(BaseModel):
    task_id: str
    accepted: bool
    actor: str = "user"
    filled_template: dict[str, Any] | None = None
    message_id: str | None = None
    thread_id: str | None = None


class TaskOutcomeRequest(BaseModel):
    task_id: str
    outcome: str  # "success" | "partial" | "failed"


class PonytailRequest(BaseModel):
    text: str
    user_id: str = "anon"
    intent_hint: str = ""
    force_tier: str | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

_TABLE_ALIASES = {
    "dms_inventory": "inventory",
    "dms_transactions": "transactions",
    "dms_suppliers": "suppliers",
    "dms_locations": "locations",
    "dms_shipments": "shipments",
    "dms_alerts": "alerts",
}


def _resolve_export_table(table: str) -> str:
    from CortexOS.dms.warehouse_db import KNOWN_TABLES

    name = _TABLE_ALIASES.get((table or "").strip().lower(), (table or "").strip().lower())
    if name not in KNOWN_TABLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown table {table!r}. Allowed: {', '.join(KNOWN_TABLES)}",
        )
    return name


def _get_db_data(table: str, limit: int = 5000) -> list[dict]:
    """Pull allowlisted warehouse rows via the C4 connection helper (read-only)."""
    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
    )

    name = _resolve_export_table(table)
    lim = max(1, min(int(limit or 5000), 50_000))
    con = get_connection(DEFAULT_DB, read_only=True)  # export reads; it never writes back
    try:
        rel = con.execute(f"SELECT * FROM {name} LIMIT {lim}")
        cols = [d[0] for d in rel.description]
        # strict=True: DuckDB guarantees description width matches row width, so a
        # mismatch is a bug worth raising rather than silently truncating a column
        # out of an export the customer receives.
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()


def _export_parquet_snapshot(table: str, limit: int = 5000) -> dict[str, Any]:
    """Write one isolated Parquet file (Power BI-safe — never the DuckLake folder)."""
    from datetime import datetime, timezone
    from pathlib import Path

    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
    )

    name = _resolve_export_table(table)
    lim = max(1, min(int(limit or 5000), 50_000))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = Path(os.environ.get("DMS_EXPORT_DIR") or (Path(DEFAULT_DB).resolve().parent / "exports"))
    out_dir = root / f"export_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.parquet"

    # COPY ... TO writes a Parquet file on disk, not the database, so this stays
    # a reader as far as DuckDB's lock is concerned.
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        # Identifier already allowlisted; limit is int-bounded.
        con.execute(
            f"COPY (SELECT * FROM {name} LIMIT {lim}) TO ? (FORMAT PARQUET)",
            [str(out_path)],
        )
        row = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        total = int(row[0]) if row else 0
    finally:
        con.close()

    exported = min(total, lim)
    return {
        "filename": out_path.name,
        "path": str(out_path),
        "directory": str(out_dir),
        "row_count": exported,
        "table": name,
        "format": "parquet",
        "summary": (
            f"Isolated Parquet snapshot of {name} ({exported} rows). "
            "Point Power BI at this file — not the DuckLake folder."
        ),
        "requires_confirm": False,
    }


def _get_warehouse_context() -> dict[str, Any]:
    """Build warehouse context for AI tasks from allowlisted row counts only."""
    try:
        from CortexOS.dms.warehouse_db import table_row_counts

        return {"row_counts": table_row_counts()}
    except Exception:  # noqa: BLE001
        return {}


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/run")
def brain_run(req: BrainRunRequest, caller: Caller = Depends(require_role("steward"))):
    """General brain dispatch — any intent."""
    from packs.dms.generative.brain import run
    actor = caller.actor
    result = run(req.intent, req.params, actor=actor)
    if result.get("error") and not result.get("mock"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.post("/chart")
def brain_chart(req: ChartRequest, caller: Caller = Depends(require_role("viewer"))):
    """Generate a chart config from warehouse data."""
    from packs.dms.generative.brain import generate_chart
    _ = caller
    data = req.data or _get_warehouse_context()
    return generate_chart(req.query, data)


@router.post("/export")
def brain_export(req: ExportRequest, caller: Caller = Depends(require_role("steward"))):
    """Export an allowlisted warehouse table as CSV or an isolated Parquet snapshot."""
    from packs.dms.generative.brain import export_csv

    _ = caller
    fmt = (req.format or "csv").strip().lower()
    if fmt == "parquet":
        return _export_parquet_snapshot(req.table, req.limit)
    if fmt != "csv":
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'parquet'")
    rows = _get_db_data(req.table, req.limit)
    return export_csv(req.query, rows)


@router.post("/email")
def brain_email(req: EmailRequest, caller: Caller = Depends(require_role("steward"))):
    """Draft a professional email about warehouse operations."""
    from packs.dms.generative.brain import draft_email
    _ = caller
    context = req.context or _get_warehouse_context()
    return draft_email(req.request, context)


@router.post("/whatsapp")
def brain_whatsapp(req: WhatsAppRequest, caller: Caller = Depends(require_role("steward"))):
    """Draft a WhatsApp/messaging app message."""
    from packs.dms.generative.brain import draft_whatsapp
    _ = caller
    context = req.context or _get_warehouse_context()
    return draft_whatsapp(req.request, context)


@router.post("/analyze")
def brain_analyze(req: AnalyzeRequest, caller: Caller = Depends(require_role("viewer"))):
    """Analyze warehouse operations for a period."""
    from packs.dms.generative.brain import analyze_sales
    _ = caller
    data = _get_warehouse_context()
    return analyze_sales(req.period, data)


@router.post("/auto-analysis")
def brain_auto_analysis(caller: Caller = Depends(require_role("viewer"))):
    """CEO-ready full warehouse executive summary."""
    from packs.dms.generative.brain import auto_analysis
    _ = caller
    data = _get_warehouse_context()
    return auto_analysis(data)


@router.post("/report")
def brain_report(req: ReportRequest, caller: Caller = Depends(require_role("viewer"))):
    """Organize data into a formatted markdown report."""
    from packs.dms.generative.brain import organize_report
    _ = caller
    data = _get_warehouse_context()
    return organize_report(req.query, data)


# ─── Task Suggest (F4) ────────────────────────────────────────────────────────

@router.post("/suggest")
def task_suggest(req: TaskSuggestRequest, caller: Caller = Depends(require_role("viewer"))):
    """Return ranked task suggestions based on warehouse state."""
    import sqlite3

    from packs.dms.tasks.suggest import suggest

    _ = caller
    db_path = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH", "data/dms_ops.db")
    state: dict[str, Any] = {"items": [], "locations": [], "recent_movements": [], "compliance_flags": []}
    try:
        with sqlite3.connect(db_path) as conn:
            # Items
            rows = conn.execute(
                "SELECT id, sku, location_id, updated_at FROM dms_items LIMIT 200"
            ).fetchall()
            state["items"] = [
                {"id": r[0], "sku": r[1], "location_id": r[2], "days_since_movement": 0}
                for r in rows
            ]
            # Locations
            rows = conn.execute(
                "SELECT id, capacity FROM dms_locations LIMIT 100"
            ).fetchall()
            state["locations"] = [{"id": r[0], "capacity": r[1] or 100, "occupied": 0} for r in rows]
            # Recent movements
            rows = conn.execute(
                "SELECT id, item_id, to_location, ts FROM dms_movements ORDER BY id DESC LIMIT 50"
            ).fetchall()
            state["recent_movements"] = [
                {"id": r[0], "item_id": r[1], "to_location": r[2], "minutes_ago": 5}
                for r in rows
            ]
    except Exception:
        pass

    return {"suggestions": suggest(state, use_llm=req.use_llm, trigger_text=req.trigger_text)}


@router.post("/suggest/choice")
def task_choice(req: TaskChoiceRequest, caller: Caller = Depends(require_role("steward"))):
    """Record accept/dismiss for a task suggestion; optional compliance gate."""
    from packs.dms.tasks.suggest import record_choice

    actor = caller.actor
    record_choice(req.task_id, req.accepted, actor)
    if not req.accepted or not req.filled_template:
        return {"ok": True}

    from packs.dms.tasks.gate import check_task, create_task_event

    event_id = create_task_event(
        message_id=req.message_id,
        thread_id=req.thread_id,
        task_id=req.task_id,
        intent=None,
        filled_template=req.filled_template,
        actor=actor,
    )
    verdict = check_task(
        event_id,
        req.task_id,
        req.filled_template,
        actor=actor,
    )
    return {
        "ok": True,
        "event_id": event_id,
        "verdict": {
            "status": verdict.status,
            "violations": verdict.violations,
            "executable": verdict.executable,
        },
    }


@router.post("/suggest/outcome")
def task_outcome(req: TaskOutcomeRequest, caller: Caller = Depends(require_role("steward"))):
    """Record outcome of an accepted task."""
    from packs.dms.tasks.suggest import record_outcome
    _ = caller
    record_outcome(req.task_id, req.outcome)
    return {"ok": True}


@router.post("/suggest/refresh-stats")
def suggest_refresh(caller: Caller = Depends(require_role("admin"))):
    """Trigger nightly batch stats refresh manually."""
    from packs.dms.tasks.learn import refresh_stats
    _ = caller
    return refresh_stats()


# ─── Ponytail ─────────────────────────────────────────────────────────────────

@router.post("/ponytail")
def ponytail_route(req: PonytailRequest, caller: Caller = Depends(require_role("viewer"))):
    """Run the Ponytail pipeline on raw text."""
    from CortexOS.ponytail.middleware import ponytail_process
    _ = caller
    return ponytail_process(
        req.text,
        user_id=req.user_id,
        intent_hint=req.intent_hint,
        force_tier=req.force_tier,
    )


def register_brain_routes(app) -> None:
    """Mount /dms/brain/* routes on a FastAPI app."""
    app.include_router(router)
