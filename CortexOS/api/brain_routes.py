"""
CortexOS/api/brain_routes.py
DMS Brain API routes — governed AI generative tasks.

All routes follow Ponytail pipeline:
  request → security gate → PII redact → tier route → execute → ledger → response

No from __future__ import annotations (FastAPI rule).
Pydantic models at module level.
"""
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/dms/brain", tags=["brain"])


# ─── Request models ───────────────────────────────────────────────────────────

class BrainRunRequest(BaseModel):
    intent: str
    params: Dict[str, Any] = {}
    actor: str = "user"


class ChartRequest(BaseModel):
    query: str
    data: Dict[str, Any] = {}


class ExportRequest(BaseModel):
    query: str
    table: str = "dms_inventory"
    limit: int = 5000


class EmailRequest(BaseModel):
    request: str
    context: Dict[str, Any] = {}
    actor: str = "user"


class WhatsAppRequest(BaseModel):
    request: str
    context: Dict[str, Any] = {}


class AnalyzeRequest(BaseModel):
    period: str = "last_7_days"


class ReportRequest(BaseModel):
    query: str
    period: str = "last_7_days"


class TaskSuggestRequest(BaseModel):
    use_llm: bool = False
    trigger_text: Optional[str] = None


class TaskChoiceRequest(BaseModel):
    task_id: str
    accepted: bool
    actor: str = "user"
    filled_template: Optional[Dict[str, Any]] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None


class TaskOutcomeRequest(BaseModel):
    task_id: str
    outcome: str  # "success" | "partial" | "failed"
    event_id: Optional[str] = None
    trigger_text: Optional[str] = None
    actor: str = "user"


class PonytailRequest(BaseModel):
    text: str
    user_id: str = "anon"
    intent_hint: str = ""
    force_tier: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_db_data(table: str, limit: int = 5000) -> List[Dict]:
    """Pull data from DuckDB for generative tasks."""
    try:
        import duckdb
        db = duckdb.connect("data/dms_analytics.db", read_only=True)
        df = db.execute(f"SELECT * FROM {table} LIMIT {limit}").df()
        return df.to_dict(orient="records")
    except Exception:
        return []


def _get_warehouse_context() -> Dict[str, Any]:
    """Build warehouse context for AI tasks."""
    ctx: Dict[str, Any] = {}
    for table in ["dms_inventory", "dms_sales", "dms_movements"]:
        try:
            import duckdb
            db = duckdb.connect("data/dms_analytics.db", read_only=True)
            df = db.execute(f"SELECT * FROM {table} LIMIT 200").df()
            ctx[table] = {
                "row_count": len(df),
                "columns": list(df.columns),
                "sample": df.head(3).to_dict(orient="records"),
            }
            if len(df) > 0:
                ctx[table]["stats"] = df.describe(include="all").fillna("").to_dict()
        except Exception:
            pass
    return ctx


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/run")
def brain_run(req: BrainRunRequest):
    """General brain dispatch — any intent."""
    from packs.dms.generative.brain import run
    result = run(req.intent, req.params, actor=req.actor)
    if result.get("error") and not result.get("mock"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.post("/chart")
def brain_chart(req: ChartRequest):
    """Generate a chart config from warehouse data."""
    from packs.dms.generative.brain import generate_chart
    data = req.data or _get_warehouse_context()
    return generate_chart(req.query, data)


@router.post("/export")
def brain_export(req: ExportRequest):
    """Export warehouse table as CSV."""
    from packs.dms.generative.brain import export_csv
    rows = _get_db_data(req.table, req.limit)
    return export_csv(req.query, rows)


@router.post("/email")
def brain_email(req: EmailRequest):
    """Draft a professional email about warehouse operations."""
    from packs.dms.generative.brain import draft_email
    context = req.context or _get_warehouse_context()
    return draft_email(req.request, context)


@router.post("/whatsapp")
def brain_whatsapp(req: WhatsAppRequest):
    """Draft a WhatsApp/messaging app message."""
    from packs.dms.generative.brain import draft_whatsapp
    context = req.context or _get_warehouse_context()
    return draft_whatsapp(req.request, context)


@router.post("/analyze")
def brain_analyze(req: AnalyzeRequest):
    """Analyze warehouse operations for a period."""
    from packs.dms.generative.brain import analyze_sales
    data = _get_warehouse_context()
    return analyze_sales(req.period, data)


@router.post("/auto-analysis")
def brain_auto_analysis():
    """CEO-ready full warehouse executive summary."""
    from packs.dms.generative.brain import auto_analysis
    data = _get_warehouse_context()
    return auto_analysis(data)


@router.post("/report")
def brain_report(req: ReportRequest):
    """Organize data into a formatted markdown report."""
    from packs.dms.generative.brain import organize_report
    data = _get_warehouse_context()
    return organize_report(req.query, data)


# ─── Task Suggest (F4) ────────────────────────────────────────────────────────

@router.post("/suggest")
def task_suggest(req: TaskSuggestRequest):
    """Return ranked task suggestions based on warehouse state."""
    from packs.dms.tasks.suggest import suggest
    import sqlite3

    db_path = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH", "data/dms_ops.db")
    state: Dict[str, Any] = {"items": [], "locations": [], "recent_movements": [], "compliance_flags": []}
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
def task_choice(req: TaskChoiceRequest):
    """Record accept/dismiss for a task suggestion; optional compliance gate."""
    from packs.dms.tasks.suggest import record_choice

    record_choice(req.task_id, req.accepted, req.actor)
    if not req.accepted or not req.filled_template:
        return {"ok": True}

    from packs.dms.tasks.gate import check_task, create_task_event

    event_id = create_task_event(
        message_id=req.message_id,
        thread_id=req.thread_id,
        task_id=req.task_id,
        intent=None,
        filled_template=req.filled_template,
        actor=req.actor,
    )
    verdict = check_task(
        event_id,
        req.task_id,
        req.filled_template,
        actor=req.actor,
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
def task_outcome(req: TaskOutcomeRequest):
    """Record outcome of an accepted task; optionally capture skill on success."""
    from packs.dms.tasks.suggest import record_outcome

    record_outcome(req.task_id, req.outcome)
    captured = None
    if req.event_id and req.trigger_text:
        from packs.dms.skills.capture import capture_from_event

        captured = capture_from_event(
            req.event_id,
            trigger_text=req.trigger_text,
            outcome=req.outcome,
            actor=req.actor,
        )
    return {"ok": True, "captured": captured}


@router.post("/suggest/refresh-stats")
def suggest_refresh():
    """Trigger nightly batch stats refresh manually."""
    from packs.dms.tasks.learn import refresh_stats
    return refresh_stats()


# ─── Ponytail ─────────────────────────────────────────────────────────────────

@router.post("/ponytail")
def ponytail_route(req: PonytailRequest):
    """Run the Ponytail pipeline on raw text."""
    from CortexOS.ponytail.middleware import ponytail_process
    return ponytail_process(
        req.text,
        user_id=req.user_id,
        intent_hint=req.intent_hint,
        force_tier=req.force_tier,
    )


def register_brain_routes(app) -> None:
    """Mount /dms/brain/* routes on a FastAPI app."""
    app.include_router(router)
