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


class AnalyseEntryRequest(BaseModel):
    raw_text: str


class AddEntryRequest(BaseModel):
    proposed: dict[str, Any]
    approved_by: str = "demo_steward"


class ProposeEditRequest(BaseModel):
    changes: list[dict[str, Any]]
    approved_by: str = "demo_steward"


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


def register_dms_routes(app: Any) -> None:
    from fastapi import HTTPException, Query

    @app.post("/dms/query", response_model=DMSQueryResponse)
    async def dms_query(body: DMSQueryRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.query_service import answer_question

        out = answer_question(body.question, session_id=body.session_id)
        # Mark when warehouse tables were synced from lake.silver (medallion path).
        if out.get("query_source") is None:
            out["query_source"] = os.environ.get(
                "DMS_QUERY_SOURCE_LABEL", "warehouse_synced_from_lake_silver"
            )
        return out

    @app.get("/dms/audit")
    async def dms_audit() -> list[dict[str, Any]]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            return []
        from CortexOS.dms.query_service import list_audit_entries

        return list_audit_entries()

    @app.get("/dms/tables")
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

    @app.get("/dms/table-preview")
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
    async def dms_propose_edit(body: ProposeEditRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.query_service import propose_edits

        return propose_edits(body.changes, approved_by=body.approved_by)

    @app.get("/dms/data/{variant}")
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

    @app.get("/dms/changelog")
    async def dms_changelog(limit: int = 100) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            return {"entries": []}
        return {"entries": _load_changelog(limit)}

    @app.post("/dms/analyse-entry")
    async def dms_analyse_entry(body: AnalyseEntryRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.entry_analyser import analyse_raw_entry

        return analyse_raw_entry(body.raw_text)

    @app.post("/dms/add-entry")
    async def dms_add_entry(body: AddEntryRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from CortexOS.dms.entry_analyser import add_inventory_entry

        return add_inventory_entry(body.proposed, approved_by=body.approved_by)

    # chat_routes and warehouse_routes are mounted by the app factory in app.py, not
    # chained from here. Chaining registered chat_routes twice, which put three
    # duplicate operation IDs into the generated OpenAPI spec - and the spec is the
    # artifact DMS consumes, so a duplicate there is a customer-facing defect.
