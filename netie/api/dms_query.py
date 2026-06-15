"""DMS Brain API routes (active when PACK=dms)."""

from typing import Any

from pydantic import BaseModel, Field


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
    audit: dict[str, Any] | None = None


def register_dms_routes(app: Any) -> None:
    from fastapi import HTTPException

    @app.post("/dms/query", response_model=DMSQueryResponse)
    async def dms_query(body: DMSQueryRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from netie.dms.query_service import answer_question

        return answer_question(body.question, session_id=body.session_id)

    @app.get("/dms/audit")
    async def dms_audit() -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from netie.dms.query_service import list_audit_entries

        return {"entries": list_audit_entries()}

    @app.get("/dms/data/{variant}")
    async def dms_data(variant: str, limit: int = 50) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from pathlib import Path

        import csv

        root = Path(__file__).resolve().parents[2]
        fname = "warehouse_messy.csv" if variant == "messy" else "warehouse_clean.csv"
        path = root / "data" / "samples" / fname
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Dataset not found: {fname}")
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))[:limit]
        return {"variant": variant, "rows": rows, "count": len(rows)}

    @app.get("/dms/changelog")
    async def dms_changelog(limit: int = 100) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "data" / "samples" / "warehouse_changelog.jsonl"
        entries = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines()[:limit]:
                if line.strip():
                    entries.append(json.loads(line))
        return {"entries": entries}
