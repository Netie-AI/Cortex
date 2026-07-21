"""L1 — OpenDMS ingest API (upload → bronze, ledger, folder scan).

Uploads arrive base64-encoded in a JSON body (no python-multipart dependency),
are size-capped, written into a managed drop dir (never an arbitrary path), and
run through the exactly-once bronze loader. Steward RBAC on writes.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/ingest", tags=["ingest"])

ROOT = Path(__file__).resolve().parents[2]


def _drop_dir() -> Path:
    d = Path(os.environ.get("DMS_INGEST_DIR") or ROOT / "data" / "ingest_drop")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_bytes() -> int:
    return int(float(os.environ.get("DMS_INGEST_MAX_MB", "25")) * 1024 * 1024)


def _safe_name(filename: str) -> str:
    name = Path(filename).name  # strip any path components (traversal guard)
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-")
    if not name or "." not in name:
        raise HTTPException(status_code=400, detail="filename must have a name and extension")
    return name


class UploadRequest(BaseModel):
    filename: str
    content_b64: str


@router.post("/file")
def upload_file(req: UploadRequest, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    name = _safe_name(req.filename)
    try:
        data = base64.b64decode(req.content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid base64: {exc}") from exc
    if len(data) > _max_bytes():
        raise HTTPException(status_code=413, detail=f"file exceeds {_max_bytes()} bytes")

    dest = _drop_dir() / name
    dest.write_bytes(data)

    from packs.dms.ingest.loader import load_one

    result = load_one(dest, actor=caller.actor)
    if result.status == "unsupported":
        raise HTTPException(status_code=415, detail=result.error or "unsupported file type")
    return result.to_dict()


@router.post("/scan")
def scan_drop(caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.ingest.loader import ingest_folder

    results = ingest_folder(_drop_dir(), actor=caller.actor)
    return {"drop_dir": str(_drop_dir()), "results": [r.to_dict() for r in results]}


@router.get("/ledger")
def ingest_ledger(limit: int = 100, caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.ingest.loader import ledger_entries

    _ = caller
    return {"entries": ledger_entries(limit=min(max(limit, 1), 500))}


def register_ingest_routes(app) -> None:
    app.include_router(router)
