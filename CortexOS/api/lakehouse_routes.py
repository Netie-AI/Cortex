"""L0 — OpenDMS lakehouse API (catalog browse, snapshots, guarded query).

Reads go through the existing deterministic sqlglot guardrail (read-only,
allowlist extended to lake schemas). RBAC via the F7 api_auth dependency.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/lakehouse", tags=["lakehouse"])


class LakeQueryRequest(BaseModel):
    sql: str
    snapshot_id: Optional[int] = None


@router.get("/status")
def status(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.lakehouse.catalog import lakehouse_status

    _ = caller
    return lakehouse_status()


@router.get("/tables")
def tables(caller: Caller = Depends(require_role("viewer"))) -> dict[str, list[str]]:
    from packs.dms.lakehouse import tables as lt

    _ = caller
    return lt.list_tables()


@router.get("/tables/{schema}/{name}/snapshots")
def snapshots(
    schema: str, name: str, caller: Caller = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from packs.dms.lakehouse import tables as lt

    _ = caller
    try:
        snaps = lt.snapshots(schema, name)
    except lt.LakehouseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"schema": schema, "table": name, "snapshots": [s.to_dict() for s in snaps]}


@router.get("/tables/{schema}/{name}")
def preview(
    schema: str, name: str, limit: int = 100,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    from packs.dms.lakehouse import tables as lt

    _ = caller
    try:
        rows = lt.read(schema, name, limit=min(max(limit, 1), 500))
    except lt.LakehouseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"schema": schema, "table": name, "rows": rows, "row_count": len(rows)}


@router.post("/query")
def query(
    req: LakeQueryRequest, caller: Caller = Depends(require_role("viewer"))
) -> dict[str, Any]:
    """Read-only guarded query over the lakehouse. DDL/DML rejected by the guardrail."""
    from CortexOS.dms.sql_guardrail import validate_sql
    from CortexOS.dms.warehouse_db import load_semantic_layer
    from packs.dms.lakehouse.catalog import LAKE_ALIAS, SCHEMAS, connect

    # Extend the allowlist so lake.<schema>.<table> references validate. The
    # guardrail keys on bare table names, so we widen tables with the lake
    # table names present in the catalog.
    semantic = load_semantic_layer()
    from packs.dms.lakehouse import tables as lt

    lake_tables = lt.list_tables()
    widened = dict(semantic.get("tables") or {})
    con = connect(read_only=True)
    try:
        for schema in SCHEMAS:
            for tname in lake_tables.get(schema, []):
                cols = con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_catalog=? AND table_schema=? AND table_name=?",
                    [LAKE_ALIAS, schema, tname],
                ).fetchall()
                widened[tname] = {"columns": [c[0] for c in cols]}
        widened_semantic = dict(semantic)
        widened_semantic["tables"] = widened

        result = validate_sql(req.sql, widened_semantic)
        if not result.passed or not result.safe_sql:
            raise HTTPException(status_code=400, detail={"violations": result.violations})

        rel = con.execute(result.safe_sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        rows = [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        con.close()
    return {"sql_used": result.safe_sql, "rows": rows, "row_count": len(rows),
            "mode": lakehouse_status_mode()}


def lakehouse_status_mode() -> str:
    from packs.dms.lakehouse.catalog import lakehouse_mode

    return lakehouse_mode()


def register_lakehouse_routes(app) -> None:
    app.include_router(router)
