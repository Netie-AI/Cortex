"""DMS Brain API routes (active when PACK=dms)."""


import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"
INVENTORY_MESSY = SAMPLES / "inventory_messy.csv"
INVENTORY_CLEAN = SAMPLES / "inventory_clean.csv"
CHANGELOG_PATH = SAMPLES / "inventory_changelog.jsonl"


class DMSQueryRequest(BaseModel):
    question: str
    session_id: str = Field(default="demo")
    space_id: str | None = None


class DMSQueryResponse(BaseModel):
    answer: str
    sql_used: str | None = None
    chart_spec: dict[str, Any] | None = None
    audit_id: str
    violations_blocked: list[str] = Field(default_factory=list)
    route: str | None = None
    sources: list[str] | None = None
    row_count: int | None = None
    rows: list[dict[str, Any]] | None = None
    source_table: str | None = None
    alerts_summary: dict[str, int] | None = None
    audit: dict[str, Any] | None = None
    query_plan: dict[str, Any] | None = None
    # Provenance — engine already computes these; keep them on the wire for Studio/UI.
    layer: str | None = None
    badge: str | None = None
    metric_id: str | None = None
    total_count: int | None = None
    truncated: bool | None = None
    assumptions: str | None = None
    suggestions: list[str] | None = None
    query_source: str | None = None
    grant_kind: str | None = None
    granted_sources: list[str] | None = None


class AnalyseEntryRequest(BaseModel):
    raw_text: str


# #263: who approved a write is the authenticated caller. ``approved_by`` stays
# on the wire unchanged (it is in the frozen contract spec's components, so
# removing it is a contract major) and its value is never read.
class AddEntryRequest(BaseModel):
    proposed: dict[str, Any]
    approved_by: str = "demo_steward"  # wire only (#263): ignored; the actor is the authenticated caller


class ProposeEditRequest(BaseModel):
    changes: list[dict[str, Any]]
    approved_by: str = "demo_steward"  # wire only (#263): ignored; the actor is the authenticated caller


def _read_csv_rows(path: Path, limit: int) -> tuple[list[dict[str, str]], int]:
    import csv

    if not path.is_file():
        return [], 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        all_rows = list(csv.DictReader(f))
    total = len(all_rows)
    return all_rows[:limit], total


def _load_changelog(limit: int = 200) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not CHANGELOG_PATH.is_file():
        return entries
    for line in CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()[:limit]:
        if line.strip():
            entries.append(json.loads(line))
    return entries


def _missing_dataset_response() -> dict[str, Any]:
    return {"error": "Run demo setup first", "rows": []}


def register_dms_routes(app: Any, *, query_auth: Any = None) -> None:
    """Register the DMS brain routes. Every route needs a credential (#263).

    ``query_auth`` gates ``POST /dms/query`` only. ``create_app`` passes the
    DMS pack's viewer dependency, which still accepts the published demo
    viewer key that DMS sends by default. Without it the route uses the engine
    port, which refuses that key: the fallback is the stricter gate, never an
    open one. Every other route here is on the engine port.
    """
    from fastapi import Depends, HTTPException, Query

    from CortexOS.security.auth_port import Principal, require_role

    viewer = [Depends(require_role("viewer"))]
    query_gate = [Depends(query_auth if query_auth is not None else require_role("viewer"))]
    steward = require_role("steward")

    @app.post("/dms/query", response_model=DMSQueryResponse, dependencies=query_gate)
    async def dms_query(body: DMSQueryRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.query_service import answer_question

        out = answer_question(
            body.question,
            session_id=body.session_id,
            space_id=body.space_id,
            require_grounding=True,
        )
        # Mark when warehouse tables were synced from lake.silver (medallion path).
        if out.get("query_source") is None:
            out["query_source"] = os.environ.get(
                "DMS_QUERY_SOURCE_LABEL", "warehouse_synced_from_lake_silver"
            )
        return out

    @app.get("/dms/audit", dependencies=viewer)
    async def dms_audit() -> list[dict[str, Any]]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            return []
        from CortexOS.dms.query_service import list_audit_entries

        return list_audit_entries()

    @app.get("/dms/tables", dependencies=viewer)
    async def dms_tables() -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            return {"tables": []}
        from CortexOS.dms.warehouse_db import KNOWN_TABLES, table_row_counts

        counts = table_row_counts()
        tables = []
        for name in KNOWN_TABLES:
            clean_path = SAMPLES / f"{name}_clean.csv"
            col_count = 0
            if clean_path.is_file():
                import csv

                with clean_path.open(encoding="utf-8-sig") as f:
                    col_count = len(next(csv.reader(f)))
            tables.append(
                {
                    "name": name,
                    "row_count": counts.get(name, 0),
                    "column_count": max(0, col_count - 1),
                }
            )
        return {"tables": tables, "total_rows": sum(counts.values())}

    @app.get("/dms/table-preview", dependencies=viewer)
    async def dms_table_preview(
        table: str = Query(...),
        from_row: int = Query(0, alias="from"),
        to_row: int = Query(100, alias="to"),
        cols: str = Query(""),
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.warehouse_db import preview_table

        col_list = [c.strip() for c in cols.split(",") if c.strip()] or None
        try:
            rows, total, all_cols = preview_table(
                table, from_row=from_row, to_row=to_row, cols=col_list
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "table": table,
            "rows": rows,
            "total_count": total,
            "columns": all_cols,
        }

    @app.post("/dms/propose-edit")
    async def dms_propose_edit(
        body: ProposeEditRequest, caller: Principal = Depends(steward)
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.query_service import propose_edits

        return propose_edits(body.changes, approved_by=caller.actor)

    @app.get("/dms/data/{variant}", dependencies=viewer)
    async def dms_data(variant: str, limit: int = 50) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            return _missing_dataset_response()

        if variant == "messy":
            path = INVENTORY_MESSY
        elif variant == "clean":
            path = INVENTORY_CLEAN
        else:
            return {"error": f"Unknown variant: {variant}", "rows": []}

        if not path.is_file():
            return _missing_dataset_response()

        rows, total = _read_csv_rows(path, limit)
        payload: dict[str, Any] = {
            "variant": variant,
            "rows": rows,
            "count": len(rows),
            "total_count": total,
        }

        if variant == "messy":
            from CortexOS.dms.profiler import profile_dataset

            payload["profile"] = profile_dataset(path).to_dict()
        else:
            payload["changelog"] = _load_changelog()

        return payload

    @app.get("/dms/changelog", dependencies=viewer)
    async def dms_changelog(limit: int = 100) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            return {"entries": []}
        return {"entries": _load_changelog(limit)}

    @app.post("/dms/analyse-entry", dependencies=viewer)
    async def dms_analyse_entry(body: AnalyseEntryRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.entry_analyser import analyse_raw_entry

        return analyse_raw_entry(body.raw_text)

    @app.post("/dms/add-entry")
    async def dms_add_entry(
        body: AddEntryRequest, caller: Principal = Depends(steward)
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.entry_analyser import add_inventory_entry

        return add_inventory_entry(body.proposed, approved_by=caller.actor)

    from CortexOS.api.chat_routes import register_chat_routes
    from CortexOS.api.warehouse_routes import register_warehouse_routes

    register_chat_routes(app)
    register_warehouse_routes(app)
